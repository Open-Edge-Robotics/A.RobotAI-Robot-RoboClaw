import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

import math
from types import SimpleNamespace

import numpy as np
import pytest
from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
    MoveItManipulationRuntime,
    load_manipulation_config,
)
from robo_claw_agent.manipulation_runtime import stretch_backend as sb
from robo_claw_agent.skills.manipulation_skill import (
    AdaptivePickObjectSkill,
    ArmPoseSkill,
    ClassifyObjectSurfaceSkill,
    CloseGripperSkill,
    FixedPatternGraspSkill,
    GraspSkill,
    HeadPanTiltSkill,
    MoveJointsSkill,
    MovePoseSkill,
    ObserveGripperTargetSkill,
    OpenGripperSkill,
    PickFromRightSideZoneSkill,
    PickFrontObjectSkill,
    PlaceSkill,
    PrepareRightSidePickSkill,
    SearchObjectSkill,
    ServoGripperToObjectSkill,
    StowForNavigationSkill,
    StretchNavigationModeSkill,
    StretchPositionModeSkill,
    SwitchStretchModeSkill,
    VLABasedPickFrontObjectSkill,
    VLABasedPickGripperObjectSkill,
    sequence,
    stretch_mode,
)
from robo_claw_agent.skills.manipulation_skill.sequence import (
    adaptive_pick_skill,
    grasp_verification,
    gripper_target_skills,
    gripper_vision,
    rotation,
    search_skill,
)
from robo_claw_agent.skills.perception_skill import (
    EstimateGripperObjectPoseSkill,
    FindObjectSkill,
)
from robo_claw_agent.skills.perception_skill import core as perception_core


def test_adaptive_pick_approach_vector_moves_toward_distant_object():
    vector = adaptive_pick_skill._approach_vector_to_reach((0.0, -2.0, 0.1))

    assert vector == pytest.approx({"forward": 0.0, "lateral": -1.58})


def test_adaptive_pick_approach_vector_skips_already_reachable_object():
    assert adaptive_pick_skill._approach_vector_to_reach((0.0, -0.42, 0.1)) is None


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
        self.calls.append(
            (
                "pose",
                group_name,
                pose.frame_id,
                dict(pose.position),
                dict(pose.orientation),
                end_effector_link,
                cartesian,
            )
        )


class _FakeFuture:
    def __init__(self, response=None, done=True):
        self._response = response
        self._done = done
        self.cancelled = False

    def done(self):
        return self._done

    def result(self):
        return self._response

    def cancel(self):
        self.cancelled = True


class _FakeTriggerResponse:
    def __init__(self, success=True, message=""):
        self.success = success
        self.message = message


class _FakeJointState:
    def __init__(self, names, positions):
        self.name = names
        self.position = positions


class _FakeClient:
    def __init__(self, service_name, future, service_ready=True):
        self.service_name = service_name
        self.future = future
        self.service_ready = service_ready

    def wait_for_service(self, timeout_sec):
        return self.service_ready

    def call_async(self, request):
        return self.future


class _FakeStretchModeNode:
    def __init__(self, futures, service_ready=True):
        self.futures = list(futures)
        self.service_ready = service_ready
        self.clients = []
        self.destroyed = False

    def create_client(self, srv_type, service_name):
        future = self.futures.pop(0)
        client = _FakeClient(service_name, future, self.service_ready)
        self.clients.append(client)
        return client

    def destroy_node(self):
        self.destroyed = True


def _make_node(params=None, **extra):
    values = dict(params or {})
    node = SimpleNamespace(
        has_parameter=lambda name: name in values,
        get_parameter=lambda name: DummyParameter(values[name]),
    )
    for key, value in extra.items():
        setattr(node, key, value)
    return node


def _patch_stretch_mode_node(monkeypatch, future, service_ready=True):
    return _patch_stretch_mode_nodes(monkeypatch, [future], service_ready=service_ready)


def _patch_stretch_mode_nodes(monkeypatch, futures, service_ready=True):
    holder = {"nodes": []}

    def create_node(name):
        node = _FakeStretchModeNode([futures[len(holder["nodes"])]], service_ready=service_ready)
        holder["node"] = node
        holder["nodes"].append(node)
        holder["name"] = name
        return node

    monkeypatch.setattr(stretch_mode.rclpy, "create_node", create_node)
    monkeypatch.setattr(
        stretch_mode.rclpy,
        "spin_until_future_complete",
        lambda node, future, timeout_sec: None,
    )
    return holder


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
            '{"open":{"finger_joint":0.04},"close":{"finger_joint":0.0}}'
        ),
    }
    node = _make_node(params, _manipulation_backend=backend)
    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.5, "y": 0.1, "z": 0.2})

    assert result["success"] is True
    assert result["target_pose"]["position"]["x"] == pytest.approx(0.5)
    assert backend.calls == [
        ("joint", "hand", {"finger_joint": 0.04}),
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
        ("joint", "hand", {"finger_joint": 0.04}),
        ("joint", "hand", {"finger_joint": 0.04}),
        ("joint", "hand", {"finger_joint": 0.0}),
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


class FakeTFBuffer:
    """map -> base_link 변환을 흉내내는 테스트용 TF 버퍼.

    base_link 원점이 map 프레임 (1.0, 2.0, 0.0)에 있다고 가정하고 평행이동만 수행한다.
    실제 로봇이 물체 감지 이후 이동했을 때, 저장된 map 프레임 pose가 grasp 실행 시점의
    최신 TF로 다시 변환되는지(스냅샷이 아니라 '지금' 기준인지)를 검증하기 위한 목적.
    """

    def transform(self, pose_stamped, target_frame, timeout=None):
        del timeout
        assert pose_stamped.header.frame_id == "map"
        assert target_frame == "base_link"
        from geometry_msgs.msg import PoseStamped

        result = PoseStamped()
        result.header.frame_id = target_frame
        result.pose.position.x = pose_stamped.pose.position.x - 1.0
        result.pose.position.y = pose_stamped.pose.position.y - 2.0
        result.pose.position.z = pose_stamped.pose.position.z
        result.pose.orientation = pose_stamped.pose.orientation
        return result


def test_grasp_skill_resolves_pose_from_memory_metadata():
    backend = FakeBackend()
    params = {
        "manipulation_end_effector_link": "tool0",
        "manipulation_gripper_presets_json": (
            '{"open":{"finger_joint":0.04},"close":{"finger_joint":0.0}}'
        ),
    }
    memory = SimpleNamespace(
        get_object_location=lambda name: (
            {
                "metadata": {
                    "target_pose": {
                        "position": {"x": 0.7, "y": 0.0, "z": 0.3},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "frame_id": "map",
                    }
                }
            }
            if name == "cup"
            else None
        )
    )
    node = _make_node(
        params,
        _manipulation_backend=backend,
        _memory=memory,
        _tf_buffer=FakeTFBuffer(),
    )
    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"object_name": "cup"})

    assert result["success"] is True
    # 저장된 pose는 map 프레임이지만, grasp 실행 시점의 최신 TF로 base_link 프레임으로
    # 재변환되어야 한다 (감지 시점 스냅샷이 아니라 소비 시점 기준).
    assert result["target_pose"]["frame_id"] == "base_link"
    assert result["target_pose"]["position"]["x"] == pytest.approx(0.7 - 1.0)
    assert result["target_pose"]["position"]["y"] == pytest.approx(0.0 - 2.0)


