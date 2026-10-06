import math
import sys
from types import SimpleNamespace
from typing import Any

import pytest
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.map_skill.capture import CaptureMapSkill
from robo_claw_agent.skills.perception_skill.core import (
    _distance_at_bearing,
)
from robo_claw_agent.skills.perception_skill.distance import GetDistanceSkill


class DummySkill(BaseSkill):
    name = "dummy"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": True}

    def execute(self, params):
        return {"success": True}


@pytest.mark.unit
def test_wait_for_message_rejects_stale_timestamp(monkeypatch):
    skill = DummySkill()
    current_time_sec = 100.0
    node = SimpleNamespace(
        get_clock=lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(
                nanoseconds=int(current_time_sec * 1e9),
                to_msg=lambda: SimpleNamespace(sec=int(current_time_sec), nanosec=0),
            )
        ),
        create_subscription=lambda msg_type, topic, cb, qos: None,
        destroy_subscription=lambda sub: None,
    )
    skill.set_node(node)

    # Message from 20 seconds ago (stamp=80.0)
    stale_msg = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=80, nanosec=0)))

    def fake_create_sub(msg_type, topic, cb, qos):
        cb(stale_msg)
        return object()

    monkeypatch.setattr(node, "create_subscription", fake_create_sub)

    # With max_age_sec=5.0, stale_msg should not be accepted
    res = skill.wait_for_message(object, "/test_topic", timeout_sec=0.05, max_age_sec=5.0)
    assert res is None


@pytest.mark.unit
def test_wait_for_message_accepts_fresh_timestamp(monkeypatch):
    skill = DummySkill()
    current_time_sec = 100.0
    node = SimpleNamespace(
        get_clock=lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(
                nanoseconds=int(current_time_sec * 1e9),
                to_msg=lambda: SimpleNamespace(sec=int(current_time_sec), nanosec=0),
            )
        ),
        create_subscription=lambda msg_type, topic, cb, qos: None,
        destroy_subscription=lambda sub: None,
    )
    skill.set_node(node)

    # Message from 1 second ago (stamp=99.0)
    fresh_msg = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=99, nanosec=0)))

    def fake_create_sub(msg_type, topic, cb, qos):
        cb(fresh_msg)
        return object()

    monkeypatch.setattr(node, "create_subscription", fake_create_sub)

    res = skill.wait_for_message(object, "/test_topic", timeout_sec=0.5, max_age_sec=5.0)
    assert res is fresh_msg


@pytest.mark.unit
def test_wait_for_message_accepts_old_latched_message(monkeypatch):
    """TRANSIENT_LOCAL(latched) 토픽은 마지막 샘플을 그대로 전달하므로
    max_age_sec보다 오래된 메시지라도 정상 수신으로 취급해야 한다.

    nav2 map_server / slam_toolbox 의 /map 은 activate 시 한 번 발행되어
    latched 되므로, 에이전트가 뒤늦게 구독하면 메시지 stamp 는 수십 초~수 분
    전일 수 있다. 이때 freshness 필터를 적용하면 정상 맵을 버리고 timeout 된다.
    """
    skill = DummySkill()
    current_time_sec = 1000.0
    node = SimpleNamespace(
        get_clock=lambda: SimpleNamespace(
            now=lambda: SimpleNamespace(
                nanoseconds=int(current_time_sec * 1e9),
                to_msg=lambda: SimpleNamespace(sec=int(current_time_sec), nanosec=0),
            )
        ),
        create_subscription=lambda msg_type, topic, cb, qos: None,
        destroy_subscription=lambda sub: None,
    )
    skill.set_node(node)

    # 900초 전에 발행되어 latched 된 정적 맵
    latched_map = SimpleNamespace(header=SimpleNamespace(stamp=SimpleNamespace(sec=100, nanosec=0)))

    def fake_create_sub(msg_type, topic, cb, qos):
        cb(latched_map)
        return object()

    monkeypatch.setattr(node, "create_subscription", fake_create_sub)

    res = skill.wait_for_message(
        object,
        "/map",
        timeout_sec=0.05,
        use_transient_local=True,
        max_age_sec=10.0,
    )

    assert res is latched_map


