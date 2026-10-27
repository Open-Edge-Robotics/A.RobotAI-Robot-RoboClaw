"""마지막 위치(last pose) 기억·복원 테스트.

AMCL 초기 위치를 잃었을 때 "마지막 위치를 초기 위치로" 요청을 처리하기 위한
RAG 레코드(type=last_pose)와 스냅샷 트래커를 검증한다.
"""

import math
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.last_pose_tracker import (
    LastPoseTracker,
    read_map_pose,
    read_tracked_or_stored_pose,
)
from robo_claw_agent.memory_manager.last_pose import (
    LAST_POSE_TYPE,
    format_last_pose_text,
    is_last_pose_request,
    read_last_pose,
    remember_last_pose,
)

pytestmark = pytest.mark.unit


class _Store:
    def __init__(self, entries=None):
        self.entries = list(entries or [])
        self.delete_calls: list[list[str]] = []

    def list_entries(self, with_vectors=False):
        return [dict(entry) for entry in self.entries]

    def delete_entries(self, *, ids=None, source_path="", text_exact=""):
        targets = {str(i) for i in (ids or [])}
        self.delete_calls.append(sorted(targets))
        before = len(self.entries)
        self.entries = [e for e in self.entries if str(e.get("id")) not in targets]
        return before - len(self.entries)


class _Memory:
    def __init__(self, store=None, objects=None):
        self._vector_store = store if store is not None else _Store()
        self._objects = list(objects or [])
        self.added: list[tuple[str, dict]] = []

    def add_knowledge(self, text, metadata=None):
        self.added.append((text, dict(metadata or {})))
        self._vector_store.entries.append(
            {"id": f"new-{len(self.added)}", "text": text, "metadata": dict(metadata or {})}
        )
        return True

    def get_nearest_object(self, x, y, radius_m=None):
        for obj in self._objects:
            return obj
        return None


def _last_pose_entry(x=1.5, y=-2.0, yaw=0.5, entry_id="lp-1", near_place="거실"):
    entry = {
        "id": entry_id,
        "text": format_last_pose_text(x, y, yaw, near_place),
        "metadata": {
            "type": LAST_POSE_TYPE,
            "x": x,
            "y": y,
            "yaw": yaw,
            "frame": "map",
            "near_place": near_place,
            "timestamp": "2026-01-01T00:00:00",
        },
    }
    return entry


def test_last_pose_aliases_are_recognized():
    for alias in ["마지막 위치", "마지막위치", "마지막 장소", "직전 위치", "last pose"]:
        assert is_last_pose_request(alias) is True
    for other in ["안방", "거실", "", "마지막"]:
        assert is_last_pose_request(other) is False


def test_remember_last_pose_keeps_single_record():
    store = _Store(
        entries=[
            {"id": "loc-1", "text": "거실의 위치는 x: 1.0, y: 2.0", "metadata": {"type": "location"}},
            _last_pose_entry(entry_id="old-last"),
        ]
    )
    memory = _Memory(store)

    ok = remember_last_pose(memory, x=3.25, y=-4.5, yaw=1.5708, near_place="안방")

    assert ok is True
    # 기존 last_pose 항목만 지우고 장소 지식은 건드리지 않는다.
    assert store.delete_calls == [["old-last"]]
    assert [e["id"] for e in store.entries] == ["loc-1", "new-1"]
    text, metadata = memory.added[0]
    assert metadata["type"] == LAST_POSE_TYPE
    assert metadata["x"] == pytest.approx(3.25)
    assert metadata["y"] == pytest.approx(-4.5)
    assert metadata["yaw"] == pytest.approx(1.5708)
    assert metadata["near_place"] == "안방"
    # 장소명 키로는 저장하지 않는다(장소 지식으로 오인되지 않도록).
    assert "place_name" not in metadata and "name" not in metadata
    assert "90.0°" in text