def test_grasp_skill_fails_when_pose_frame_needs_transform_but_no_tf_buffer():
    """TF 버퍼가 없으면 프레임이 다른 저장된 pose를 조용히 잘못 사용하지 않고 명시적으로 실패해야 한다."""
    backend = FakeBackend()
    memory = SimpleNamespace(
        get_object_location=lambda name: (
            {
                "metadata": {
                    "target_pose": {
                        "position": {"x": 0.7, "y": 0.0, "z": 0.3},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "frame_id": "map",
                    }
                }
            }
            if name == "cup"
            else None
        )
    )
    node = _make_node(
        {"manipulation_end_effector_link": "tool0"},
        _manipulation_backend=backend,
        _memory=memory,
    )
    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"object_name": "cup"})

    assert result["success"] is False
    assert "TF" in result["message"]


def _stretch_object_node(monkeypatch, backend, position, *, extra_params=None):
    """object_name='cup'가 base_link 기준 position 에 있는 stretch 백엔드 노드 생성."""
    params = {
        "manipulation_backend": "stretch",
        "manipulation_end_effector_link": "link_grasp_center",
        "manipulation_gripper_presets_json": (
            '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
        ),
    }
    params.update(extra_params or {})
    memory = SimpleNamespace(
        get_object_location=lambda name: (
            {
                "metadata": {
                    "target_pose": {
                        "position": dict(zip("xyz", position, strict=True)),
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "frame_id": "base_link",
                    }
                }
            }
            if name == "cup"
            else None
        )
    )
    return _make_node(params, _manipulation_backend=backend, _memory=memory)


def test_grasp_rotates_when_unreachable_then_retries(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, 0.0, 0.5))

    reachable_calls = []

    def fake_check_reachable(xyz):
        reachable_calls.append(tuple(xyz))
        return len(reachable_calls) > 1

    monkeypatch.setattr(sb, "check_reachable", fake_check_reachable)
    monkeypatch.setattr(rotation, "compute_align_rotation_rad", lambda xyz: math.radians(45.0))
    monkeypatch.setattr(
        rotation, "_joint_positions", lambda skill, names: {"wrist_extension": 0.05}
    )

    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"object_name": "cup", "require_gripper_confirmation": False})

    assert result["success"] is True
    assert result["reachability"]["rotated"] is True
    assert result["base_rotation_applied_deg"] == pytest.approx(45.0)
    rotate_calls = [c for c in backend.calls if c[0] == "joint" and c[1] == "base"]
    assert len(rotate_calls) == 1
    assert rotate_calls[0][2] == {"rotate_mobile_base": pytest.approx(math.radians(45.0))}
    # check_reachable가 회전 전/후 두 번 호출되어야 한다 (재시도 확인).
    assert len(reachable_calls) == 2


def test_grasp_skips_rotation_for_moveit_backend(monkeypatch):
    backend = FakeBackend()
    params = {
        "manipulation_end_effector_link": "tool0",
        "manipulation_gripper_presets_json": '{"open":{"finger_joint":0.04},"close":{"finger_joint":0.0}}',
    }
    memory = SimpleNamespace(
        get_object_location=lambda name: (
            {
                "metadata": {
                    "target_pose": {
                        "position": {"x": 0.3, "y": 0.0, "z": 0.5},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "frame_id": "base_link",
                    }
                }
            }
            if name == "cup"
            else None
        )
    )
    node = _make_node(params, _manipulation_backend=backend, _memory=memory)

    def fail_if_called(xyz):
        raise AssertionError("MoveIt 백엔드에서는 check_reachable이 호출되면 안 됩니다")

    monkeypatch.setattr(sb, "check_reachable", fail_if_called)

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})

    assert result["success"] is True
    assert result["reachability"]["rotated"] is False
    rotate_calls = [c for c in backend.calls if c[0] == "joint" and c[1] == "base"]
    assert rotate_calls == []


def test_grasp_defaults_to_gripper_camera_confirmation_for_object_name(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(grasp_verification, "_joint_positions", lambda skill, names: None)

    estimate_calls = []
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill,
        "execute",
        lambda self, params: estimate_calls.append(params) or {"success": True},
    )
    servo_calls = []
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: (
            servo_calls.append(params) or {"success": True, "ready_to_grasp": True}
        ),
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})  # camera 파라미터 미지정

    assert result["success"] is True
    assert result["gripper_camera_confirmed"] is True
    assert len(estimate_calls) == 1
    assert estimate_calls[0]["target_object"] == "cup"
    assert len(servo_calls) == 1
    assert servo_calls[0]["target_object"] == "cup"


def test_grasp_blocks_close_when_not_ready_to_grasp(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill, "execute", lambda self, params: {"success": True}
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": False, "message": "안 보임"},
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})

    assert result["success"] is False
    assert result["gripper_camera_confirmed"] is False
    close_calls = [
        c
        for c in backend.calls
        if c[0] == "joint" and c[1] == "gripper" and c[2].get("gripper_aperture") == 0.0
    ]
    assert close_calls == []


def test_grasp_allows_opt_out_with_require_gripper_confirmation_false(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(grasp_verification, "_joint_positions", lambda skill, names: None)

    def fail_if_called(self, params):
        raise AssertionError("그리퍼 카메라 확인이 호출되면 안 됩니다")

    monkeypatch.setattr(EstimateGripperObjectPoseSkill, "execute", fail_if_called)
    monkeypatch.setattr(sequence.ServoGripperToObjectSkill, "execute", fail_if_called)

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup", "require_gripper_confirmation": False})

    assert result["success"] is True
    assert result["gripper_camera_confirmed"] is False
    assert result["confirmation_skipped_reason"] == "require_gripper_confirmation=False"


def test_grasp_verified_true_proceeds_normally(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill, "execute", lambda self, params: {"success": True}
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", lambda skill, names: {"gripper_aperture": 0.03}
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert result["grasp_retry_count"] == 0


def test_grasp_verified_false_retries_then_succeeds(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill, "execute", lambda self, params: {"success": True}
    )
    # 1차 close: 명령값까지 도달(허공) -> 재시도 -> 2차 close: 물체에 걸려 성공.
    achieved_sequence = [0.0, 0.03]
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {"gripper_aperture": achieved_sequence.pop(0)},
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert result["grasp_retry_count"] == 1
    close_calls = [
        c
        for c in backend.calls
        if c[0] == "joint" and c[1] == "gripper" and c[2].get("gripper_aperture") == 0.0
    ]
    assert len(close_calls) == 2  # 최초 시도 + 재시도 1회


def test_grasp_verified_false_after_retries_exhausted_fails(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill, "execute", lambda self, params: {"success": True}
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", lambda skill, names: {"gripper_aperture": 0.0}
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup", "max_grasp_retries": 1})

    assert result["success"] is False
    assert result["grasp_verified"] is False
    assert result["grasp_retry_count"] == 1


def test_place_confirms_with_gripper_camera_when_target_object_given(monkeypatch):
    backend = FakeBackend()
    params = {
        "manipulation_backend": "stretch",
        "manipulation_end_effector_link": "link_grasp_center",
        "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
    }
    node = _make_node(params, _manipulation_backend=backend)

    observe_calls = []
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: (
            observe_calls.append(params) or {"success": True, "ready_to_grasp": True}
        ),
    )

    skill = PlaceSkill()
    skill.set_node(node)
    result = skill.execute({"x": 0.4, "y": -0.2, "z": 0.15, "target_object": "table"})

    assert result["success"] is True
    assert result["gripper_camera_confirmed"] is True
    assert len(observe_calls) == 1
    assert observe_calls[0]["target_object"] == "table"


def test_place_marks_unconfirmed_when_no_target_object():
    backend = FakeBackend()
    params = {
        "manipulation_backend": "stretch",
        "manipulation_end_effector_link": "link_grasp_center",
        "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
    }
    node = _make_node(params, _manipulation_backend=backend)
    skill = PlaceSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.4, "y": -0.2, "z": 0.15})

    assert result["success"] is True
    assert result["gripper_camera_confirmed"] is False
    assert result["confirmation_skipped_reason"] == "no target_object given"


