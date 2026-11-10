"""Map subscription QoS contracts without ROS nodes or hardware."""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from nav_msgs.msg import OccupancyGrid
from rclpy.qos import (
    QoSCompatibility,
    QoSDurabilityPolicy,
    QoSProfile,
    QoSReliabilityPolicy,
    qos_check_compatible,
)
from robo_claw_grpc.nodes import nav_node

pytestmark = pytest.mark.unit


@pytest.fixture
def node(monkeypatch):
    subscriptions = []

    def subscribe(_self, **kwargs):
        subscription = SimpleNamespace(**kwargs)
        subscriptions.append(subscription)
        return subscription

    monkeypatch.setattr(nav_node.Node, "__init__", lambda *_args: None)
    monkeypatch.setattr(nav_node.Node, "create_subscription", subscribe)
    monkeypatch.setattr(nav_node.Node, "create_publisher", lambda *_args: MagicMock())
    monkeypatch.setattr(nav_node.Node, "get_logger", lambda _self: MagicMock())
    monkeypatch.setattr(nav_node, "ActionClient", MagicMock())
    monkeypatch.setitem(
        sys.modules,
        "tf2_ros",
        SimpleNamespace(Buffer=MagicMock(), TransformListener=MagicMock()),
    )
    result = nav_node.RoboClawGrpcNavNode("/amcl_pose", "/test/navigate", "/lio/grid_map")
    result.test_subscriptions = subscriptions
    return result


def test_map_subscriptions_preserve_latched_and_live_delivery(node):
    subscriptions = [s for s in node.test_subscriptions if s.msg_type is OccupancyGrid]
    assert len(subscriptions) == 2
    assert {s.topic for s in subscriptions} == {"/lio/grid_map"}
    assert {s.qos_profile.durability for s in subscriptions} == {
        QoSDurabilityPolicy.TRANSIENT_LOCAL,
        QoSDurabilityPolicy.VOLATILE,
    }
    assert all(s.qos_profile.reliability == QoSReliabilityPolicy.BEST_EFFORT for s in subscriptions)
    assert all(s.qos_profile.depth == 1 for s in subscriptions)


@pytest.mark.parametrize(
    "reliability", [QoSReliabilityPolicy.RELIABLE, QoSReliabilityPolicy.BEST_EFFORT]
)
@pytest.mark.parametrize(
    "durability", [QoSDurabilityPolicy.TRANSIENT_LOCAL, QoSDurabilityPolicy.VOLATILE]
)
def test_map_is_cached_from_each_supported_publisher_qos(node, reliability, durability):
    publisher_qos = QoSProfile(depth=1, reliability=reliability, durability=durability)
    subscribers = [s for s in node.test_subscriptions if s.msg_type is OccupancyGrid]
    compatible = [
        s
        for s in subscribers
        # SYSTEM_DEFAULT liveliness can yield WARNING without incompatibility.
        if qos_check_compatible(publisher_qos, s.qos_profile)[0] != QoSCompatibility.ERROR
    ]
    assert compatible, "At least one map subscription must match the publisher"
    assert node.get_current_map() is None
    msg = OccupancyGrid()
    msg.header.frame_id = "lio_map"
    msg.info.width = 2
    msg.info.height = 1
    msg.data = [0, 100]

    for subscription in compatible:
        subscription.callback(msg)

    assert node.get_current_map() is msg
    assert node.get_current_map().header.frame_id == "lio_map"


def test_late_joiner_can_receive_transient_local_map(node):
    publisher_qos = QoSProfile(
        depth=1,
        reliability=QoSReliabilityPolicy.RELIABLE,
        durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
    )
    subscribers = [s for s in node.test_subscriptions if s.msg_type is OccupancyGrid]
    historical = [
        s
        for s in subscribers
        if s.qos_profile.durability == QoSDurabilityPolicy.TRANSIENT_LOCAL
        and qos_check_compatible(publisher_qos, s.qos_profile)[0] != QoSCompatibility.ERROR
    ]
    assert historical
    msg = OccupancyGrid()
    historical[0].callback(msg)
    assert node.get_current_map() is msg
