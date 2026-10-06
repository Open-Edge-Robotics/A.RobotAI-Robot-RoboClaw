"""헤드 카메라 파지 보조 (dual-camera grasp) 테스트.

그리퍼 카메라 단독 서보의 두 한계 — 근접 시 관측 소실, 좌우(err_x) 보정 수단 부재 —
를 헤드 카메라로 메우는 경로를 검증한다. 핵심은 **기존 동작이 그대로 보존되는지**
(회귀 고정)와 **헤드가 실패하면 기존 폴백으로 내려가는지**다.
"""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

import math
from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.manipulation_skill import (
    AdaptivePickObjectSkill,
    ServoGripperToObjectSkill,
)
from robo_claw_agent.skills.manipulation_skill.sequence import (
    adaptive_pick_skill,
    aruco_calib,
    ee_state,
    gripper_target_skills,
    head_assist,
    head_vision,
)


class DummyParameter:
    def __init__(self, value):
        self.value = value


class FakeBackend:
    def __init__(self):
        self.calls = []

    def move_to_named_pose(self, group_name, target_name):
        self.calls.append(("named", group_name, target_name))

    def move_to_joint_target(self, group_name, joint_values):
        self.calls.append(("joint", group_name, dict(joint_values)))

    def move_to_pose_target(self, group_name, pose, end_effector_link, cartesian=False):
        self.calls.append(("pose", group_name, pose.frame_id))


def _make_node(params=None, **extra):
    values = dict(params or {})
    node = SimpleNamespace(
        has_parameter=lambda name: name in values,
        get_parameter=lambda name: DummyParameter(values[name]),
    )
    for key, value in extra.items():
        setattr(node, key, value)
    return node


def _stretch_node(backend=None, extra_params=None):
    """헤드 보조가 활성화되는 Stretch 백엔드 노드."""
    params = {"manipulation_backend": "stretch"}
    params.update(extra_params or {})
    return _make_node(params, _manipulation_backend=backend or FakeBackend())


def _patch_servo(monkeypatch, observations, joints=None):
    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: observations.pop(0),
    )
    monkeypatch.setattr(
        gripper_target_skills,
        "_joint_positions",
        lambda skill, names: dict(joints or {"wrist_extension": 0.1, "joint_lift": 0.4}),
    )


def _patch_head(monkeypatch, head_results, joints=None):
    """헤드 관측/EE 위치/헤드 이동을 스텁으로 대체한다."""
    monkeypatch.setattr(
        head_assist,
        "_observe_head_target",
        lambda skill, params: head_results.pop(0) if head_results else {"success": False},
    )
    monkeypatch.setattr(
        head_assist,
        "_joint_positions",
        lambda skill, names: dict(joints or {"wrist_extension": 0.1, "joint_lift": 0.4}),
    )
    # 헤드 자세 이동은 실제 액션 호출 없이 성공한 것으로 처리하되,
    # ArUco 잔차 캐시 생성은 실제 경로를 그대로 탄다.
    def _fake_ensure_pose(self):
        if self.residual_cache is None:
            self.residual_cache = aruco_calib.make_residual_cache(self.skill, self.params)
        return {"moved": True, "pose_name": self.pose_name}

    monkeypatch.setattr(head_assist.HeadAssist, "ensure_pose", _fake_ensure_pose)


def _head_obs(x, y, z, *, success=True):
    return {
        "success": success,
        "message": "stub",
        "object_base_xyz": {"x": x, "y": y, "z": z},
    }


# ── 1. 회귀 고정: 헤드 보조 비활성 시 기존 동작 그대로 ────────────────────