def test_place_skill_executes_release_sequence():
    backend = FakeBackend()
    params = {
        "manipulation_end_effector_link": "tool0",
        "manipulation_gripper_presets_json": '{"open":{"finger_joint":0.04}}',
    }
    node = _make_node(params, _manipulation_backend=backend)
    skill = PlaceSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.4, "y": -0.2, "z": 0.15})

    assert result["success"] is True
    assert backend.calls[1][0] == "pose"
    assert backend.calls[2] == ("joint", "gripper", {"finger_joint": 0.04})


def test_runtime_reports_unavailable_backend(monkeypatch):
    node = _make_node({"manipulation_end_effector_link": "tool0"})
    runtime = MoveItManipulationRuntime(node)

    class RaisingBackend:
        def __init__(self, *args, **kwargs):
            raise ManipulationRuntimeUnavailableError("missing moveit")

    monkeypatch.setattr(
        "robo_claw_agent.manipulation_runtime.runtime.MoveItPyBackend",
        RaisingBackend,
    )

    with pytest.raises(ManipulationRuntimeUnavailableError, match="missing moveit"):
        runtime.move_to_named_pose("home")


def test_invalid_json_config_is_rejected():
    node = _make_node({"manipulation_named_poses_json": "not-json"})

    with pytest.raises(ManipulationConfigError):
        load_manipulation_config(node)


def test_switch_stretch_mode_skill_calls_position_service(monkeypatch):
    future = _FakeFuture(_FakeTriggerResponse(True, "position ok"))
    holder = _patch_stretch_mode_node(monkeypatch, future)
    skill = SwitchStretchModeSkill()

    result = skill.execute({"mode": "position"})

    assert result["success"] is True
    assert result["mode"] == "position"
    assert result["service"] == "/switch_to_position_mode"
    assert result["message"] == "position ok"
    assert holder["node"].clients[0].service_name == "/switch_to_position_mode"
    assert holder["node"].destroyed is True


def test_switch_stretch_mode_skill_calls_navigation_service(monkeypatch):
    future = _FakeFuture(_FakeTriggerResponse(True, "navigation ok"))
    holder = _patch_stretch_mode_node(monkeypatch, future)
    skill = SwitchStretchModeSkill()

    result = skill.execute({"mode": "nav"})

    assert result["success"] is True
    assert result["mode"] == "navigation"
    assert result["service"] == "/switch_to_navigation_mode"
    assert holder["node"].clients[0].service_name == "/switch_to_navigation_mode"


def test_switch_stretch_mode_skill_rejects_invalid_mode():
    skill = SwitchStretchModeSkill()

    assert skill.validate_params({"mode": "unknown"}) is False
    result = skill.execute({"mode": "unknown"})

    assert result["success"] is False
    assert "position 또는 navigation" in result["message"]


def test_stretch_mode_skill_reports_timeout(monkeypatch):
    future = _FakeFuture(None, done=False)
    _patch_stretch_mode_node(monkeypatch, future)
    skill = StretchNavigationModeSkill()

    result = skill.execute({"timeout_sec": 0.1})

    assert result["success"] is False
    assert result["mode"] == "navigation"
    assert "타임아웃" in result["message"]
    assert future.cancelled is True


def test_stretch_position_mode_skill_uses_position(monkeypatch):
    future = _FakeFuture(_FakeTriggerResponse(True, ""))
    holder = _patch_stretch_mode_node(monkeypatch, future)
    skill = StretchPositionModeSkill()

    result = skill.execute({})

    assert result["success"] is True
    assert result["mode"] == "position"
    assert holder["node"].clients[0].service_name == "/switch_to_position_mode"


def test_stow_for_navigation_calls_stow_then_navigation(monkeypatch):
    stow_future = _FakeFuture(_FakeTriggerResponse(True, "stow ok"))
    nav_future = _FakeFuture(_FakeTriggerResponse(True, "nav ok"))
    holder = _patch_stretch_mode_nodes(monkeypatch, [stow_future, nav_future])
    skill = StowForNavigationSkill()

    result = skill.execute({})

    assert result["success"] is True
    assert result["stowed"] is True
    assert result["navigation_mode"] is True
    assert holder["nodes"][0].clients[0].service_name == "/stow_the_robot"
    assert holder["nodes"][1].clients[0].service_name == "/switch_to_navigation_mode"


def test_stow_for_navigation_reports_navigation_failure(monkeypatch):
    stow_future = _FakeFuture(_FakeTriggerResponse(True, "stow ok"))
    nav_future = _FakeFuture(_FakeTriggerResponse(False, "nav failed"))
    _patch_stretch_mode_nodes(monkeypatch, [stow_future, nav_future])
    skill = StowForNavigationSkill()

    result = skill.execute({})

    assert result["success"] is False
    assert result["stowed"] is True
    assert result["navigation_mode"] is False
    assert "navigation 모드 전환 실패" in result["message"]


def test_stow_for_navigation_can_skip_navigation_switch(monkeypatch):
    stow_future = _FakeFuture(_FakeTriggerResponse(True, "stow ok"))
    holder = _patch_stretch_mode_node(monkeypatch, stow_future)
    skill = StowForNavigationSkill()

    result = skill.execute({"switch_navigation": False})

    assert result["success"] is True
    assert result["stowed"] is True
    assert result["navigation_mode"] is False
    assert len(holder["nodes"]) == 1
    assert holder["nodes"][0].clients[0].service_name == "/stow_the_robot"


def test_pick_from_right_side_zone_runs_calibrated_sequence(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_ready":{"joint_lift":0.4},'
                '"right_side_pick_reach":{"wrist_extension":0.3},'
                '"right_side_pick_lift":{"joint_lift":0.5},'
                '"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", lambda skill, names: {"gripper_aperture": 0.03}
    )
    skill = PickFromRightSideZoneSkill()
    skill.set_node(node)

    result = skill.execute({})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("named", "arm", "right_side_pick_ready"),
        ("named", "arm", "right_side_pick_reach"),
        ("joint", "gripper", {"gripper_aperture": 0.0}),
        ("named", "arm", "right_side_pick_lift"),
        ("named", "arm", "carry"),
    ]


def test_align_right_arm_to_front_uses_stretch_rotate_mobile_base():
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    skill = sequence.AlignRightArmToFrontSkill()
    skill.set_node(node)

    result = skill.execute({"angle_deg": 90.0})

    assert result["success"] is True
    assert result["joint"] == "rotate_mobile_base"
    assert result["angle_source"] == "fixed"
    assert backend.calls == [
        ("joint", "base", {"rotate_mobile_base": pytest.approx(1.57079632679)})
    ]