def test_read_last_pose_returns_latest_and_falls_back_to_text():
    store = _Store(
        entries=[
            _last_pose_entry(x=1.0, y=1.0, yaw=0.0, entry_id="lp-1"),
            # metadata 에 좌표가 없고 텍스트만 있는 항목(구버전/수동 저장)도 읽는다.
            {
                "id": "lp-2",
                "text": "마지막 위치: x=7.5, y=-8.25, yaw=3.14rad (180.0°)",
                "metadata": {"type": LAST_POSE_TYPE, "frame": "map"},
            },
        ]
    )

    pose = read_last_pose(_Memory(store))

    assert pose is not None
    assert pose["id"] == "lp-2"
    assert (pose["x"], pose["y"]) == (pytest.approx(7.5), pytest.approx(-8.25))
    assert pose["yaw"] == pytest.approx(3.14)


def test_read_last_pose_returns_none_when_absent():
    store = _Store(entries=[{"id": "loc-1", "text": "거실", "metadata": {"type": "location"}}])
    assert read_last_pose(_Memory(store)) is None
    assert read_last_pose(SimpleNamespace()) is None


def test_remember_last_pose_rejects_non_finite():
    memory = _Memory()
    assert remember_last_pose(memory, x=float("inf"), y=0.0, yaw=0.0) is False
    assert memory.added == []


def _node_with_tf(x, y, yaw, frame="map"):
    half = yaw / 2.0
    rotation = SimpleNamespace(x=0.0, y=0.0, z=math.sin(half), w=math.cos(half))
    transform = SimpleNamespace(
        transform=SimpleNamespace(
            translation=SimpleNamespace(x=x, y=y, z=0.0),
            rotation=rotation,
        )
    )
    tf_buffer = SimpleNamespace(lookup_transform=lambda *args, **kwargs: transform)
    return SimpleNamespace(_tf_buffer=tf_buffer, _map_frame=frame, _robot_base_frame="base_link")


def test_read_map_pose_uses_tf_only():
    pose = read_map_pose(_node_with_tf(1.5, -2.0, 1.5708))
    assert pose is not None
    assert pose["frame"] == "map"
    assert pose["x"] == pytest.approx(1.5)
    assert pose["yaw"] == pytest.approx(1.5708)

    # TF 가 없으면 odom 으로 대체하지 않는다(프레임이 달라 초기 위치로 쓸 수 없다).
    assert read_map_pose(SimpleNamespace(_tf_buffer=None)) is None


def test_read_map_pose_returns_none_when_lookup_fails():
    def _boom(*args, **kwargs):
        raise RuntimeError("no transform")

    node = SimpleNamespace(_tf_buffer=SimpleNamespace(lookup_transform=_boom))
    assert read_map_pose(node) is None


def test_tracker_persists_on_change_and_throttles():
    now = [0.0]
    memory = _Memory()
    tracker = LastPoseTracker(clock=lambda: now[0])

    # 첫 스냅샷은 저장.
    assert tracker.update({"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"}, memory) is True
    assert len(memory.added) == 1
    saved_y = memory.added[0][1]["y"]
    assert saved_y == pytest.approx(2.0)

    # 같은 자리에서 시간만 흐르면 쓰지 않는다.
    now[0] += 60.0
    tracker._persisted = None  # noqa: SLF001 - 간격 규칙 검증을 위해 상태 초기화
    tracker.update({"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"}, memory)
    assert len(memory.added) == 2

    # 임계 거리 미만 이동은 저장하지 않는다.
    now[0] += 1.0
    assert tracker.update({"x": 1.1, "y": 2.1, "yaw": 0.0, "frame": "map"}, memory) is False
    assert len(memory.added) == 2

    # 임계 거리 이상 이동하면 저장.
    assert tracker.update({"x": 2.0, "y": 2.0, "yaw": 0.0, "frame": "map"}, memory) is True
    assert len(memory.added) == 3