def test_head_assist_disabled_keeps_blind_lift_recovery(monkeypatch):
    """use_head_assist=False 면 기존 '눈감고 lift 흔들기'가 그대로 동작해야 한다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.55,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": False, "message": "lost target"},
        {"success": True, "ready_to_grasp": True, "depth_m": 0.25,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "max_steps": 4, "use_head_assist": False}
    )

    assert result["success"] is True
    assert backend.calls == [
        ("joint", "arm", {"wrist_extension": pytest.approx(0.15)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.37)}),
    ]
    assert "head_assist" not in result


def test_head_assist_skipped_on_non_stretch_backend(monkeypatch):
    """moveit 등 비-Stretch 백엔드에서는 헤드 보조가 아예 켜지지 않는다."""
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)  # backend 기본값 = moveit
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.55,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": False, "message": "lost target"},
        {"success": True, "ready_to_grasp": True, "depth_m": 0.25,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 4})

    assert result["success"] is True
    assert "head_assist" not in result
    assert backend.calls == [
        ("joint", "arm", {"wrist_extension": pytest.approx(0.15)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.37)}),
    ]


# ── 2. 대상 소실 → 헤드 유도 ───────────────────────────────────────────────


def test_head_guides_arm_instead_of_blind_jog_when_gripper_loses_target(monkeypatch):
    """그리퍼가 대상을 놓쳤을 때 헤드 3D 오차 방향으로 유도해야 한다(눈감고 흔들기 X)."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.30,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": False, "message": "lost target"},
        {"success": True, "ready_to_grasp": True, "depth_m": 0.22,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)
    # grasp center 는 (0, -0.60, 0.70), 물체는 (0, -0.68, 0.74).
    # => 팔 축(-y) 방향으로 0.08m 더 멀고, 0.04m 더 높다.
    _patch_head(monkeypatch, [_head_obs(0.0, -0.68, 0.74)])
    monkeypatch.setattr(
        head_assist, "_grasp_center_base_xyz", lambda skill, **kw: (0.0, -0.60, 0.70)
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 4})

    assert result["success"] is True
    # 1번째: depth 0.30 -> extension +0.05 (기존 로직)
    # 2번째: 헤드 유도 — extension +0.05(스텝 상한 클램프), lift +0.04
    assert backend.calls[1][0:2] == ("joint", "arm")
    guided = backend.calls[1][2]
    assert guided["joint_lift"] == pytest.approx(0.44)  # 0.40 + 0.04
    assert guided["wrist_extension"] == pytest.approx(0.15)  # 0.10 + min(0.08, 0.05)
    assert result["head_assist"]["guided_steps"] == 1


def test_head_reports_ready_when_object_within_grasp_radius(monkeypatch):
    """헤드가 파지 반경 안이라고 판정하면 서보를 성공으로 끝내고 close 로 넘긴다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.30,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": False, "message": "lost target"},
    ]
    _patch_servo(monkeypatch, observations)
    _patch_head(monkeypatch, [_head_obs(0.0, -0.62, 0.70)])
    monkeypatch.setattr(
        head_assist, "_grasp_center_base_xyz", lambda skill, **kw: (0.0, -0.60, 0.70)
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 4})

    assert result["success"] is True
    assert result["ready_to_grasp"] is True
    assert result["ready_source"] == "head_camera"


def test_falls_back_to_blind_jog_when_head_also_fails(monkeypatch):
    """헤드도 대상을 못 보면 기존 눈감고 흔들기로 내려가야 한다(기능 후퇴 없음)."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.55,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": False, "message": "lost target"},
        {"success": True, "ready_to_grasp": True, "depth_m": 0.25,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)
    _patch_head(monkeypatch, [{"success": False, "message": "head lost too"}])

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 4})

    assert result["success"] is True
    assert backend.calls == [
        ("joint", "arm", {"wrist_extension": pytest.approx(0.15)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.37)}),  # 눈감고 흔들기 유지
    ]