def test_align_right_arm_to_front_falls_back_to_fixed_angle_without_target():
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    skill = sequence.AlignRightArmToFrontSkill()
    skill.set_node(node)

    result = skill.execute({})

    assert result["success"] is True
    assert result["angle_source"] == "fixed"
    assert result["angle_deg"] == pytest.approx(90.0)


def test_align_right_arm_to_front_computes_angle_from_target_pose():
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    skill = sequence.AlignRightArmToFrontSkill()
    skill.set_node(node)

    # 정면(로봇 x축 위) 물체 -- 회전 없이는 도달 불가하므로 계산된 각도로 회전해야 한다.
    result = skill.execute({"x": 0.3, "y": 0.0, "z": 0.5})

    assert result["success"] is True
    assert result["angle_source"] == "computed"
    assert result["angle_deg"] != pytest.approx(90.0)
    assert len(backend.calls) == 1
    kind, group, joints = backend.calls[0]
    assert kind == "joint"
    assert group == "base"
    assert set(joints.keys()) == {"rotate_mobile_base"}


def test_align_right_arm_to_front_rejects_rotation_exceeding_max_rotation_deg():
    backend = FakeBackend()
    robot_limits = {"navigation": {"max_rotation_deg": 10.0}}
    node = _make_node({}, _manipulation_backend=backend, _robot_limits_dict=robot_limits)
    skill = sequence.AlignRightArmToFrontSkill()
    skill.set_node(node)

    result = skill.execute({"angle_deg": 90.0})

    assert result["success"] is False
    assert backend.calls == []


def test_pick_front_object_composes_stow_align_and_right_side_pick(monkeypatch):
    calls = []

    def fake_stow_execute(self, params):
        calls.append(("stow", params))
        return {"success": True}

    def fake_align_execute(self, params):
        calls.append(("align", params["angle_deg"]))
        return {"success": True}

    def fake_pick_execute(self, params):
        calls.append(("pick", params.get("ready_pose", "default")))
        return {"success": True}

    monkeypatch.setattr(stretch_mode.StowForNavigationSkill, "execute", fake_stow_execute)
    monkeypatch.setattr(sequence.AlignRightArmToFrontSkill, "execute", fake_align_execute)
    monkeypatch.setattr(sequence.PickFromRightSideZoneSkill, "execute", fake_pick_execute)

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"align_angle_deg": 90.0, "ready_pose": "custom_ready"})

    assert result["success"] is True
    assert calls == [("stow", {}), ("align", 90.0), ("pick", "custom_ready")]


def test_pick_front_object_can_restore_heading(monkeypatch):
    calls = []

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(params["angle_deg"]) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.PickFromRightSideZoneSkill,
        "execute",
        lambda self, params: {"success": True},
    )

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"align_angle_deg": 90.0, "restore_heading": True})

    assert result["success"] is True
    assert calls == [90.0, -90.0]


def test_pick_front_object_uses_active_search_when_requested(monkeypatch):
    calls = []

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 32.0, "body_rotation_applied_deg": 90.0}
        ),
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(("align", params["angle_deg"])) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.PickFromRightSideZoneSkill,
        "execute",
        lambda self, params: calls.append("pick") or {"success": True},
    )

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "use_active_search": True, "restore_heading": True}
    )

    assert result["success"] is True
    assert result["used_active_search"] is True
    # search가 찾은 각도(32.0)가 고정값(90.0) 대신 사용되어야 하고,
    # 복귀 시에는 search의 몸통 회전(90.0)까지 합친 누적치를 되돌려야 한다.
    assert calls == ["search", ("align", 32.0), "pick", ("align", -122.0)]


def test_pick_front_object_fails_when_active_search_fails(monkeypatch):
    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {"success": False, "message": "못 찾음"},
    )

    def fail_if_called(self, params):
        raise AssertionError("탐색 실패 시 정렬/집기를 시도하면 안 됩니다")

    monkeypatch.setattr(sequence.AlignRightArmToFrontSkill, "execute", fail_if_called)
    monkeypatch.setattr(sequence.PickFromRightSideZoneSkill, "execute", fail_if_called)

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_active_search": True})

    assert result["success"] is False
    assert "탐색 실패" in result["message"]


def test_pick_front_object_active_search_requires_target_object(monkeypatch):
    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )

    def fail_if_called(self, params):
        raise AssertionError("target_object 없이 SearchObjectSkill이 호출되면 안 됩니다")

    monkeypatch.setattr(sequence.SearchObjectSkill, "execute", fail_if_called)

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"use_active_search": True})

    assert result["success"] is False


def test_observe_gripper_target_skill_uses_observation_helper(monkeypatch):
    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: {
            "success": True,
            "target_object": params["target_object"],
            "ready_to_grasp": True,
        },
    )
    skill = ObserveGripperTargetSkill()
    skill.set_node(_make_node())

    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert result["target_object"] == "cup"
    assert result["ready_to_grasp"] is True


def test_fallback_cup_center_from_gripper_image_detects_green_cup():
    skill = ServoGripperToObjectSkill()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (120, 120, 120)
    image[240:320, 260:340] = (120, 150, 110)
    skill.get_opencv_image = lambda params, timeout_sec=1.0: (image, "/camera", False)

    fallback = gripper_vision._fallback_cup_center_from_gripper_image(
        skill, {"target_object": "cup"}
    )

    assert fallback is not None
    cx, cy, size_x, size_y, message = fallback
    assert cx == pytest.approx(299.5, abs=2.0)
    assert cy == pytest.approx(279.5, abs=2.0)
    assert size_x >= 70
    assert size_y >= 70
    assert "fallback" in message


def test_fallback_cup_center_default_hsv_does_not_match_blue_cup():
    """기본 HSV 범위는 (다른 현장의) 파란 컵까지 잡아내지 않아야 한다 —
    하드코딩된 녹색 전용 폴백이라는 한계를 보여주는 회귀 테스트."""
    skill = ServoGripperToObjectSkill()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (120, 120, 120)
    image[240:320, 260:340] = (200, 60, 40)  # BGR 강한 파랑
    skill.get_opencv_image = lambda params, timeout_sec=1.0: (image, "/camera", False)

    fallback = gripper_vision._fallback_cup_center_from_gripper_image(
        skill, {"target_object": "cup"}
    )

    assert fallback is None


def test_fallback_cup_center_hsv_params_generalize_to_other_colors():
    """cup_fallback_hsv_lower/upper 파라미터로 녹색이 아닌 컵도 잡을 수 있어야 한다."""
    skill = ServoGripperToObjectSkill()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (120, 120, 120)
    image[240:320, 260:340] = (200, 60, 40)  # BGR 강한 파랑
    skill.get_opencv_image = lambda params, timeout_sec=1.0: (image, "/camera", False)

    fallback = gripper_vision._fallback_cup_center_from_gripper_image(
        skill,
        {
            "target_object": "cup",
            "cup_fallback_hsv_lower": [95, 60, 40],
            "cup_fallback_hsv_upper": [130, 255, 255],
        },
    )

    assert fallback is not None
    cx, cy, size_x, size_y, message = fallback
    assert cx == pytest.approx(299.5, abs=2.0)
    assert cy == pytest.approx(279.5, abs=2.0)
    assert "fallback" in message