def test_tracker_skips_non_map_frames():
    memory = _Memory()
    tracker = LastPoseTracker()
    assert tracker.update({"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "odom"}, memory) is False
    assert memory.added == []
    assert tracker.latest is None


def test_tracker_remembers_near_place_name():
    memory = _Memory(objects=[{"name": "안방"}])
    tracker = LastPoseTracker()
    tracker.update({"x": 3.2, "y": -1.4, "yaw": 0.0, "frame": "map"}, memory)
    assert memory.added[0][1]["near_place"] == "안방"
    assert "안방 부근" in memory.added[0][0]


def test_tracked_pose_prefers_in_memory_then_rag():
    memory = _Memory(_Store(entries=[_last_pose_entry(x=9.0, y=9.0, yaw=0.0)]))
    node = SimpleNamespace(_memory=memory, _last_pose_tracker=None)

    # 트래커가 없으면 RAG 기록을 쓴다.
    stored = read_tracked_or_stored_pose(node)
    assert stored is not None and stored["x"] == pytest.approx(9.0)

    # 트래커가 있으면 메모리 최신값이 우선(네비 재기동 직전 pose).
    tracker = LastPoseTracker()
    tracker.update({"x": 4.0, "y": 5.0, "yaw": 0.25, "frame": "map"}, memory)
    node._last_pose_tracker = tracker
    latest = read_tracked_or_stored_pose(node)
    assert latest is not None
    assert (latest["x"], latest["y"], latest["yaw"]) == (
        pytest.approx(4.0),
        pytest.approx(5.0),
        pytest.approx(0.25),
    )


def test_setup_last_pose_tracking_registers_timer_and_persists_rag():
    from robo_claw_agent.agent_node.initialization import setup_last_pose_tracking

    memory = _Memory()
    timers: list = []
    tf_buffer = _node_with_tf(2.0, 3.0, 0.0)._tf_buffer
    node = SimpleNamespace(
        _enable_rag=True,
        _memory=memory,
        _tf_buffer=tf_buffer,
        _map_frame="map",
        _robot_base_frame="base_link",
        create_timer=lambda period, callback, callback_group=None: timers.append((period, callback)),
        get_logger=lambda: SimpleNamespace(info=lambda *_: None, warning=lambda *_: None),
    )

    setup_last_pose_tracking(node)

    assert node._last_pose_tracker is not None
    assert timers, "마지막 위치 타이머가 등록되어야 한다"
    assert timers[0][0] > 0

    # 타이머 콜백이 실제로 스냅샷 → RAG 저장(백그라운드)까지 수행한다.
    timers[0][1]()
    assert node._last_pose_tracker.wait_for_pending() is True
    assert memory.added, "타이머 콜백이 마지막 위치를 저장해야 한다"
    assert memory.added[0][1]["x"] == pytest.approx(2.0)


def test_setup_last_pose_tracking_keeps_memory_without_rag():
    from robo_claw_agent.agent_node.initialization import setup_last_pose_tracking

    memory = _Memory()
    timers: list = []
    node = SimpleNamespace(
        _enable_rag=False,
        _memory=memory,
        _tf_buffer=_node_with_tf(1.0, 1.0, 0.0)._tf_buffer,
        _map_frame="map",
        _robot_base_frame="base_link",
        create_timer=lambda period, callback, callback_group=None: timers.append((period, callback)),
        get_logger=lambda: SimpleNamespace(info=lambda *_: None, warning=lambda *_: None),
    )

    setup_last_pose_tracking(node)
    timers[0][1]()

    # RAG 저장은 하지 않지만, 메모리 최신 pose 는 유지한다.
    assert memory.added == []
    assert node._last_pose_tracker.latest["x"] == pytest.approx(1.0)


def test_setup_last_pose_tracking_disabled_without_tf():
    from robo_claw_agent.agent_node.initialization import setup_last_pose_tracking

    timers: list = []
    node = SimpleNamespace(
        _enable_rag=True,
        _memory=_Memory(),
        _tf_buffer=None,
        create_timer=lambda period, callback, callback_group=None: timers.append((period, callback)),
        get_logger=lambda: SimpleNamespace(info=lambda *_: None, warning=lambda *_: None),
    )

    setup_last_pose_tracking(node)

    assert node._last_pose_tracker is None
    assert timers == []
