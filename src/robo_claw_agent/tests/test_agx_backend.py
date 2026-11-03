"""AGX 드라이버 토픽 백엔드 단위 테스트.

실제 드라이버/하드웨어 없이 토픽 발행 내용과 완료 판정/실패 경로를 검증한다.
"""

from __future__ import annotations

import pytest
from geometry_msgs.msg import PoseStamped
from robo_claw_agent.manipulation_runtime.agx_backend import AgxDriverBackend
from robo_claw_agent.manipulation_runtime.exceptions import (
    ManipulationConfigError,
    ManipulationError,
)
from robo_claw_agent.manipulation_runtime.types import ManipulationConfig, PoseGoal
from sensor_msgs.msg import JointState

pytestmark = pytest.mark.unit


class _RecordingPublisher:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def publish(self, msg: object) -> None:
        self.messages.append(msg)


class _FakeNode:
    """create_publisher/create_subscription 만 제공하는 최소 노드 대역."""

    def __init__(self) -> None:
        self.publishers: dict[str, list[_RecordingPublisher]] = {}
        self.subscriptions: dict[str, object] = {}

    def create_publisher(self, msg_type: object, topic: str, qos: int) -> _RecordingPublisher:
        del msg_type, qos
        publisher = _RecordingPublisher()
        self.publishers.setdefault(topic, []).append(publisher)
        return publisher

    def create_subscription(
        self, msg_type: object, topic: str, callback: object, qos: int
    ) -> object:
        del msg_type, qos
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


def test_move_to_named_pose_without_joint_map_is_rejected() -> None:
    node = _FakeNode()
    backend = AgxDriverBackend(node, _config())

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