@pytest.mark.unit
def test_wait_for_message_falls_back_for_volatile_map_publishers(monkeypatch):
    """TRANSIENT_LOCAL 요청으로도 VOLATILE 맵 publisher를 수신해야 한다."""
    reliability = SimpleNamespace(RELIABLE="reliable", BEST_EFFORT="best_effort")
    durability = SimpleNamespace(TRANSIENT_LOCAL="transient_local", VOLATILE="volatile")

    class FakeQoSProfile:
        def __init__(self, depth):
            self.depth = depth
            self.reliability = None
            self.durability = None

    monkeypatch.setitem(
        sys.modules,
        "rclpy.qos",
        SimpleNamespace(
            DurabilityPolicy=durability,
            QoSProfile=FakeQoSProfile,
            ReliabilityPolicy=reliability,
        ),
    )

    skill = DummySkill()
    node = SimpleNamespace(
        create_subscription=lambda *args: None,
        destroy_subscription=lambda sub: None,
    )
    skill.set_node(node)
    map_msg = SimpleNamespace()
    profiles = []

    def fake_create_sub(msg_type, topic, cb, qos):
        profiles.append((qos.reliability, qos.durability))
        if qos.durability == durability.VOLATILE:
            cb(map_msg)
        return object()

    monkeypatch.setattr(node, "create_subscription", fake_create_sub)

    result = skill.wait_for_message(
        object,
        "/map",
        timeout_sec=0.05,
        use_transient_local=True,
        max_age_sec=10.0,
    )

    assert result is map_msg
    assert (reliability.RELIABLE, durability.TRANSIENT_LOCAL) in profiles
    assert (reliability.RELIABLE, durability.VOLATILE) in profiles


@pytest.mark.unit
def test_wait_for_message_reuses_subscription_without_destroying():
    """wait_for_message는 임시 구독을 매 호출마다 destroy하지 않는다.

    MultiThreadedExecutor가 이미 작업 큐에 넣은 take 핸들러가 destroy된
    subscription handle을 사용하면 ``InvalidHandle: cannot use Destroyable``
    예외로 executor.spin()이 종료되고 노드 전체가 죽는다. 따라서 동일한
    (msg_type, topic, QoS) 구독은 재사용하고 destroy하지 않아야 한다.
    """
    import threading

    skill = DummySkill()
    created: list[str] = []
    destroyed: list[object] = []
    current: dict[str, Any] = {"cb": None}

    def fake_create_sub(msg_type, topic, cb, qos):
        created.append(topic)
        current["cb"] = cb
        return object()

    node = SimpleNamespace(
        create_subscription=fake_create_sub,
        destroy_subscription=lambda sub: destroyed.append(sub),
    )
    skill.set_node(node)

    def deliver_later(msg, delay=0.01):
        threading.Timer(delay, lambda: current["cb"](msg)).start()

    first = SimpleNamespace()
    deliver_later(first)
    assert skill.wait_for_message(object, "/test_topic", timeout_sec=0.5) is first
    assert created == ["/test_topic"]
    assert destroyed == []

    # 같은 토픽/QoS의 두 번째 호출은 캐시된 구독을 재사용하고 destroy하지 않는다.
    second = SimpleNamespace()
    deliver_later(second)
    assert skill.wait_for_message(object, "/test_topic", timeout_sec=0.5) is second
    assert created == ["/test_topic"]
    assert destroyed == []