def test_head_observation_exception_does_not_break_grasp(monkeypatch):
    """헤드 관측이 예외를 던져도 파지 자체는 죽지 않고 기존 경로로 폴백한다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.55,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": False, "message": "lost target"},
        {"success": True, "ready_to_grasp": True, "depth_m": 0.25,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)

    def _boom(skill, params):
        raise RuntimeError("camera topic exploded")

    monkeypatch.setattr(head_assist, "_observe_head_target", _boom)
    monkeypatch.setattr(
        head_assist.HeadAssist, "ensure_pose", lambda self: {"moved": True}
    )
    monkeypatch.setattr(
        head_assist,
        "_joint_positions",
        lambda skill, names: {"wrist_extension": 0.1, "joint_lift": 0.4},
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 4})

    assert result["success"] is True
    assert ("joint", "arm", {"joint_lift": pytest.approx(0.37)}) in backend.calls


def test_head_guided_steps_are_budgeted(monkeypatch):
    """헤드 유도가 무한 반복되지 않도록 max_head_guided_steps 로 막힌다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [{"success": False, "message": "lost"} for _ in range(8)]
    _patch_servo(monkeypatch, observations)
    _patch_head(monkeypatch, [_head_obs(0.0, -0.90, 0.70) for _ in range(8)])
    monkeypatch.setattr(
        head_assist, "_grasp_center_base_xyz", lambda skill, **kw: (0.0, -0.60, 0.70)
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "max_steps": 8, "max_head_guided_steps": 2}
    )

    assert result["success"] is False
    assert result["head_assist"]["guided_steps"] == 2


# ── 3. depth 소실 시 눈감고 집기가 마지막 수단으로 강등되었는지 ────────────


def test_depth_lost_uses_head_confirmation_before_blind_pick(monkeypatch):
    """depth 소실 시 헤드가 '아직 멀다'고 하면 눈감고 집지 않고 유도한다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        # 직전 관측: 눈감고 집기 조건(target_depth+2*tol=0.32 이내, y 정렬됨)을 충족한다.
        {"success": True, "ready_to_grasp": False, "depth_m": 0.30,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": True, "ready_to_grasp": False, "depth_m": None,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": True, "ready_to_grasp": True, "depth_m": 0.22,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)
    # 헤드 기준으로는 아직 0.20m 떨어져 있다 -> 눈감고 집기는 부적절.
    _patch_head(monkeypatch, [_head_obs(0.0, -0.80, 0.70)])
    monkeypatch.setattr(
        head_assist, "_grasp_center_base_xyz", lambda skill, **kw: (0.0, -0.60, 0.70)
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 4})

    assert result["success"] is True
    # 눈감고 집기(previous_gripper_observation)가 아니라 정상 그리퍼 관측으로 끝나야 한다.
    assert result.get("ready_source") != "previous_gripper_observation"
    assert result["head_assist"]["guided_steps"] == 1


def test_depth_lost_still_falls_back_to_blind_pick_when_head_unavailable(monkeypatch):
    """헤드를 못 쓰면 기존 '직전 관측 기준 집기'가 그대로 남아 있어야 한다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.30,
         "image_error_px": {"x": 0.0, "y": 0.0}},
        {"success": True, "ready_to_grasp": False, "depth_m": None,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)
    _patch_head(monkeypatch, [{"success": False, "message": "no head"}])

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 4})

    assert result["success"] is True
    assert result["ready_to_grasp"] is True
    assert result["ready_source"] == "previous_gripper_observation"


# ── 4. 좌우(err_x) 오차 — 기존에 보정 수단이 아예 없던 부분 ────────────────


