"""저수준 매니퓰레이션 스킬 실패 보고 형식 회귀 테스트.

`grasp`/`place` 는 실패 시 `failed_stage`/`partial_execution` 을 보고하지만
`arm_pose`/`move_joints`/`move_pose` 는 `success=false` 와 메시지만 반환했습니다.
상위 계층(플래너·대시보드)이 "어디까지 진행됐고 무엇이 실패했는지" 를 구분할 수
있도록 보고 형식을 맞춥니다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.manipulation_skill import (
    ArmPoseSkill,
    MoveJointsSkill,
    MovePoseSkill,
)

pytestmark = pytest.mark.unit


class _FailingBackend:
    """모든 이동 요청에서 실패하는 백엔드."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def move_to_named_pose(self, group_name, target_name):
        self.calls.append(("named", target_name))
        raise RuntimeError("드라이버가 목표를 거부했습니다.")

    def move_to_joint_target(self, group_name, joint_values):
        self.calls.append(("joint", dict(joint_values)))
        raise RuntimeError("조인트 이동 실패")

    def move_to_pose_target(self, group_name, pose, end_effector_link, cartesian=False):
        self.calls.append(("pose", cartesian))
        raise RuntimeError("pose 이동 실패")


class _DummyParameter:
    def __init__(self, value) -> None:
        self.value = value


def _make_node(backend) -> object:
    values = {
        "manipulation_backend": "moveit",
        "manipulation_end_effector_link": "tool0",
        "manipulation_named_poses_json": '{"home":{"joint1":0.0}}',
    }
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
        _robot_limits_dict={"manipulation": {"joint_limits": {"joint1": [-3.2, 3.2]}}},
        _manipulation_backend=backend,
    )
    return node


def _skill(skill_cls, backend):
    skill = skill_cls()
    skill.set_node(_make_node(backend))
    return skill


def test_arm_pose_failure_reports_stage_and_no_partial_execution():
    backend = _FailingBackend()
    result = _skill(ArmPoseSkill, backend).execute({"pose_name": "home"})

    assert result["success"] is False
    assert result["failed_stage"] == "arm_pose"
    assert result["partial_execution"] is False
    assert result["executed_steps"] == []
    assert backend.calls == [("named", "home")]


def test_move_joints_failure_reports_stage_and_empty_steps():
    backend = _FailingBackend()
    result = _skill(MoveJointsSkill, backend).execute({"joints": {"joint1": 0.1}})

    assert result["success"] is False
    assert result["failed_stage"] == "move_joints"
    # 단일 단계 스킬이므로 "완료된 단계" 는 없다(grasp/place 와 같은 정의).
    assert result["partial_execution"] is False
    assert result["executed_steps"] == []
    assert backend.calls == [("joint", {"joint1": 0.1})]


def test_move_pose_failure_reports_stage():
    backend = _FailingBackend()
    result = _skill(MovePoseSkill, backend).execute(
        {"x": 0.3, "y": 0.0, "z": 0.4, "frame_id": "base_link"}
    )

    assert result["success"] is False
    assert result["failed_stage"] == "move_pose"
    assert result["partial_execution"] is False
    assert result["executed_steps"] == []