def test_fallback_cup_center_invalid_param_length_falls_back_to_default():
    """길이가 맞지 않는 잘못된 파라미터는 조용히 기본값으로 폴백해야 한다."""
    skill = ServoGripperToObjectSkill()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (120, 120, 120)
    image[240:320, 260:340] = (120, 150, 110)
    skill.get_opencv_image = lambda params, timeout_sec=1.0: (image, "/camera", False)

    fallback = gripper_vision._fallback_cup_center_from_gripper_image(
        skill,
        {"target_object": "cup", "cup_fallback_hsv_lower": [1, 2]},  # 길이 2 (잘못됨, 3 필요)
    )

    assert fallback is not None
    cx, cy, size_x, size_y, message = fallback
    assert cx == pytest.approx(299.5, abs=2.0)
    assert cy == pytest.approx(279.5, abs=2.0)


def test_observe_gripper_target_uses_stricter_y_tolerance_for_cup(monkeypatch):
    det = SimpleNamespace(
        bbox=SimpleNamespace(
            center=SimpleNamespace(position=SimpleNamespace(x=320.0, y=337.9)),
            size_x=80.0,
            size_y=90.0,
        )
    )
    monkeypatch.setattr(
        gripper_vision,
        "_best_gripper_detection",
        lambda *args, **kwargs: (det, "cup", 0.9, ""),
    )
    depth_frame = SimpleNamespace(depth_img=np.zeros((480, 640), dtype=np.uint16))
    monkeypatch.setattr(perception_core, "_fetch_depth_frame", lambda skill, params: depth_frame)
    monkeypatch.setattr(
        perception_core,
        "_depth_point_for_pixel",
        lambda skill, depth_frame, params, cx, cy: ((0.0, 0.0, 0.0), 0.42),
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node())
    result = gripper_vision._observe_gripper_target(
        skill,
        {
            "target_object": "cup",
            "target_center_y_ratio": 0.58,
            "center_tolerance_px": 60.0,
            "center_tolerance_y_px": 35.0,
            "target_depth_m": 0.42,
            "depth_tolerance_m": 0.07,
        },
    )

    assert result["success"] is True
    assert result["image_error_px"]["y"] == pytest.approx(59.5)
    assert result["centered"] is False
    assert result["ready_to_grasp"] is False


def test_observe_gripper_target_allows_wider_x_tolerance_for_open_cup_grasp(monkeypatch):
    det = SimpleNamespace(
        bbox=SimpleNamespace(
            center=SimpleNamespace(position=SimpleNamespace(x=406.4, y=302.1)),
            size_x=80.0,
            size_y=90.0,
        )
    )
    monkeypatch.setattr(
        gripper_vision,
        "_best_gripper_detection",
        lambda *args, **kwargs: (det, "cup", 0.9, ""),
    )
    depth_frame = SimpleNamespace(depth_img=np.zeros((480, 640), dtype=np.uint16))
    monkeypatch.setattr(perception_core, "_fetch_depth_frame", lambda skill, params: depth_frame)
    monkeypatch.setattr(
        perception_core,
        "_depth_point_for_pixel",
        lambda skill, depth_frame, params, cx, cy: ((0.0, 0.0, 0.0), 0.174),
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node())
    result = gripper_vision._observe_gripper_target(
        skill,
        {
            "target_object": "cup",
            "target_center_y_ratio": 0.58,
            "center_tolerance_y_px": 30.0,
            "target_depth_m": 0.22,
            "depth_tolerance_m": 0.05,
        },
    )

    assert result["image_error_px"]["x"] == pytest.approx(86.4)
    assert result["image_error_px"]["y"] == pytest.approx(23.7)
    assert result["center_tolerance_px"]["x"] == pytest.approx(100.0)
    assert result["ready_to_grasp"] is True


def test_servo_gripper_to_object_moves_extension_and_lift(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    observations = [
        {
            "success": True,
            "ready_to_grasp": False,
            "depth_m": 0.35,
            "image_error_px": {"x": 0.0, "y": -100.0},
        },
        {
            "success": True,
            "ready_to_grasp": True,
            "depth_m": 0.25,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
    ]

    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: observations.pop(0),
    )
    monkeypatch.setattr(
        gripper_target_skills,
        "_joint_positions",
        lambda skill, names: {"wrist_extension": 0.1, "joint_lift": 0.4},
    )
    skill = ServoGripperToObjectSkill()
    skill.set_node(node)

    result = skill.execute({"target_object": "cup", "max_steps": 2})

    assert result["success"] is True
    assert len(backend.calls) == 1
    assert backend.calls[0][0:2] == ("joint", "arm")
    assert backend.calls[0][2] == {
        "wrist_extension": pytest.approx(0.15),
        "joint_lift": pytest.approx(0.45),
    }


def test_servo_gripper_to_object_scans_lift_after_losing_seen_target(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    observations = [
        {
            "success": True,
            "ready_to_grasp": False,
            "depth_m": 0.55,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
        {"success": False, "message": "lost target"},
        {
            "success": True,
            "ready_to_grasp": True,
            "depth_m": 0.25,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
    ]

    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: observations.pop(0),
    )
    monkeypatch.setattr(
        gripper_target_skills,
        "_joint_positions",
        lambda skill, names: {"wrist_extension": 0.1, "joint_lift": 0.4},
    )
    skill = ServoGripperToObjectSkill()
    skill.set_node(node)

    result = skill.execute({"target_object": "cup", "max_steps": 4})

    assert result["success"] is True
    assert backend.calls == [
        ("joint", "arm", {"wrist_extension": pytest.approx(0.15)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.37)}),
    ]


def test_servo_gripper_to_object_alternates_lift_recovery_direction(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    observations = [
        {
            "success": True,
            "ready_to_grasp": False,
            "depth_m": 0.55,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
        {"success": False, "message": "lost target"},
        {"success": False, "message": "still lost"},
        {
            "success": True,
            "ready_to_grasp": True,
            "depth_m": 0.25,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
    ]

    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: observations.pop(0),
    )
    monkeypatch.setattr(
        gripper_target_skills,
        "_joint_positions",
        lambda skill, names: {"wrist_extension": 0.1, "joint_lift": 0.4},
    )
    skill = ServoGripperToObjectSkill()
    skill.set_node(node)

    result = skill.execute({"target_object": "cup", "max_steps": 5})

    assert result["success"] is True
    assert backend.calls == [
        ("joint", "arm", {"wrist_extension": pytest.approx(0.15)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.37)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.43)}),
    ]


def test_joint_positions_synthesizes_wrist_extension_from_arm_segments():
    skill = ServoGripperToObjectSkill()
    msg = _FakeJointState(
        ["joint_lift", "joint_arm_l0", "joint_arm_l1", "joint_arm_l2", "joint_arm_l3"],
        [0.4, 0.01, 0.02, 0.03, 0.04],
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=1.0, **kwargs: msg

    positions = sequence._joint_positions(skill, ["joint_lift", "wrist_extension"])

    assert positions == {"joint_lift": 0.4, "wrist_extension": 0.1}


def test_joint_positions_synthesizes_gripper_aperture_from_finger_joints():
    skill = ServoGripperToObjectSkill()
    msg = _FakeJointState(
        ["joint_gripper_finger_left", "joint_gripper_finger_right"],
        [0.25, 0.25],
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=1.0, **kwargs: msg

    positions = sequence._joint_positions(skill, ["gripper_aperture"])

    assert positions == {"gripper_aperture": pytest.approx(0.0855)}


def test_vla_pick_front_object_runs_safe_sequence(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_ready":{"joint_lift":0.4},'
                '"right_side_pick_lift":{"joint_lift":0.5},'
                '"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", lambda skill, names: {"gripper_aperture": 0.03}
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("named", "arm", "right_side_pick_ready"),
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("joint", "gripper", {"gripper_aperture": 0.0}),
        ("named", "arm", "right_side_pick_lift"),
    ]


def test_vla_pick_front_object_does_not_close_when_servo_not_ready(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
            "manipulation_named_poses_json": '{"right_side_pick_ready":{"joint_lift":0.4}}',
        },
        _manipulation_backend=backend,
    )

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": False},
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is False
    assert "gripper close" in result["message"]
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("named", "arm", "right_side_pick_ready"),
    ]


def test_vla_pick_front_object_uses_active_search_when_requested(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_ready":{"joint_lift":0.4},'
                '"right_side_pick_lift":{"joint_lift":0.5},'
                '"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )

    calls = []

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 60.0, "body_rotation_applied_deg": 0.0}
        ),
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(("align", params["angle_deg"])) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", lambda skill, names: {"gripper_aperture": 0.03}
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "use_active_search": True})

    assert result["success"] is True
    assert result["used_active_search"] is True
    assert result["base_rotation_applied_deg"] == pytest.approx(60.0)
    # 고정 90도 대신 search가 찾은 60도가 사용되어야 한다.
    assert calls == ["search", ("align", 60.0)]


