import sys
from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.base import BaseSkill

pytestmark = pytest.mark.unit


class DummySkill(BaseSkill):
    name = "map_freshness_dummy"

    def execute(self, params):
        return {"success": True}


@pytest.fixture
def receiver(monkeypatch):
    reliability = SimpleNamespace(RELIABLE="reliable", BEST_EFFORT="best_effort")
    durability = SimpleNamespace(TRANSIENT_LOCAL="transient_local", VOLATILE="volatile")

    class QoSProfile:
        def __init__(self, depth):
            self.depth = depth

    monkeypatch.setitem(
        sys.modules,
        "rclpy.qos",
        SimpleNamespace(
            QoSProfile=QoSProfile,
            ReliabilityPolicy=reliability,
            DurabilityPolicy=durability,
        ),
    )
    subscriptions = []
    node = SimpleNamespace(
        publishing=True,
        map_is_static=True,
        has_parameter=lambda name: name == "map_is_static",
        get_parameter=lambda name: SimpleNamespace(
            get_parameter_value=lambda: SimpleNamespace(bool_value=node.map_is_static)
        ),
        get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(nanoseconds=1000 * 10**9)),
    )

    def create_subscription(msg_type, topic, callback, qos):
        subscriptions.append((topic, callback, qos))
        if node.publishing and qos.durability == node.publisher_durability:
            callback(node.message)
        return object()

    node.create_subscription = create_subscription
    skill = DummySkill()
    skill.set_node(node)
    return skill, node, subscriptions


def sample(stamp):
    return SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=stamp, nanosec=0)))


@pytest.mark.parametrize("stamp", [100, 990, 1000, 1002])
def test_static_map_accepts_latched_samples(receiver, stamp):
    skill, node, subscriptions = receiver
    node.publisher_durability = "transient_local"
    node.message = sample(stamp)
    assert skill.wait_for_map_message("/map", timeout_sec=0.01) is node.message
    assert {topic for topic, _, _ in subscriptions} == {"/map"}


@pytest.mark.parametrize("stamp,accepted", [(989, False), (990, True), (1000, True), (1003, False)])
def test_volatile_map_checks_timestamp(receiver, stamp, accepted):
    skill, node, subscriptions = receiver
    node.publisher_durability = "volatile"
    node.message = sample(stamp)
    result = skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01)
    assert (result is node.message) is accepted
    assert {topic for topic, _, _ in subscriptions} == {"/lio/grid_map"}


def test_volatile_map_rejects_old_cached_sample_even_without_stamp(receiver, monkeypatch):
    skill, node, _ = receiver
    node.publisher_durability = "volatile"
    node.message = sample(0)
    now = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    assert skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01) is node.message
    node.publishing = False
    now[0] = 111.0
    assert skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01) is None


def test_volatile_map_reuses_recent_cached_sample(receiver, monkeypatch):
    skill, node, subscriptions = receiver
    node.publisher_durability = "volatile"
    node.message = sample(1000)
    now = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    assert skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01) is node.message
    count = len(subscriptions)
    now[0] = 109.0
    assert skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01) is node.message
    assert len(subscriptions) == count


def test_volatile_map_rejects_invalid_timestamp(receiver):
    skill, node, _ = receiver
    node.publisher_durability = "volatile"
    node.message = sample(float("nan"))
    assert skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01) is None


@pytest.mark.parametrize("durability", ["transient_local", "volatile"])
def test_dynamic_map_rejects_old_sample_regardless_of_durability(receiver, durability):
    skill, node, _ = receiver
    node.map_is_static = False
    node.publisher_durability = durability
    node.message = sample(989)
    assert skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01) is None


def test_dynamic_latched_map_rejects_old_cache_without_stamp(receiver, monkeypatch):
    skill, node, _ = receiver
    node.map_is_static = False
    node.publisher_durability = "transient_local"
    node.message = sample(0)
    now = [100.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    assert skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01) is node.message
    node.publishing = False
    now[0] = 111.0
    assert skill.wait_for_map_message("/lio/grid_map", timeout_sec=0.01) is None
