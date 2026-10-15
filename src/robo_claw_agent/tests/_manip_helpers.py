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


def _grasp_positions(aperture):
    """파지 검증용 joint stub.

    aperture 만 담은 dict 는 파지 후 lift 검증이 요구하는 joint_lift 를 빠뜨려
    실패하므로, 요청된 이름을 기본값으로 채운 뒤 aperture/joint_lift 를 덮어쓴다.
    """

    def _positions(skill, names):
        values = {name: 0.0 for name in names}
        values["gripper_aperture"] = aperture
        values["joint_lift"] = 0.5
        return values

    return _positions


def _make_node(params=None, manipulation_backend="stretch", **extra):
    values = dict(params or {})
    values.setdefault("manipulation_backend", manipulation_backend)
    node = SimpleNamespace(
        has_parameter=lambda name: name in values,
        get_parameter=lambda name: DummyParameter(values[name]),
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


@pytest.fixture(autouse=True)
def _stub_joint_positions(monkeypatch):
    """실제 joint_states 대기를 피한다.

    기본은 None(현재 하네스와 동일한 "joint state 없음" 결과)이며, 특정 값이
    필요한 테스트는 각자 monkeypatch 로 덮어쓴다(fixture 가 먼저 실행됨).
    """
    from robo_claw_agent.skills.manipulation_skill.sequence import (
        ee_state,
        grasp_verification,
        gripper_target_skills,
        head_assist,
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

    for module in (
        joint_state,
        grasp_verification,
        gripper_target_skills,
        head_assist,
        ee_state,
        rotation,
    ):
        monkeypatch.setattr(module, "_joint_positions", _fake_positions)


@pytest.fixture(autouse=True)
def _stub_head_observation(monkeypatch):
    """헤드 카메라 관측이 실제 토픽 타임아웃을 기다리지 않도록 기본 실패로 고정한다."""
    from robo_claw_agent.skills.manipulation_skill.sequence import head_assist

    monkeypatch.setattr(
        head_assist,
        "_observe_head_target",
        lambda skill, params: {"success": False, "message": "head observation unavailable"},
    )


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


__all__ = [
    "AdaptivePickObjectSkill",
    "ArmPoseSkill",
    "ClassifyObjectSurfaceSkill",
    "CloseGripperSkill",
    "DummyParameter",
    "EstimateGripperObjectPoseSkill",
    "FakeBackend",
    "FindObjectSkill",
    "FixedPatternGraspSkill",
    "GraspSkill",
    "HeadPanTiltSkill",
    "ManipulationConfigError",
    "ManipulationError",
    "ManipulationRuntimeUnavailableError",
    "MoveItManipulationRuntime",
    "MoveJointsSkill",
    "MovePoseSkill",
    "ObserveGripperTargetSkill",
    "OpenGripperSkill",
    "PickFromRightSideZoneSkill",
    "PickFrontObjectSkill",
    "PlaceSkill",
    "PrepareRightSidePickSkill",
    "SearchObjectSkill",
    "ServoGripperToObjectSkill",
    "SimpleNamespace",
    "StowForNavigationSkill",
    "StretchNavigationModeSkill",
    "StretchPositionModeSkill",
    "SwitchStretchModeSkill",
    "VLABasedPickFrontObjectSkill",
    "VLABasedPickGripperObjectSkill",
    "_FakeClient",
    "_FakeFuture",
    "_FakeJointState",
    "_FakeStretchModeNode",
    "_FakeTriggerResponse",
    "_grasp_positions",
    "_make_node",
    "_patch_stretch_mode_node",
    "_patch_stretch_mode_nodes",
    "_stub_head_observation",
    "_stub_joint_positions",
    "adaptive_pick_skill",
    "grasp_verification",
    "gripper_target_skills",
    "gripper_vision",
    "load_manipulation_config",
    "math",
    "np",
    "perception_core",
    "pytest",
    "rotation",
    "sb",
    "search_skill",
    "sequence",
    "stretch_mode",
]