def test_vla_pick_front_object_defaults_to_fixed_angle_without_active_search(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
            "manipulation_named_poses_json": '{"right_side_pick_ready":{"joint_lift":0.4}}',
        },
        _manipulation_backend=backend,
    )

    def fail_if_called(self, params):
        raise AssertionError(
            "use_active_search 기본값(False)에서는 SearchObjectSkill이 호출되면 안 됩니다"
        )

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(sequence.SearchObjectSkill, "execute", fail_if_called)
    align_calls = []
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: align_calls.append(params["angle_deg"]) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": False},
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    # servo가 ready_to_grasp=False를 반환하는 조기 실패 경로라 고정 각도(90.0)로
    # align이 한 번만 호출되었는지만 확인한다(search는 호출되지 않아야 함).
    assert result["success"] is False
    assert align_calls == [90.0]


def test_prepare_right_side_pick_opens_and_moves_ready():
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
            "manipulation_named_poses_json": '{"right_side_pick_ready":{"joint_lift":0.4}}',
        },
        _manipulation_backend=backend,
    )
    skill = PrepareRightSidePickSkill()
    skill.set_node(node)

    result = skill.execute({})

    assert result["success"] is True
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("named", "arm", "right_side_pick_ready"),
    ]


def test_vla_pick_gripper_object_does_not_align_or_move_ready_by_default(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": '{"close":{"gripper_aperture":0.0}}',
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", lambda skill, names: {"gripper_aperture": 0.03}
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.0}),
        ("named", "arm", "right_side_pick_lift"),
    ]


def test_vla_pick_gripper_object_uses_loose_close_for_cup(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"close":{"gripper_aperture":0.0},"close_loose":{"gripper_aperture":0.025}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", lambda skill, names: {"gripper_aperture": 0.04}
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert result["close_preset"] == "close_loose"
    assert backend.calls[0] == ("joint", "gripper", {"gripper_aperture": 0.025})


def test_vla_pick_gripper_object_explicit_close_preset_wins(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"close":{"gripper_aperture":0.0},"close_loose":{"gripper_aperture":0.025}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", lambda skill, names: {"gripper_aperture": 0.03}
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "close_preset": "close"})

    assert result["success"] is True
    assert result["close_preset"] == "close"
    assert backend.calls[0] == ("joint", "gripper", {"gripper_aperture": 0.0})


def test_vla_pick_gripper_object_does_not_close_when_not_ready(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": False},
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is False
    assert backend.calls == []


def test_estimate_gripper_object_pose_uses_gripper_low_score_defaults(monkeypatch):
    captured = []
    monkeypatch.setattr(
        FindObjectSkill,
        "execute",
        lambda self, params: (
            captured.append(params) or {"success": False, "message": "stop after capture"}
        ),
    )

    skill = EstimateGripperObjectPoseSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is False
    assert len(captured) == 1
    assert captured[0]["camera"] == "gripper"
    assert captured[0]["detections_topic"] == "/gripper_object_detector_node/detections"
    assert captured[0]["min_score"] == pytest.approx(0.1)
    assert captured[0]["timeout_sec"] == pytest.approx(6.0)


def _stretch_head_node(backend=None):
    backend = backend or FakeBackend()
    return _make_node({"manipulation_backend": "stretch"}, _manipulation_backend=backend), backend


def test_head_pan_tilt_rejects_non_stretch_backend():
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)  # manipulation_backend 미지정(기본 moveit)
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pan_deg": 10.0, "tilt_deg": -10.0})

    assert result["success"] is False
    assert "Stretch3" in result["message"]
    assert backend.calls == []


def test_head_pan_tilt_moves_with_explicit_deg_values():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pan_deg": 45.0, "tilt_deg": -30.0})

    assert result["success"] is True
    assert backend.calls == [
        (
            "joint",
            "head",
            {
                "joint_head_pan": pytest.approx(math.radians(45.0)),
                "joint_head_tilt": pytest.approx(math.radians(-30.0)),
            },
        )
    ]


def test_head_pan_tilt_moves_with_explicit_rad_values():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pan_rad": 0.5, "tilt_rad": -0.8})

    assert result["success"] is True
    assert backend.calls == [("joint", "head", {"joint_head_pan": 0.5, "joint_head_tilt": -0.8})]


def test_head_pan_tilt_uses_builtin_default_named_pose():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pose_name": "search_head_down"})

    assert result["success"] is True
    assert backend.calls == [("joint", "head", {"joint_head_pan": 0.1, "joint_head_tilt": -0.8})]


def test_head_pan_tilt_configured_named_pose_overrides_default():
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_backend": "stretch",
            "manipulation_named_poses_json": (
                '{"search_head_down":{"joint_head_pan":0.5,"joint_head_tilt":-0.5}}'
            ),
        },
        _manipulation_backend=backend,
    )
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pose_name": "search_head_down"})

    assert result["success"] is True
    assert backend.calls == [("joint", "head", {"joint_head_pan": 0.5, "joint_head_tilt": -0.5})]


def test_head_pan_tilt_rejects_unknown_pose_name():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pose_name": "no_such_pose"})

    assert result["success"] is False
    assert backend.calls == []


def test_head_pan_tilt_requires_pose_name_or_angles():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({})

    assert result["success"] is False
    assert backend.calls == []


def test_head_pan_tilt_enforces_robot_limits():
    backend = FakeBackend()
    robot_limits = {"manipulation": {"joint_limits": {"joint_head_pan": [-1.0, 1.0]}}}
    node = _make_node(
        {"manipulation_backend": "stretch"},
        _manipulation_backend=backend,
        _robot_limits_dict=robot_limits,
    )
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pan_rad": 2.0})

    assert result["success"] is False
    assert backend.calls == []