def test_lateral_only_error_reports_actionable_message(monkeypatch):
    """err_x 만 남았을 때 '보정할 delta 없음'이 아니라 좌우 오차임을 알려야 한다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        # err_y/depth 는 허용 범위 -> 보정할 관절 델타가 없다. err_x 만 크다.
        {"success": True, "ready_to_grasp": False, "depth_m": 0.22,
         "image_error_px": {"x": 150.0, "y": 0.0}, "target_pose": None},
    ]
    _patch_servo(monkeypatch, observations)
    _patch_head(monkeypatch, [{"success": False, "message": "no head"}])
    monkeypatch.setattr(
        gripper_target_skills, "_map_point_to_base", lambda skill, meta: None
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 2})

    assert result["success"] is False
    assert "좌우" in result["message"]
    assert "err_x=150.0px" in result["message"]


def test_servo_does_not_rotate_base_by_default(monkeypatch):
    """서보 중 베이스 회전은 기본 비활성이어야 한다(뻗은 팔을 휘두르지 않도록)."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.22,
         "image_error_px": {"x": 150.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)
    _patch_head(monkeypatch, [_head_obs(0.10, -0.60, 0.70)])
    monkeypatch.setattr(
        head_assist, "_grasp_center_base_xyz", lambda skill, **kw: (0.0, -0.60, 0.70)
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 2})

    assert result["success"] is False
    assert not any(call[1] == "base" for call in backend.calls)
    assert "allow_servo_base_rotation" in result["message"]


