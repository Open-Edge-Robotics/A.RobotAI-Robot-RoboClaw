"""AGX 드라이버 토픽 백엔드 단위 테스트.

실제 드라이버/하드웨어 없이 토픽 발행 내용과 완료 판정/실패 경로를 검증한다.
"""

from __future__ import annotations

import logging

import pytest
from geometry_msgs.msg import PoseStamped
from robo_claw_agent.manipulation_runtime import agx_backend
from robo_claw_agent.manipulation_runtime.agx_backend import AgxDriverBackend
from robo_claw_agent.manipulation_runtime.exceptions import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)
from robo_claw_agent.manipulation_runtime.types import ManipulationConfig, PoseGoal
from sensor_msgs.msg import JointState

pytestmark = pytest.mark.unit


class _RecordingPublisher:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def publish(self, msg: object) -> None:
        self.messages.append(msg)


class _DoneFuture:
    def __init__(self, response: object = None) -> None:
        self._response = response

    def done(self) -> bool:
        return True

    def result(self) -> object:
        return self._response

    def cancel(self) -> None:
        return None


class _FakeClient:
    def __init__(self, service_available: bool = True, response: object = None) -> None:
        self.service_available = service_available
        self.calls = 0
        self.requests: list[object] = []
        self.response = response

    def wait_for_service(self, timeout_sec: float) -> bool:
        del timeout_sec
        return self.service_available

    def call_async(self, request: object) -> _DoneFuture:
        self.calls += 1
        self.requests.append(request)
        return _DoneFuture(self.response)


class _FakeNode:
    """create_publisher/create_subscription/create_client 만 제공하는 최소 노드 대역."""

    def __init__(self, service_available: bool = True) -> None:
        self.publishers: dict[str, list[_RecordingPublisher]] = {}
        self.subscriptions: dict[str, object] = {}
        self.clients: dict[str, _FakeClient] = {}
        self._service_available = service_available

    def create_client(
        self, srv_type: object, name: str, *args: object, **kwargs: object
    ) -> _FakeClient:
        del srv_type, args, kwargs
        if name not in self.clients:
            self.clients[name] = _FakeClient(self._service_available)
        return self.clients[name]

    def create_publisher(
        self, msg_type: object, topic: str, qos: int, *args: object, **kwargs: object
    ) -> _RecordingPublisher:
        del msg_type, qos, args, kwargs
        publisher = _RecordingPublisher()
        self.publishers.setdefault(topic, []).append(publisher)
        return publisher

    def create_subscription(
        self,
        msg_type: object,
        topic: str,
        callback: object,
        qos: int,
        *args: object,
        **kwargs: object,
    ) -> object:
        del msg_type, qos, args, kwargs
        self.subscriptions[topic] = callback
        return callback


def _config(**overrides: object) -> ManipulationConfig:
    defaults: dict[str, object] = {
        "backend": "agx",
        "base_frame": "base_link",
        "agx_motion_timeout_sec": 0.2,
        "agx_feedback_timeout_sec": 0.05,
        "agx_joint_tolerance_rad": 0.02,
        "agx_pose_tolerance_m": 0.01,
        "named_pose_joint_values": {"home": {"joint1": 0.0, "joint2": 0.0}},
    }
    defaults.update(overrides)
    return ManipulationConfig(**defaults)  # type: ignore[arg-type]


def _published_messages(node: _FakeNode, topic: str) -> list[object]:
    return [msg for publisher in node.publishers.get(topic, []) for msg in publisher.messages]


def _joint_state(names: list[str], positions: list[float]) -> JointState:
    msg = JointState()
    msg.name = list(names)
    msg.position = list(positions)
    return msg


def _tcp_pose(x: float, y: float, z: float) -> PoseStamped:
    msg = PoseStamped()
    msg.header.frame_id = "base_link"
    msg.pose.position.x = x
    msg.pose.position.y = y
    msg.pose.position.z = z
    msg.pose.orientation.w = 1.0
    return msg


