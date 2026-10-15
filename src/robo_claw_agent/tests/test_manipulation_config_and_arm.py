"""많이 쓰는 설정/자세/그리퍼 preset 스킬 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    ArmPoseSkill,
    CloseGripperSkill,
    FakeBackend,
    GraspSkill,
    ManipulationError,
    MoveJointsSkill,
    MovePoseSkill,
    OpenGripperSkill,
    PlaceSkill,
    _make_node,
    _stub_head_observation,
    _stub_joint_positions,
    load_manipulation_config,
    pytest,
)


def test_load_manipulation_config_merges_defaults_and_json():
    node = _make_node(
        {
            "manipulation_arm_group": "manipulator",
            "manipulation_end_effector_link": "tool0",
            "manipulation_named_poses_json": '{"inspect":"inspect_pose"}',
            "manipulation_gripper_presets_json": '{"open":{"finger_joint":0.04}}',
            "manipulation_default_approach_distance_m": 0.15,
        }
    )

    config = load_manipulation_config(node)

    assert config.arm_group == "manipulator"
    assert config.end_effector_link == "tool0"
    assert config.named_poses["home"] == "home"
    assert config.named_poses["inspect"] == "inspect_pose"
    assert config.gripper_presets["open"]["finger_joint"] == pytest.approx(0.04)
    assert config.default_approach_distance_m == pytest.approx(0.15)


def test_arm_pose_skill_executes_named_pose():
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_named_poses_json": '{"inspect":"inspection_pose"}',
        },
        _manipulation_backend=backend,
    )
    skill = ArmPoseSkill()
    skill.set_node(node)

    result = skill.execute({"pose_name": "inspect"})

    assert result["success"] is True
    assert result["moveit_target"] == "inspection_pose"
    assert backend.calls == [("named", "arm", "inspection_pose")]


def test_move_joints_skill_validates_joint_map():
    skill = MoveJointsSkill()
    assert skill.validate_params({"joints": {"joint1": 0.2}}) is True
    assert skill.validate_params({"joints": {}}) is False
    assert skill.validate_params({}) is False


def test_move_pose_skill_requires_end_effector_config():
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    skill = MovePoseSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.3, "y": 0.1, "z": 0.4})

    assert result["success"] is False
    assert "end_effector_link" in result["message"]


def test_open_and_close_gripper_use_presets():
    backend = FakeBackend()
    params = {
        "manipulation_gripper_presets_json": (
            '{"open":{"finger_joint":0.04},"close":{"finger_joint":0.0}}'
        ),
        "manipulation_gripper_group": "hand",
    }
    node = _make_node(params, _manipulation_backend=backend)

    open_skill = OpenGripperSkill()
    open_skill.set_node(node)
    close_skill = CloseGripperSkill()
    close_skill.set_node(node)

    open_result = open_skill.execute({})
    close_result = close_skill.execute({})

    assert open_result["success"] is True
    assert close_result["success"] is True
    assert backend.calls == [
        ("joint", "hand", {"finger_joint": 0.04}),
        ("joint", "hand", {"finger_joint": 0.0}),
    ]


def test_grasp_skill_runs_generic_sequence():
    backend = FakeBackend()
    params = {
        "manipulation_end_effector_link": "tool0",
        "manipulation_gripper_group": "hand",
        "manipulation_gripper_presets_json": (
            '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
        ),
    }
    node = _make_node(params, _manipulation_backend=backend)
    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.5, "y": 0.1, "z": 0.2})

    assert result["success"] is True
    assert result["target_pose"]["position"]["x"] == pytest.approx(0.5)
    assert backend.calls == [
        ("joint", "hand", {"gripper_aperture": 0.16}),
        (
            "pose",
            "arm",
            "base_link",
            {"x": 0.5, "y": 0.1, "z": 0.30000000000000004},
            {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "tool0",
            False,
        ),
        (
            "pose",
            "arm",
            "base_link",
            {"x": 0.5, "y": 0.1, "z": 0.2},
            {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "tool0",
            False,
        ),
        ("joint", "hand", {"gripper_aperture": 0.16}),
        ("joint", "hand", {"gripper_aperture": 0.0}),
        (
            "pose",
            "arm",
            "base_link",
            {"x": 0.5, "y": 0.1, "z": 0.30000000000000004},
            {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            "tool0",
            False,
        ),
    ]


def test_grasp_failure_attempts_arm_stow_recovery():
    class FailingBackend(FakeBackend):
        def move_to_pose_target(self, group_name, pose, end_effector_link, cartesian=False):
            super().move_to_pose_target(group_name, pose, end_effector_link, cartesian)
            if len([call for call in self.calls if call[0] == "pose"]) == 2:
                raise ManipulationError("target motion failed")

    backend = FailingBackend()
    node = _make_node(
        {
            "manipulation_end_effector_link": "tool0",
            "manipulation_gripper_presets_json": (
                '{"open":{"finger_joint":0.04},"close":{"finger_joint":0.0}}'
            ),
        },
        _manipulation_backend=backend,
    )
    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.5, "y": 0.1, "z": 0.2})

    assert result["success"] is False
    assert result["failed_stage"] == "reach_target"
    assert result["partial_execution"] is True
    assert result["recovery_attempted"] is True
    assert result["recovery_success"] is True
    assert ("named", "arm", "stow") in backend.calls


def test_place_failure_reports_recovery_failure():
    class FailingBackend(FakeBackend):
        def move_to_named_pose(self, group_name, target_name):
            if target_name == "stow":
                raise ManipulationError("stow unavailable")
            super().move_to_named_pose(group_name, target_name)

        def move_to_pose_target(self, group_name, pose, end_effector_link, cartesian=False):
            super().move_to_pose_target(group_name, pose, end_effector_link, cartesian)
            if len([call for call in self.calls if call[0] == "pose"]) == 1:
                raise ManipulationError("approach failed")

    backend = FailingBackend()
    node = _make_node(
        {
            "manipulation_end_effector_link": "tool0",
            "manipulation_gripper_presets_json": '{"open":{"finger_joint":0.04}}',
        },
        _manipulation_backend=backend,
    )
    skill = PlaceSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.4, "y": -0.2, "z": 0.15})

    assert result["success"] is False
    assert result["failed_stage"] == "approach"
    assert result["recovery_attempted"] is True
    assert result["recovery_success"] is False
    assert result["requires_recovery"] is True