def test_servo_rotates_base_when_explicitly_allowed(monkeypatch):
    """allow_servo_base_rotation=True 면 좌우 오차를 베이스 회전으로 보정한다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": True, "ready_to_grasp": False, "depth_m": 0.22,
         "image_error_px": {"x": 150.0, "y": 0.0}},
        {"success": True, "ready_to_grasp": True, "depth_m": 0.22,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)
    # 물체가 팔 작업축(-y)에서 살짝 +x 쪽으로 벗어나 있다.
    _patch_head(monkeypatch, [_head_obs(0.05, -0.60, 0.70)])
    monkeypatch.setattr(
        head_assist, "_grasp_center_base_xyz", lambda skill, **kw: (0.0, -0.60, 0.70)
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "max_steps": 3, "allow_servo_base_rotation": True}
    )

    assert result["success"] is True
    base_calls = [call for call in backend.calls if call[1] == "base"]
    assert len(base_calls) == 1
    assert "rotate_mobile_base" in base_calls[0][2]
    assert result["head_assist"]["base_rotations"] == 1


# ── 5. lateral_align 단위 동작 ────────────────────────────────────────────


class _RotationRecorder:
    def __init__(self):
        self.calls = []

    def move_to_joint_target(self, target, group_name=None):
        self.calls.append((group_name, dict(target)))


def _lateral_skill(monkeypatch, wrist_extension=0.10):
    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node({}, _robot_limits_dict=None))
    monkeypatch.setattr(
        head_assist,
        "_joint_positions",
        lambda s, names: {"wrist_extension": wrist_extension},
    )
    return skill


def test_lateral_align_ignores_error_inside_deadband(monkeypatch):
    skill = _lateral_skill(monkeypatch)
    runtime = _RotationRecorder()
    # 팔 작업축 방위(-91.81°) 위에 정확히 있는 점 -> 필요 회전각 ≈ 0.
    bearing = ee_state._arm_reach_bearing_rad()
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target)

    assert info["rotated"] is False
    assert runtime.calls == []


def test_lateral_align_rotates_for_moderate_error(monkeypatch):
    skill = _lateral_skill(monkeypatch)
    runtime = _RotationRecorder()
    bearing = ee_state._arm_reach_bearing_rad() + math.radians(6.0)
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target)

    assert info["rotated"] is True
    assert info["angle_deg"] == pytest.approx(6.0, abs=0.1)
    assert runtime.calls[0][0] == "base"
    assert runtime.calls[0][1]["rotate_mobile_base"] == pytest.approx(
        math.radians(6.0), abs=1e-3
    )


def test_lateral_align_rejects_implausibly_large_correction(monkeypatch):
    """상한을 넘는 각도는 오검출로 보고 회전하지 않는다."""
    skill = _lateral_skill(monkeypatch)
    runtime = _RotationRecorder()
    bearing = ee_state._arm_reach_bearing_rad() + math.radians(40.0)
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target)

    assert info["rotated"] is False
    assert "오검출" in info["reason"]
    assert runtime.calls == []


def test_lateral_align_refuses_rotation_with_extended_arm(monkeypatch):
    """팔이 뻗은 상태에서는 회전을 거부한다(_ROTATE_SAFE_WRIST_EXTENSION_M)."""
    skill = _lateral_skill(monkeypatch, wrist_extension=0.35)
    runtime = _RotationRecorder()
    bearing = ee_state._arm_reach_bearing_rad() + math.radians(6.0)
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target, allow_retract=False)

    assert info["rotated"] is False
    assert runtime.calls == []


def test_lateral_align_retracts_before_rotating_when_allowed(monkeypatch):
    """allow_retract=True 면 안전 길이까지 접고 → 회전 → 원래 길이로 복원한다."""
    skill = _lateral_skill(monkeypatch, wrist_extension=0.35)
    runtime = _RotationRecorder()
    bearing = ee_state._arm_reach_bearing_rad() + math.radians(6.0)
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target, allow_retract=True)

    assert info["rotated"] is True
    assert [call[0] for call in runtime.calls] == ["arm", "base", "arm"]
    assert runtime.calls[0][1]["wrist_extension"] == pytest.approx(0.15)
    assert runtime.calls[2][1]["wrist_extension"] == pytest.approx(0.35)


# ── 6. 오차 분해 (팔 작업축 기준) ─────────────────────────────────────────


def test_decompose_base_error_maps_axes_correctly():
    """FK 로 확인한 축 대응: -y = 팔 신장 방향, +x = 좌우, z = lift."""
    along, lateral, vertical = ee_state._decompose_base_error((0.0, -0.10, 0.05))
    assert along == pytest.approx(0.10, abs=1e-3)  # 더 뻗어야 함
    assert lateral == pytest.approx(0.0, abs=5e-3)
    assert vertical == pytest.approx(0.05)

    along, lateral, vertical = ee_state._decompose_base_error((0.10, 0.0, 0.0))
    assert along == pytest.approx(0.0, abs=5e-3)
    assert lateral == pytest.approx(0.10, abs=1e-3)  # 좌우 = 베이스 회전 필요


# ── 7. ArUco 차분 보정 ────────────────────────────────────────────────────


class _FakeTfBuffer:
    def __init__(self, frames):
        self.frames = frames

    def lookup_transform(self, target_frame, source_frame, time):
        if source_frame not in self.frames:
            raise RuntimeError(f"no transform for {source_frame}")
        x, y, z, stamp_ns = self.frames[source_frame]
        return SimpleNamespace(
            header=SimpleNamespace(
                stamp=SimpleNamespace(
                    sec=stamp_ns // 1_000_000_000, nanosec=stamp_ns % 1_000_000_000
                )
            ),
            transform=SimpleNamespace(
                translation=SimpleNamespace(x=x, y=y, z=z)
            ),
        )


def _aruco_skill(frames, now_ns=10_000_000_000, params=None):
    node = _make_node(
        params or {},
        _tf_buffer=_FakeTfBuffer(frames),
        get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=now_ns)),
    )
    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    return skill


def test_marker_residual_averages_wrist_markers():
    """두 손목 마커의 잔차를 평균낸다 (URDF - 측정)."""
    fresh = 9_500_000_000  # 0.5s 전
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, fresh),
            "link_aruco_inner_wrist": (0.02, -0.60, 0.70, fresh),
            "wrist_top": (0.0, -0.62, 0.72, fresh),
            "link_aruco_top_wrist": (0.02, -0.62, 0.72, fresh),
        }
    )

    result = aruco_calib._marker_tf_residual(skill, {})

    assert result is not None
    residual, diagnostics = result
    assert residual == pytest.approx((0.02, 0.0, 0.0))
    assert set(diagnostics["pairs_used"]) == {"wrist_inside", "wrist_top"}


def test_marker_residual_rejects_stale_marker_tf():
    """마커 TF 가 오래됐으면(지금 안 보임) 보정하지 않는다."""
    stale = 1_000_000_000  # 9s 전
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, stale),
            "link_aruco_inner_wrist": (0.02, -0.60, 0.70, stale),
        }
    )

    assert aruco_calib._marker_tf_residual(skill, {}) is None


def test_marker_residual_rejects_oversized_residual():
    """잔차가 상한을 넘으면 마커 오검출로 보고 버린다."""
    fresh = 9_500_000_000
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, fresh),
            "link_aruco_inner_wrist": (0.50, -0.60, 0.70, fresh),
        }
    )

    assert aruco_calib._marker_tf_residual(skill, {}) is None


def test_marker_residual_rejects_disagreeing_pairs():
    """두 마커가 서로 다른 답을 내면 신뢰하지 않는다."""
    fresh = 9_500_000_000
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, fresh),
            "link_aruco_inner_wrist": (0.02, -0.60, 0.70, fresh),
            "wrist_top": (0.0, -0.62, 0.72, fresh),
            "link_aruco_top_wrist": (-0.08, -0.62, 0.72, fresh),
        }
    )

    assert aruco_calib._marker_tf_residual(skill, {}) is None


def test_marker_residual_none_when_aruco_node_not_running():
    """aruco 노드 미실행(마커 TF 없음) -> None. 보정 없이 그대로 진행한다."""
    fresh = 9_500_000_000
    skill = _aruco_skill({"link_aruco_inner_wrist": (0.02, -0.60, 0.70, fresh)})

    assert aruco_calib._marker_tf_residual(skill, {}) is None


def test_marker_residual_none_without_tf_buffer():
    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node({}))

    assert aruco_calib._marker_tf_residual(skill, {}) is None


def test_residual_cache_latches_and_applies_offset():
    """마커가 안 보이게 되어도 래치된 잔차가 유지된다."""
    cache = aruco_calib.MarkerResidualCache()
    assert cache.residual is None
    assert cache.apply((1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)

    fresh = 9_500_000_000
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, fresh),
            "link_aruco_inner_wrist": (0.02, -0.60, 0.70, fresh),
        }
    )
    assert cache.refresh(skill, {}) is True
    assert cache.apply((1.0, 2.0, 3.0)) == pytest.approx((1.02, 2.0, 3.0))

    # 마커가 사라져도 래치된 값은 유지된다.
    empty_skill = _aruco_skill({})
    assert cache.refresh(empty_skill, {}) is False
    assert cache.apply((1.0, 2.0, 3.0)) == pytest.approx((1.02, 2.0, 3.0))


def test_marker_frame_pairs_configurable_via_json():
    pairs = aruco_calib._marker_frame_pairs(
        {"aruco_marker_frame_pairs_json": '[["m_a","u_a"],["m_b","u_b"]]'}
    )
    assert pairs == [("m_a", "u_a"), ("m_b", "u_b")]

    # 깨진 JSON 은 기본값으로 안전하게 폴백.
    assert aruco_calib._marker_frame_pairs(
        {"aruco_marker_frame_pairs_json": "{not json"}
    ) == list(aruco_calib._DEFAULT_MARKER_FRAME_PAIRS)


def _run_servo_with_residual(monkeypatch, residual):
    """헤드가 물체와 grasp center 를 같은 지점으로 보는 상황에서 잔차 효과만 관찰한다."""
    backend = FakeBackend()
    node = _stretch_node(backend)
    observations = [
        {"success": False, "message": "lost target"},
        {"success": True, "ready_to_grasp": True, "depth_m": 0.22,
         "image_error_px": {"x": 0.0, "y": 0.0}},
    ]
    _patch_servo(monkeypatch, observations)
    _patch_head(monkeypatch, [_head_obs(0.0, -0.60, 0.70)])
    monkeypatch.setattr(
        head_assist, "_grasp_center_base_xyz", lambda skill, **kw: (0.0, -0.60, 0.70)
    )
    cache = aruco_calib.MarkerResidualCache()
    if residual is not None:
        cache._residual = residual
        cache._latched_at = float("inf")
    monkeypatch.setattr(
        aruco_calib, "make_residual_cache", lambda skill, params, refresh=True: cache
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "max_steps": 3})
    return result, backend


def test_without_aruco_residual_head_reports_ready(monkeypatch):
    """잔차가 없으면 오차 0 -> 파지 반경 이내이므로 관절 명령 없이 ready."""
    result, backend = _run_servo_with_residual(monkeypatch, None)

    assert result["ready_source"] == "head_camera"
    assert backend.calls == []


def test_aruco_residual_shifts_head_guidance(monkeypatch):
    """잔차가 헤드 3D 관측에 실제로 더해져 유도 명령을 바꾸는지 확인한다."""
    # 헤드가 물체를 실제보다 0.10m 낮게 본다고 보정 -> 파지 반경(0.05) 밖이 되어
    # lift 를 올려야 한다. 보정이 반영되지 않으면 위 테스트처럼 명령이 없어야 한다.
    result, backend = _run_servo_with_residual(monkeypatch, (0.0, 0.0, 0.10))

    lift_calls = [c for c in backend.calls if "joint_lift" in c[2]]
    assert lift_calls, "ArUco 잔차가 헤드 유도에 반영되지 않았습니다."
    # lift_delta = clamp(0.10, ±max_lift_step_m 0.06) = 0.06
    assert lift_calls[0][2]["joint_lift"] == pytest.approx(0.46)
    assert result["head_assist"]["guided_steps"] == 1


# ── 8. 헤드 관측 헬퍼 ─────────────────────────────────────────────────────


def test_head_camera_params_force_head_routing():
    """그리퍼용 params 가 섞여 들어와도 헤드 depth 토픽으로 라우팅되어야 한다."""
    routed = head_vision._head_camera_params(
        {
            "camera": "gripper",
            "camera_source": "wrist",
            "depth_topic": "/gripper_camera/aligned_depth_to_color/image_raw",
            "camera_info_topic": "/gripper_camera/aligned_depth_to_color/camera_info",
            "target_object": "cup",
        }
    )

    assert "camera" not in routed
    assert "camera_source" not in routed
    assert "depth_topic" not in routed
    assert "camera_info_topic" not in routed
    assert routed["target_object"] == "cup"


def test_head_camera_params_honor_explicit_head_overrides():
    routed = head_vision._head_camera_params(
        {"head_depth_topic": "/head/depth", "head_camera_info_topic": "/head/info"}
    )
    assert routed["depth_topic"] == "/head/depth"
    assert routed["camera_info_topic"] == "/head/info"


def test_object_base_xyz_extracts_tuple():
    assert head_vision._object_base_xyz(_head_obs(1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)
    assert head_vision._object_base_xyz({"success": False}) is None
    assert head_vision._object_base_xyz({"success": True}) is None


# ── 9. grasp center 조회 (TF 우선, FK 폴백) ───────────────────────────────


def test_grasp_center_prefers_tf():
    fresh = 9_500_000_000
    skill = _aruco_skill({"link_grasp_center": (0.01, -0.67, 0.71, fresh)})

    assert ee_state._grasp_center_base_xyz(skill) == pytest.approx((0.01, -0.67, 0.71))


def test_grasp_center_falls_back_to_fk(monkeypatch):
    """TF 가 없으면 joint_states + FK 로 계산한다."""
    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node({}))
    monkeypatch.setattr(
        ee_state,
        "_joint_positions",
        lambda s, names: {
            "joint_lift": 0.6,
            "wrist_extension": 0.26,
            "joint_wrist_yaw": 0.0,
        },
    )

    xyz = ee_state._grasp_center_base_xyz(skill)

    # FK 검증값: lift=0.6, ext=0.26 -> (-0.0213, -0.6749, 0.7097)
    assert xyz == pytest.approx((-0.0213, -0.6749, 0.7097), abs=1e-3)


def test_grasp_center_returns_none_without_tf_or_joints(monkeypatch):
    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node({}))
    monkeypatch.setattr(ee_state, "_joint_positions", lambda s, names: None)

    assert ee_state._grasp_center_base_xyz(skill) is None


# ── 10. adaptive_pick_object 배선 ─────────────────────────────────────────


def _patch_adaptive_pick(monkeypatch, calls, *, head_pose_succeeds=True):
    """adaptive_pick_object 의 하위 스킬을 전부 스텁으로 대체하고 호출 순서를 기록한다."""
    from robo_claw_agent.skills.manipulation_skill import HeadPanTiltSkill, sequence

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: calls.append(("search", None))
        or {"success": True, "align_angle_deg": None, "body_rotation_applied_deg": 0.0},
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append(("prepare", None)) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append(("observe_gripper", None)) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append(("pick", params.get("head_assist_ready")))
        or {"success": True},
    )
    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: calls.append(("head", params.get("pose_name")))
        or {"success": head_pose_succeeds, "message": "" if head_pose_succeeds else "head fail"},
    )
    monkeypatch.setattr(
        head_assist.HeadAssist,
        "observe",
        lambda self: (calls.append(("observe_head", None)) or (None, {"success": False})),
    )


def test_adaptive_pick_places_head_between_prepare_and_gripper_observe(monkeypatch):
    """헤드는 준비자세 직후, 그리퍼 확인 직전에 파지 감시 자세로 이동해야 한다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    stages = [name for name, _ in calls]
    assert stages.index("prepare") < stages.index("head") < stages.index("observe_gripper")
    assert ("head", "gripper_side_head") in calls
    # 주행 안전을 위한 travel_head 복귀는 그대로 유지되어야 한다.
    assert calls[-1] == ("head", "travel_head")


