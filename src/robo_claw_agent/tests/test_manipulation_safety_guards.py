"""매니퓰레이션 안전 가드(E-stop 우선, 조인트 한계 fail-closed) 회귀 테스트.

AGENTS.md §8 은 emergency stop 이 모든 실행 경로보다 우선하고, joint limit
검증이 모든 명령 경로에 적용되어야 한다고 규정한다. 이 파일은 그 두 규칙이
스킬 계층과 런타임 계층에서 실제로 강제되는지 검증한다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from robo_claw_agent.manipulation_runtime import (
    ManipulationEmergencyStopError,
    MoveItManipulationRuntime,
    PoseGoal,
)
from robo_claw_agent.skills.manipulation_skill import (
    ArmPoseSkill,
    CloseGripperSkill,
    MoveJointsSkill,
    MovePoseSkill,
    OpenGripperSkill,
)

pytestmark = pytest.mark.unit


class RecordingBackend:
    """명령이 백엔드까지 도달했는지 기록하는 fake backend."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def move_to_named_pose(self, group_name, target_name):
        self.calls.append(("named", group_name, target_name))

    def move_to_joint_target(self, group_name, joint_values):
        self.calls.append(("joint", group_name, dict(joint_values)))

    def move_to_pose_target(self, group_name, pose, end_effector_link, cartesian=False):
        self.calls.append(("pose", group_name, pose.frame_id, cartesian))


class _DummyParameter:
    def __init__(self, value) -> None:
        self.value = value


def _make_node(params: dict | None = None, **extra):
    values = dict(params or {})
    values.setdefault("manipulation_backend", "stretch")
    node = SimpleNamespace(
        has_parameter=lambda name: name in values,
        get_parameter=lambda name: _DummyParameter(values[name]),
        create_subscription=lambda *a, **k: SimpleNamespace(destroy=lambda: None),
        destroy_subscription=lambda *a, **k: None,
        default_callback_group=SimpleNamespace(
            add_entity=lambda *a, **k: None, remove_entity=lambda *a, **k: None
        ),
        add_entity=lambda *a, **k: None,
        remove_entity=lambda *a, **k: None,
    )
    for key, value in extra.items():
        setattr(node, key, value)
    return node


def _limits(*names: str) -> dict:
    return {"manipulation": {"joint_limits": {name: [-3.2, 3.2] for name in names}}}


_ALL_JOINTS = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "joint7", "gripper")


def _latched_node(backend, **extra):
    return _make_node(
        {
            "manipulation_end_effector_link": "tool0",
            "manipulation_gripper_presets_json": '{"open":{"gripper":0.5},"close":{"gripper":0.0}}',
            "manipulation_named_poses_json": '{"home":{"joint1":0.0}}',
        },
        _manipulation_backend=backend,
        _emergency_stop_latched=True,
        _emergency_stopped=True,
        _robot_limits_dict=_limits(*_ALL_JOINTS),
        **extra,
    )


def _skill(skill_cls, node):
    skill = skill_cls()
    skill.set_node(node)
    return skill


# ---------------------------------------------------------------------------
# E-stop 우선권
# ---------------------------------------------------------------------------


def test_arm_pose_is_refused_while_emergency_stop_latched():
    backend = RecordingBackend()
    skill = _skill(ArmPoseSkill, _latched_node(backend))

    result = skill.execute({"pose_name": "home"})

    assert result["success"] is False
    assert result["stopped_by"] == "emergency_stop"
    assert result["failure_reason"] == "emergency_stop_active"
    assert backend.calls == []


def test_move_joints_is_refused_while_emergency_stop_latched():
    backend = RecordingBackend()
    skill = _skill(MoveJointsSkill, _latched_node(backend))

    result = skill.execute({"joints": {"joint1": 0.1}})

    assert result["success"] is False
    assert result["stopped_by"] == "emergency_stop"
    assert backend.calls == []


def test_move_pose_is_refused_while_emergency_stop_latched():
    backend = RecordingBackend()
    skill = _skill(MovePoseSkill, _latched_node(backend))

    result = skill.execute({"x": 0.3, "y": 0.0, "z": 0.4, "frame_id": "base_link"})

    assert result["success"] is False
    assert result["stopped_by"] == "emergency_stop"
    assert backend.calls == []


@pytest.mark.parametrize(
    ("skill_cls", "params"),
    [
        (OpenGripperSkill, {"preset_name": "open"}),
        (CloseGripperSkill, {"preset_name": "close"}),
    ],
)
def test_gripper_preset_is_refused_while_emergency_stop_latched(skill_cls, params):
    backend = RecordingBackend()
    skill = _skill(skill_cls, _latched_node(backend))

    result = skill.execute(params)

    assert result["success"] is False
    assert result["stopped_by"] == "emergency_stop"
    assert backend.calls == []


def test_is_emergency_stopped_callable_is_honored():
    backend = RecordingBackend()
    node = _make_node(
        _manipulation_backend=backend,
        _robot_limits_dict=_limits(*_ALL_JOINTS),
        is_emergency_stopped=lambda: True,
    )
    skill = _skill(ArmPoseSkill, node)

    result = skill.execute({"pose_name": "home"})

    assert result["success"] is False
    assert result["stopped_by"] == "emergency_stop"
    assert backend.calls == []


def test_latch_clear_allows_motion_again():
    backend = RecordingBackend()
    node = _latched_node(backend)
    skill = _skill(ArmPoseSkill, node)

    assert skill.execute({"pose_name": "home"})["success"] is False

    node._emergency_stop_latched = False
    node._emergency_stopped = False

    assert skill.execute({"pose_name": "home"})["success"] is True
    assert backend.calls == [("named", "arm", "home")]


def test_runtime_raises_estop_error_before_backend_dispatch():
    backend = RecordingBackend()
    runtime = MoveItManipulationRuntime(_latched_node(backend), backend=backend)

    with pytest.raises(ManipulationEmergencyStopError) as excinfo:
        runtime.move_to_pose_target(
            PoseGoal(
                frame_id="base_link",
                position={"x": 0.3, "y": 0.0, "z": 0.4},
                orientation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            )
        )

    assert excinfo.value.stopped_by == "emergency_stop"
    assert backend.calls == []


# ---------------------------------------------------------------------------
# 조인트 한계 fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limits", [None, {}])
def test_joint_target_is_refused_when_robot_limits_are_missing(limits):
    backend = RecordingBackend()
    node = _make_node(_manipulation_backend=backend, _robot_limits_dict=limits)
    skill = _skill(MoveJointsSkill, node)

    result = skill.execute({"joints": {"joint1": 0.1}})

    assert result["success"] is False
    assert result["failure_reason"] == "robot_limits_missing"
    assert backend.calls == []


def test_named_pose_is_refused_when_robot_limits_are_missing():
    backend = RecordingBackend()
    node = _make_node(
        {
            "manipulation_named_poses_json": '{"inspect":{"joint1":0.2}}',
        },
        _manipulation_backend=backend,
        _robot_limits_dict=None,
    )
    skill = _skill(ArmPoseSkill, node)

    result = skill.execute({"pose_name": "inspect"})

    assert result["success"] is False
    assert result["failure_reason"] == "robot_limits_missing"
    assert backend.calls == []


def test_out_of_range_joint_target_is_still_rejected():
    backend = RecordingBackend()
    node = _make_node(
        _manipulation_backend=backend,
        _robot_limits_dict=_limits("joint1"),
    )
    skill = _skill(MoveJointsSkill, node)

    result = skill.execute({"joints": {"joint1": 9.9}})

    assert result["success"] is False
    assert backend.calls == []
