"""head assist servo guidance"""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _head_assist_helpers import (  # noqa: F401
    FakeBackend,
    ServoGripperToObjectSkill,
    _head_obs,
    _lateral_skill,
    _make_node,
    _patch_head,
    _patch_servo,
    _RotationRecorder,
    _stretch_node,
    _stub_joint_positions,
    ee_state,
    gripper_target_skills,
    head_assist,
    math,
    pytest,
)


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
    """ServoGripperToObjectSkill 은 Stretch 전용이라 비-Stretch 에서는 헤드 보조 이전에 거부된다."""
    backend = FakeBackend()
    node = _make_node({}, manipulation_backend="moveit", _manipulation_backend=backend)
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

    assert result["success"] is False
    assert "Stretch" in result["message"]
    assert "head_assist" not in result
    assert backend.calls == []
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
def test_lateral_align_ignores_error_inside_deadband(monkeypatch):
    skill = _lateral_skill(monkeypatch)
    runtime = _RotationRecorder()
    # 팔 작업축 방위(-91.81°) 위에 정확히 있는 점 -> 필요 회전각 ≈ 0.
    bearing = ee_state._arm_reach_bearing_rad()
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target)

    assert info["rotated"] is False
    assert runtime.calls == []