def test_adaptive_pick_marks_head_ready_for_servo(monkeypatch):
    """서보가 헤드를 다시 움직이지 않도록 head_assist_ready 를 내려보낸다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    skill.execute({"target_object": "cup"})

    pick_calls = [value for name, value in calls if name == "pick"]
    assert pick_calls == [True]


def test_adaptive_pick_continues_when_head_move_fails(monkeypatch):
    """헤드 이동 실패는 치명적이지 않다 — 파지 시퀀스가 계속 진행되어야 한다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls, head_pose_succeeds=False)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert "observe_gripper" in [name for name, _ in calls]


def test_adaptive_pick_skips_head_assist_when_disabled(monkeypatch):
    """use_head_assist=False 면 gripper_side_head 이동/헤드 관측이 없어야 한다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    result = skill.execute({"target_object": "cup", "use_head_assist": False})

    assert result["success"] is True
    assert ("head", "gripper_side_head") not in calls
    assert "observe_head" not in [name for name, _ in calls]
    # travel_head 복귀는 여전히 수행된다.
    assert calls[-1] == ("head", "travel_head")


def test_adaptive_pick_lateral_aligns_while_arm_is_retracted(monkeypatch):
    """헤드가 물체를 보면 팔이 접힌 지금(ready pose) 좌우 정렬을 시도해야 한다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls)
    monkeypatch.setattr(
        head_assist.HeadAssist,
        "observe",
        lambda self: (calls.append(("observe_head", None)) or ((0.05, -0.60, 0.70), {"success": True})),
    )
    aligned = {}

    def _fake_align(skill, runtime, params, obj_xyz, allow_retract=False):
        aligned["obj"] = obj_xyz
        aligned["allow_retract"] = allow_retract
        return {"rotated": True, "angle_deg": 4.0}

    monkeypatch.setattr(adaptive_pick_skill, "lateral_align", _fake_align)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert aligned["obj"] == (0.05, -0.60, 0.70)
    # 팔이 접혀 있으므로 retract 없이 바로 회전 가능해야 한다.
    assert aligned["allow_retract"] is False
    stages = [name for name, _ in calls]
    assert stages.index("observe_head") < stages.index("observe_gripper")