def test_search_object_requires_target_object():
    skill = SearchObjectSkill()
    skill.set_node(_make_node())

    assert skill.validate_params({}) is False
    result = skill.execute({})
    assert result["success"] is False


def test_search_object_succeeds_on_first_head_pan_view(monkeypatch):
    node, backend = _stretch_head_node()
    head_calls = []

    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: head_calls.append(params) or {"success": True},
    )

    find_calls = []

    def fake_localize(node_, params, target_object):
        find_calls.append(target_object)
        return 0.5, {"success": True, "class_name": target_object}

    monkeypatch.setattr(search_skill, "_localize_rotation_with_base_camera", fake_localize)

    align_calls = []
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: align_calls.append(params["angle_deg"]) or {"success": True},
    )

    skill = SearchObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "head_settle_sec": 0})

    assert result["success"] is True
    assert result["align_angle_deg"] == pytest.approx(math.degrees(0.5))
    assert result["body_rotation_applied_deg"] == pytest.approx(0.0)
    # 헤드 팜 스윗 첫 번째 시도에서 바로 찾았으므로 몸통 회전은 필요하지 않다.
    assert align_calls == []
    assert len(find_calls) == 1
    assert len(head_calls) == 1


def test_search_object_sweeps_head_pan_before_rotating_body(monkeypatch):
    node, backend = _stretch_head_node()

    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: {"success": True},
    )

    find_results = [
        (None, {"success": False, "message": "not found"}),
        (None, {"success": False, "message": "not found"}),
        (None, {"success": False, "message": "not found"}),
        (0.2, {"success": True, "class_name": "cup"}),
    ]

    def fake_localize(node_, params, target_object):
        return find_results.pop(0)

    monkeypatch.setattr(search_skill, "_localize_rotation_with_base_camera", fake_localize)

    align_calls = []
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: align_calls.append(params["angle_deg"]) or {"success": True},
    )

    skill = SearchObjectSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "pan_sweep_deg": [-68.8, 0.0, 68.8], "head_settle_sec": 0}
    )

    assert result["success"] is True
    # pan 3스텝을 다 시도한 뒤에야 몸통 회전(1회)이 일어난다.
    assert align_calls == [90.0]
    assert result["body_rotation_applied_deg"] == pytest.approx(90.0)


def test_search_object_gives_up_after_max_body_rotations(monkeypatch):
    node, backend = _stretch_head_node()

    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        search_skill,
        "_localize_rotation_with_base_camera",
        lambda node_, params, target_object: (None, {"success": False, "message": "not found"}),
    )
    align_calls = []
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: align_calls.append(params["angle_deg"]) or {"success": True},
    )

    skill = SearchObjectSkill()
    skill.set_node(node)
    result = skill.execute(
        {
            "target_object": "cup",
            "pan_sweep_deg": [0.0],
            "max_body_rotations": 2,
            "head_settle_sec": 0,
        }
    )

    assert result["success"] is False
    assert align_calls == [90.0, 90.0]
    assert result["body_rotation_applied_deg"] == pytest.approx(180.0)


def test_search_object_skips_head_when_not_stretch_backend(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)  # moveit 기본

    def fail_if_called(self, params):
        raise AssertionError("non-stretch 백엔드에서는 헤드 제어를 시도하면 안 됩니다")

    monkeypatch.setattr(HeadPanTiltSkill, "execute", fail_if_called)

    calls = []

    def fake_localize(node_, params, target_object):
        calls.append("find")
        return 0.3, {"success": True}

    monkeypatch.setattr(search_skill, "_localize_rotation_with_base_camera", fake_localize)

    skill = SearchObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert calls == ["find"]


def test_adaptive_pick_object_full_flow_uses_search_result_to_align(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 45.0, "body_rotation_applied_deg": 90.0}
        ),
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(("align", params["angle_deg"])) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append("pick") or {"success": True},
    )
    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: calls.append(("head", params.get("pose_name"))) or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False})

    assert result["success"] is True
    assert calls == [
        "search",
        ("align", 45.0),
        "prepare",
        "observe",
        "pick",
        ("head", "travel_head"),
    ]


def test_adaptive_pick_object_skips_align_when_search_angle_negligible(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 0.0, "body_rotation_applied_deg": 0.0}
        ),
    )

    def fail_if_called(self, params):
        raise AssertionError("회전각이 0에 가까울 때는 align을 호출하면 안 됩니다")

    monkeypatch.setattr(sequence.AlignRightArmToFrontSkill, "execute", fail_if_called)
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append("pick") or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False, "restore_head_after": False})

    assert result["success"] is True
    assert calls == ["search", "prepare", "observe", "pick"]


def test_adaptive_pick_object_can_skip_search(monkeypatch):
    calls = []

    def fail_if_called(self, params):
        raise AssertionError("skip_search=True면 SearchObjectSkill이 호출되면 안 됩니다")

    monkeypatch.setattr(sequence.SearchObjectSkill, "execute", fail_if_called)
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append("pick") or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "skip_search": True, "use_fixed_pattern_grasp": False, "restore_head_after": False}
    )

    assert result["success"] is True
    assert calls == ["prepare", "observe", "pick"]


def test_adaptive_pick_object_fails_when_search_fails(monkeypatch):
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {"success": False, "message": "못 찾음"},
    )

    def fail_if_called(self, params):
        raise AssertionError("탐색 실패 시 다음 단계가 호출되면 안 됩니다")

    monkeypatch.setattr(sequence.PrepareRightSidePickSkill, "execute", fail_if_called)
    monkeypatch.setattr(sequence.VLABasedPickGripperObjectSkill, "execute", fail_if_called)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False, "restore_head_after": False})

    assert result["success"] is False
    assert "탐색 실패" in result["message"]


def test_adaptive_pick_object_fails_when_observe_fails_and_restores_heading(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {
            "success": True,
            "align_angle_deg": 45.0,
            "body_rotation_applied_deg": 0.0,
        },
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(("align", params["angle_deg"])) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": False, "message": "not found"},
    )

    def fail_if_called(self, params):
        raise AssertionError("그리퍼 확인 실패 시 집기를 시도하면 안 됩니다")

    monkeypatch.setattr(sequence.VLABasedPickGripperObjectSkill, "execute", fail_if_called)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "return_to_start_on_fail": True, "use_fixed_pattern_grasp": False, "restore_head_after": False}
    )

    assert result["success"] is False
    assert calls == [
        ("align", 45.0),
        "prepare",
        "observe",
        ("align", -45.0),
    ]


def test_adaptive_pick_object_restores_head_even_on_pick_failure(monkeypatch):
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {
            "success": True,
            "align_angle_deg": None,
            "body_rotation_applied_deg": 0.0,
        },
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: {"success": False, "message": "grasp failed"},
    )
    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: calls.append(("head", params.get("pose_name"))) or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False})

    assert result["success"] is False
    assert "grasp failed" in result["message"]
    assert calls == [("head", "travel_head")]


