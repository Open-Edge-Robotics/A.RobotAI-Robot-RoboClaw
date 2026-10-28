"""동료 자세 공유 어댑터 단위 테스트 (ROS2 불필요).

레지스트리·메시지 변환·발행 조건을 fake 메시지로 검증한다.
"""

import time
from types import SimpleNamespace

import pytest
from robo_claw_agent.domain.peer_occupancy import PeerPoseSample
from robo_claw_agent.skills.peer_pose_sharing import (
    PeerPoseRegistry,
    blocking_peer_names,
    handle_peer_pose_message,
    publish_own_pose,
    sample_from_message,
)

pytestmark = pytest.mark.unit


def _msg(
    agent_id="Former0047",
    *,
    x=2.1,
    y=3.05,
    yaw=0.0,
    frame_id="map",
    current_skill="",
    yieldable=True,
):
    return SimpleNamespace(
        agent_id=agent_id,
        x=x,
        y=y,
        yaw=yaw,
        frame_id=frame_id,
        current_skill=current_skill,
        yieldable=yieldable,
    )


# --- 레지스트리 ---------------------------------------------------------------


def test_registry_keeps_only_the_latest_sample_per_peer():
    registry = PeerPoseRegistry()

    registry.update(PeerPoseSample("Former0047", 1.0, 1.0, 0.0, time.monotonic()))
    registry.update(PeerPoseSample("Former0047", 2.1, 3.05, 0.0, time.monotonic()))

    snapshot = registry.snapshot()
    assert len(snapshot) == 1
    assert snapshot[0].x == 2.1


def test_registry_caps_entry_count():
    registry = PeerPoseRegistry()
    now = time.monotonic()
    for i in range(40):
        registry.update(PeerPoseSample(f"peer{i:02d}", 0.0, 0.0, 0.0, now + i))

    assert len(registry.snapshot()) == 32
    assert registry.dropped_count > 0


def test_registry_ignores_empty_agent_id():
    registry = PeerPoseRegistry()

    registry.update(PeerPoseSample("", 1.0, 1.0, 0.0, time.monotonic()))

    assert registry.snapshot() == []


def test_registry_finds_blocking_peer_and_excludes_self():
    registry = PeerPoseRegistry()
    now = time.monotonic()
    registry.update(PeerPoseSample("Former0047", 2.1, 3.05, 0.0, now))
    registry.update(PeerPoseSample("Stretch3", 2.0, 3.0, 0.0, now))

    occupants = registry.blocking_peers(
        target_x=2.0, target_y=3.0, now_sec=now, exclude_agent_id="Stretch3"
    )

    assert [s.agent_id for s in occupants] == ["Former0047"]


def test_registry_drops_stale_samples():
    registry = PeerPoseRegistry()
    registry.update(PeerPoseSample("Former0047", 2.1, 3.05, 0.0, time.monotonic() - 30.0))

    occupants = registry.blocking_peers(target_x=2.0, target_y=3.0)

    assert occupants == []


# --- 메시지 변환 --------------------------------------------------------------


def test_message_is_converted_to_sample():
    sample = sample_from_message(_msg(current_skill="tidy_home", yieldable=False))

    assert sample is not None
    assert sample.agent_id == "Former0047"
    assert sample.current_skill == "tidy_home"
    assert sample.yieldable is False


def test_message_without_agent_id_is_rejected():
    assert sample_from_message(_msg(agent_id="   ")) is None


def test_malformed_message_is_rejected_without_raising():
    assert sample_from_message(SimpleNamespace(agent_id="X", x="not-a-number", y=0.0)) is None


def test_handle_message_updates_registry():
    registry = PeerPoseRegistry()

    assert handle_peer_pose_message(registry, _msg()) is True
    assert len(registry.snapshot()) == 1


def test_handle_invalid_message_returns_false():
    registry = PeerPoseRegistry()

    assert handle_peer_pose_message(registry, SimpleNamespace()) is False
    assert registry.snapshot() == []


# --- 발행 조건 ---------------------------------------------------------------


class _FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


def _node():
    return SimpleNamespace(
        _agent_id="Stretch3",
        _map_frame="map",
        _current_skill="navigate_to",
        get_clock=lambda: SimpleNamespace(now=lambda: SimpleNamespace(to_msg=lambda: "stamp")),
    )


def test_publish_skips_when_pose_is_unknown():
    publisher = _FakePublisher()

    published = publish_own_pose(
        _node(),
        publisher,
        PeerPoseRegistry(),
        pose_provider=lambda: None,
        yieldable_provider=lambda: True,
    )

    assert published is False
    assert publisher.messages == []


def test_publish_sends_own_pose():
    publisher = _FakePublisher()
    from robo_claw_agent.domain.yield_slot import Pose2D

    published = publish_own_pose(
        _node(),
        publisher,
        PeerPoseRegistry(),
        pose_provider=lambda: Pose2D(1.0, 2.0, 0.5),
        yieldable_provider=lambda: False,
    )

    assert published is True
    assert len(publisher.messages) == 1
    message = publisher.messages[0]
    assert message.agent_id == "Stretch3"
    assert message.yieldable is False
    assert message.current_skill == "navigate_to"
    assert message.header.stamp == "stamp"


def test_blocking_peer_names_helper_excludes_self():
    registry = PeerPoseRegistry()
    now = time.monotonic()
    registry.update(PeerPoseSample("Former0047", 2.05, 3.0, 0.0, now))

    names = blocking_peer_names(registry, target_x=2.0, target_y=3.0, agent_id="Stretch3")

    assert names == ["Former0047"]