def _pose_goal(x: float, y: float, z: float, frame_id: str = "base_link") -> PoseGoal:
    return PoseGoal(
        frame_id=frame_id,
        position={"x": x, "y": y, "z": z},
        orientation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    )


def test_move_to_joint_target_publishes_and_returns_when_feedback_reached() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    backend.move_to_joint_target("arm", {"joint1": 0.0, "joint2": 0.0})

    published = node.publishers[config.agx_move_joint_topic][0].messages
    assert len(published) == 1
    assert isinstance(published[0], JointState)
    assert list(published[0].name) == ["joint1", "joint2"]
    assert list(published[0].position) == [0.0, 0.0]

    assert not _published_messages(node, config.agx_move_pose_topic)
    assert not _published_messages(node, config.agx_move_linear_topic)


def test_move_to_joint_target_raises_when_target_not_reached() -> None:
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=0.15)
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1"], [0.0])
    )
    with pytest.raises(ManipulationError, match="도달하지 못했습니다"):
        backend.move_to_joint_target("arm", {"joint1": 1.0})


def test_move_to_joint_target_raises_when_feedback_missing() -> None:
    node = _FakeNode()
    config = _config(agx_motion_timeout_sec=5.0, agx_feedback_timeout_sec=0.05)
    backend = AgxDriverBackend(node, config)

    with pytest.raises(ManipulationError, match="피드백을 받지 못했습니다"):
        backend.move_to_joint_target("arm", {"joint1": 0.0})


def test_move_to_named_pose_uses_configured_joint_map() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    backend.move_to_named_pose("arm", "home")

    published = node.publishers[config.agx_move_joint_topic][0].messages
    assert list(published[0].name) == ["joint1", "joint2"]


def test_move_to_named_pose_home_calls_driver_service() -> None:
    node = _FakeNode()
    config = _config(named_pose_joint_values={})
    backend = AgxDriverBackend(node, config)

    backend.move_to_named_pose("arm", "home")

    client = node.clients[config.agx_home_service]
    assert client.calls == 1
    assert not _published_messages(node, config.agx_move_joint_topic)


def test_move_to_named_pose_home_reports_missing_service() -> None:
    config = _config(named_pose_joint_values={})
    node = _FakeNode(service_available=True)
    node.clients[config.agx_home_service] = _FakeClient(service_available=False)
    backend = AgxDriverBackend(node, config)

    with pytest.raises(ManipulationRuntimeUnavailableError, match="move_home"):
        backend.move_to_named_pose("arm", "home")


def test_move_to_named_pose_without_joint_map_is_rejected() -> None:
    node = _FakeNode()
    backend = AgxDriverBackend(node, _config(named_pose_joint_values={}))

    with pytest.raises(ManipulationConfigError, match="조인트 값을 알 수 없습니다"):
        backend.move_to_named_pose("arm", "ready")


def test_move_to_pose_target_uses_pose_topic_and_waits_for_tcp_feedback() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_tcp_pose_topic](  # type: ignore[operator]
        _tcp_pose(0.3, 0.0, 0.4)
    )
    backend.move_to_pose_target("arm", _pose_goal(0.3, 0.0, 0.4), "link7")

    published = node.publishers[config.agx_move_pose_topic][0].messages
    assert isinstance(published[0], PoseStamped)
    assert published[0].header.frame_id == "base_link"
    assert published[0].pose.position.x == pytest.approx(0.3)
    assert not _published_messages(node, config.agx_move_linear_topic)


def test_move_to_pose_target_uses_linear_topic_for_cartesian() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_tcp_pose_topic](  # type: ignore[operator]
        _tcp_pose(0.0, 0.0, 0.0)
    )
    backend.move_to_pose_target("arm", _pose_goal(0.0, 0.0, 0.0), "link7", cartesian=True)

    assert not _published_messages(node, config.agx_move_pose_topic)
    assert len(node.publishers[config.agx_move_linear_topic][0].messages) == 1


def test_move_to_pose_target_rejects_other_frames() -> None:
    node = _FakeNode()
    backend = AgxDriverBackend(node, _config())

    with pytest.raises(ManipulationConfigError, match="base frame"):
        backend.move_to_pose_target("arm", _pose_goal(0.1, 0.0, 0.2, "map"), "link7")