def test_adaptive_pick_object_default_max_attempts_does_not_retry(monkeypatch):
    """max_attempts 미지정 시 기존과 동일하게 1회 실패로 즉시 종료해야 한다."""
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: calls.append("search") or {"success": False, "message": "못 찾음"},
    )

    def fail_if_called(self, params):
        raise AssertionError("max_attempts 미지정 시 재시도가 발생하면 안 됩니다")

    monkeypatch.setattr(sequence.PrepareRightSidePickSkill, "execute", fail_if_called)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False, "restore_head_after": False})

    assert result["success"] is False
    assert calls == ["search"]
    assert "attempts" not in result
    assert not any(step.get("skill") == "retry_pending" for step in result["steps"])


def test_adaptive_pick_object_retries_full_sequence_after_pick_failure(monkeypatch):
    """max_attempts=2 이면 파지 실패 후 탐색부터 다시 시도하고, 2번째 시도가 성공하면 종료한다."""
    calls = []
    search_results = iter(
        [
            {"success": True, "align_angle_deg": None, "body_rotation_applied_deg": 0.0},
            {"success": True, "align_angle_deg": None, "body_rotation_applied_deg": 0.0},
        ]
    )
    pick_results = iter(
        [
            {"success": False, "message": "grasp failed once"},
            {"success": True},
        ]
    )

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: calls.append("search") or next(search_results),
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: calls.append("observe") or {"success": True},
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: calls.append("pick") or next(pick_results),
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "restore_head_after": False, "use_fixed_pattern_grasp": False, "max_attempts": 2}
    )

    assert result["success"] is True
    assert calls == [
        "search",
        "prepare",
        "observe",
        "pick",
        "search",
        "prepare",
        "observe",
        "pick",
    ]
    retry_markers = [s for s in result["steps"] if s.get("skill") == "retry_pending"]
    assert len(retry_markers) == 1
    assert "grasp failed once" in retry_markers[0]["message"]


def test_adaptive_pick_object_reports_attempts_when_all_retries_exhausted(monkeypatch):
    """max_attempts 만큼 모두 실패하면 attempts 필드에 정확한 시도 횟수를 남긴다."""
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {"success": False, "message": "never found"},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "restore_head_after": False, "use_fixed_pattern_grasp": False, "max_attempts": 3}
    )

    assert result["success"] is False
    assert result["attempts"] == 3
    retry_markers = [s for s in result["steps"] if s.get("skill") == "retry_pending"]
    assert len(retry_markers) == 2


# ── 고정 패턴 파지 / 표면 분류 스킬 (옵션 B) ────────────────────────────────

def _expect_joints(xyz):
    """FixedPatternGraspSkill._ik_reach와 동일한 클램프 적용 기대 관절값."""
    from robo_claw_agent.manipulation_runtime.stretch_kinematics import _ik_position

    lift, ext, yaw = _ik_position(xyz)
    return {
        "joint_lift": max(0.0, min(1.1, float(lift))),
        "wrist_extension": max(0.0, min(0.52, float(ext))),
        "joint_wrist_yaw": float(yaw),
    }


def test_classify_object_surface_from_object_base_xyz():
    skill = ClassifyObjectSurfaceSkill()
    skill.set_node(_make_node({}))
    floor = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.12}}
    )
    assert floor["success"] is True
    assert floor["surface"] == "floor"
    assert floor["surface_from"] == "object_base_xyz"

    elevated = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.45}}
    )
    assert elevated["success"] is True
    assert elevated["surface"] == "elevated"


def test_classify_object_surface_uses_hint():
    skill = ClassifyObjectSurfaceSkill()
    skill.set_node(_make_node({}))
    result = skill.execute({"target_object": "cup", "surface_hint": "elevated"})
    assert result["success"] is True
    assert result["surface"] == "elevated"
    assert result["surface_from"] == "hint"


def test_classify_object_surface_custom_threshold():
    skill = ClassifyObjectSurfaceSkill()
    skill.set_node(_make_node({}))
    # z=0.30은 기본 임계(0.15)로는 elevated지만 임계를 0.4로 올리면 floor.
    result = skill.execute(
        {
            "target_object": "cup",
            "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.30},
            "floor_z_threshold_m": 0.4,
        }
    )
    assert result["success"] is True
    assert result["surface"] == "floor"


def test_fixed_pattern_grasp_floor_deterministic_sequence(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {
            name: (0.03 if name == "gripper_aperture" else 0.4) for name in names
        },
    )
    monkeypatch.setattr(
        sequence.fixed_pattern_grasp,
        "_log_achieved_gripper_aperture",
        lambda skill, logger, stage: None,
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.12}}
    )

    assert result["success"] is True
    assert result["surface"] == "floor"
    assert result["grasp_verified"] is True

    approach = _expect_joints((0.0, -0.6, 0.12 + 0.10))  # floor approach height 0.10
    grasp = _expect_joints((0.0, -0.6, 0.12))  # floor grasp offset 0.0
    expected = [
        ("joint", "gripper", {"gripper_aperture": 0.16}),  # open
        ("joint", "arm", approach),  # approach hover
        ("joint", "arm", grasp),  # reach grasp
        ("joint", "gripper", {"gripper_aperture": 0.0}),  # close
        ("joint", "arm", {"joint_lift": 0.4 + 0.18}),  # floor post-grasp lift
    ]
    assert backend.calls == expected


def test_fixed_pattern_grasp_elevated_deterministic_sequence(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {
            name: (0.03 if name == "gripper_aperture" else 0.4) for name in names
        },
    )
    monkeypatch.setattr(
        sequence.fixed_pattern_grasp,
        "_log_achieved_gripper_aperture",
        lambda skill, logger, stage: None,
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.45}}
    )

    assert result["success"] is True
    assert result["surface"] == "elevated"

    approach = _expect_joints((0.0, -0.6, 0.45 + 0.06))  # elevated approach height 0.06
    grasp = _expect_joints((0.0, -0.6, 0.45))  # elevated grasp offset 0.0
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("joint", "arm", approach),
        ("joint", "arm", grasp),
        ("joint", "gripper", {"gripper_aperture": 0.0}),
        ("joint", "arm", {"joint_lift": 0.4 + 0.12}),  # elevated post-grasp lift
    ]


def test_fixed_pattern_grasp_fails_when_object_unreachable(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            )
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {name: 0.03 for name in names},
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    # 팔 도달 범위 밖(아주 먼 곳)의 좌표 → IK 실패로 우아하게 실패해야 한다.
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 5.0, "y": 5.0, "z": 5.0}}
    )
    assert result["success"] is False
    assert "도달" in result["message"] or "IK" in result["message"]
    # IK 실패 시 그리퍼 open/팔 이동 어느 것도 실행하면 안 된다.
    assert backend.calls == []


def test_adaptive_pick_object_default_uses_fixed_pattern_grasp(monkeypatch):
    """기본 경로(옵션 B)는 고정 패턴 파지를 사용해야 한다."""
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 0.0, "body_rotation_applied_deg": 0.0}
        ),
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    # 기본 경로에서는 그리퍼 서보/VLA가 호출되면 안 된다.
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: (_ for _ in ()).throw(AssertionError("기본 경로에서 observe가 호출되면 안 됩니다")),
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: (_ for _ in ()).throw(AssertionError("기본 경로에서 vla_pick이 호출되면 안 됩니다")),
    )
    monkeypatch.setattr(
        sequence.FixedPatternGraspSkill,
        "execute",
        lambda self, params: calls.append("fixed_pattern_grasp") or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "restore_head_after": False})

    assert result["success"] is True
    assert calls == ["search", "prepare", "fixed_pattern_grasp"]
