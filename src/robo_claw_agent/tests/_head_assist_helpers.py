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

# 공용 하네스(manipulation 계열 테스트와 동일 구현).
from _manip_helpers import DummyParameter, _make_node  # noqa: F401
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


class FakeBackend:
    def __init__(self):
        self.calls = []

    def move_to_named_pose(self, group_name, target_name):
        self.calls.append(("named", group_name, target_name))

    def move_to_joint_target(self, group_name, joint_values):
        self.calls.append(("joint", group_name, dict(joint_values)))

    def move_to_pose_target(self, group_name, pose, end_effector_link, cartesian=False):
        self.calls.append(("pose", group_name, pose.frame_id))
@pytest.fixture(autouse=True)
def _stub_joint_positions(monkeypatch):
    """실제 joint_states 대기를 피하기 위해 joint 조회를 기본값으로 고정한다.

    개별 테스트가 특정 값을 필요로 하면 monkeypatch 로 덮어쓴다(fixture 가 먼저 실행됨).
    """
    from robo_claw_agent.skills.manipulation_skill.sequence import (
        ee_state,
        grasp_verification,
        gripper_target_skills,
        joint_state,
        rotation,
    )

    defaults = {
        "gripper_aperture": 0.03,
        "joint_lift": 0.5,
        "wrist_extension": 0.1,
        "joint_wrist_yaw": 0.0,
    }

    def _fake_positions(skill, names):
        return {name: defaults.get(name, 0.0) for name in names}

    for module in (joint_state, grasp_verification, gripper_target_skills, ee_state, rotation):
        monkeypatch.setattr(module, "_joint_positions", _fake_positions)
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


__all__ = [
    "AdaptivePickObjectSkill",
    "DummyParameter",
    "FakeBackend",
    "ServoGripperToObjectSkill",
    "SimpleNamespace",
    "_FakeTfBuffer",
    "_RotationRecorder",
    "_aruco_skill",
    "_head_obs",
    "_lateral_skill",
    "_make_node",
    "_patch_adaptive_pick",
    "_patch_head",
    "_patch_servo",
    "_run_servo_with_residual",
    "_stretch_node",
    "_stub_joint_positions",
    "adaptive_pick_skill",
    "aruco_calib",
    "ee_state",
    "gripper_target_skills",
    "head_assist",
    "head_vision",
    "math",
    "pytest",
]