@pytest.mark.unit
def test_wait_for_message_replays_cached_latched_message():
    """TRANSIENT_LOCAL 토픽은 재구독하지 않고 캐시된 마지막 샘플을 재생한다.

    latched 샘플은 구독 매칭 시점에 한 번만 전달되므로, 구독을 재사용하면
    두 번째 호출에서 DDS가 다시 보내주지 않는다. 캐시된 마지막 메시지를
    재생하지 않으면 정적 /map 이 두 번째부터 timeout 된다.
    """
    skill = DummySkill()
    created: list[object] = []
    latched = SimpleNamespace()

    def fake_create_sub(msg_type, topic, cb, qos):
        created.append(cb)
        cb(latched)  # TRANSIENT_LOCAL late-joiner 전달
        return object()

    node = SimpleNamespace(
        create_subscription=fake_create_sub,
        destroy_subscription=lambda sub: None,
    )
    skill.set_node(node)

    assert (
        skill.wait_for_message(object, "/map", timeout_sec=0.1, use_transient_local=True) is latched
    )
    created_after_first = len(created)

    # 두 번째 호출: 새 구독 없이 캐시된 latched 샘플을 재생해야 한다.
    assert (
        skill.wait_for_message(object, "/map", timeout_sec=0.1, use_transient_local=True) is latched
    )
    assert len(created) == created_after_first


@pytest.mark.unit
def test_laser_scan_no_valid_samples_returns_none():
    scan = SimpleNamespace(
        angle_min=-math.pi,
        angle_max=math.pi,
        range_min=0.1,
        range_max=10.0,
        ranges=[float("nan")] * 100,
    )
    dist = _distance_at_bearing(scan, 0.0)
    assert dist is None


@pytest.mark.unit
def test_get_distance_skill_handles_invalid_laser_samples(monkeypatch):
    skill = GetDistanceSkill()
    node = SimpleNamespace()
    skill.set_node(node)

    scan = SimpleNamespace(
        angle_min=-math.pi,
        angle_max=math.pi,
        range_min=0.1,
        range_max=10.0,
        ranges=[float("nan")] * 100,
    )
    monkeypatch.setattr(skill, "wait_for_message", lambda *args, **kwargs: scan)

    res = skill.execute({"direction": "front"})
    assert res["success"] is False
    assert "유효한" in res["message"] or "측정값" in res["message"] or "없습니다" in res["message"]


@pytest.mark.unit
def test_occupancy_grid_malformed_data_rejected(monkeypatch):
    skill = CaptureMapSkill()
    node = SimpleNamespace()
    skill.set_node(node)

    # Malformed OccupancyGrid where data size != width * height
    map_msg = SimpleNamespace(
        info=SimpleNamespace(
            width=10,
            height=10,
            resolution=0.05,
            origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0, z=0.0)),
        ),
        data=[0] * 50,  # Expected 100
    )
    monkeypatch.setattr(skill, "wait_for_message", lambda *args, **kwargs: map_msg)

    res = skill.execute({"map_topic": "/map"})
    assert res["success"] is False
    assert (
        "OccupancyGrid" in res["message"]
        or "일치하지" in res["message"]
        or "데이터" in res["message"]
    )


@pytest.mark.unit
def test_occupancy_grid_frame_mismatch_is_rejected(monkeypatch):
    skill = CaptureMapSkill()
    node = SimpleNamespace(_map_frame="map")
    skill.set_node(node)

    map_msg = SimpleNamespace(
        header=SimpleNamespace(frame_id="odom"),
        info=SimpleNamespace(
            width=1,
            height=1,
            resolution=0.05,
            origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0, z=0.0)),
        ),
        data=[0],
    )
    monkeypatch.setattr(skill, "wait_for_message", lambda *args, **kwargs: map_msg)

    res = skill.execute({"map_topic": "/map"})

    assert res["success"] is False
    assert "frame_id" in res["message"]


@pytest.mark.unit
def test_capture_map_image_write_failure(monkeypatch):
    skill = CaptureMapSkill()
    node = SimpleNamespace()
    skill.set_node(node)

    map_msg = SimpleNamespace(
        info=SimpleNamespace(
            width=5,
            height=5,
            resolution=0.05,
            origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0, z=0.0)),
        ),
        data=[0] * 25,
    )
    monkeypatch.setattr(skill, "wait_for_message", lambda *args, **kwargs: map_msg)
    monkeypatch.setattr("cv2.imwrite", lambda path, img: False)

    res = skill.execute({"map_topic": "/map"})
    assert res["success"] is False
    assert "저장" in res["message"] or "실패" in res["message"]