def test_pose_target_requires_all_position_components() -> None:
    node = _FakeNode()
    backend = AgxDriverBackend(node, _config())
    goal = PoseGoal(
        frame_id="base_link",
        position={"x": 0.1, "y": 0.0},
        orientation={"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    )

    with pytest.raises(ManipulationConfigError, match="'z' 값이 없습니다"):
        backend.move_to_pose_target("arm", goal, "link7")


def test_move_enables_arm_and_control_gate_before_motion() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2"], [0.0, 0.0])
    )
    backend.move_to_joint_target("arm", {"joint1": 0.0, "joint2": 0.0})
    backend.move_to_joint_target("arm", {"joint1": 0.0, "joint2": 0.0})

    enable_client = node.clients[config.agx_enable_service]
    gate_client = node.clients[config.agx_control_enable_service]
    assert enable_client.calls == 1
    assert getattr(enable_client.requests[0], "data", None) is True
    assert gate_client.calls == 1
    assert getattr(gate_client.requests[0], "data", None) is True


def test_move_raises_when_enable_service_returns_false() -> None:
    class _FailedResp:
        success = False
        message = "Agx_arm is not connected"

    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)
    node.create_client(object, config.agx_enable_service).response = _FailedResp()

    with pytest.raises(ManipulationError, match="Agx_arm is not connected"):
        backend.move_to_named_pose("arm", "home")


def test_move_to_joint_target_preserves_unspecified_arm_joints_from_feedback() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2", "joint3"], [0.5, -0.35, 0.2])
    )
    backend.move_to_joint_target("arm", {"joint1": 0.5})

    published = node.publishers[config.agx_move_joint_topic][0].messages
    assert len(published) >= 1
    msg = published[0]
    assert isinstance(msg, JointState)
    joint_map = dict(zip(msg.name, msg.position, strict=False))
    assert joint_map == {
        "joint1": pytest.approx(0.5),
        "joint2": pytest.approx(-0.35),
        "joint3": pytest.approx(0.2),
    }


def test_move_to_joint_target_publishes_only_gripper_when_targets_is_gripper_only() -> None:
    node = _FakeNode()
    config = _config()
    backend = AgxDriverBackend(node, config)

    node.subscriptions[config.agx_joint_state_topic](  # type: ignore[operator]
        _joint_state(["joint1", "joint2", "gripper"], [0.5, -0.35, 0.05])
    )
    backend.move_to_joint_target("gripper", {"gripper": 0.05})

    published = node.publishers[config.agx_move_joint_topic][0].messages
    assert len(published) >= 1
    msg = published[0]
    assert isinstance(msg, JointState)
    assert list(msg.name) == ["gripper"]
    assert list(msg.position) == [pytest.approx(0.05)]


class _FakeClock:
    """time 모듈 대역. monotonic/sleep 만 제공한다."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_joint_state_feedback_logging_is_throttled(monkeypatch, caplog) -> None:
    """피드백 콜백마다 INFO 를 남기지 않고 일정 간격으로만 남긴다."""
    node = _FakeNode()
    config = _config()
    AgxDriverBackend(node, config)
    callback = node.subscriptions[config.agx_joint_state_topic]

    clock = _FakeClock()
    monkeypatch.setattr(agx_backend, "time", clock)

    with caplog.at_level(logging.INFO, logger=agx_backend.__name__):
        # 드라이버가 100Hz 로 발행하는 상황을 모사한다.
        for _ in range(100):
            callback(_joint_state(["gripper", "joint1"], [0.02, 0.01]))
            clock.now += 0.01

        clock.now += agx_backend._FEEDBACK_LOG_INTERVAL_SEC
        callback(_joint_state(["gripper", "joint1"], [0.03, 0.02]))

    feedback_logs = [
        record.message for record in caplog.records if "AGX feedback" in record.message
    ]
    assert len(feedback_logs) == 2
    assert "gripper=0.020000" in feedback_logs[0]
    assert "gripper=0.030000" in feedback_logs[1]
