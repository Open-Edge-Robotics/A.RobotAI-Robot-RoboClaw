"""마지막 위치(last pose) 기억·복원 테스트.

메모리 디렉터리의 `last_pose.yaml` 파일 저장/읽기와 스냅샷 트래커를 검증한다.
former nav2 기동 스크립트가 같은 파일을 AMCL 초기 위치로 쓴다.
"""

import math
import os
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.last_pose_tracker import (
    LastPoseTracker,
    MapNameQuery,
    amcl_std_from_covariance,
    read_map_pose,
    read_tracked_or_stored_pose,
    snapshot_tick,
)
from robo_claw_agent.memory_manager.last_pose import (
    LAST_POSE_FILENAME,
    format_last_pose_yaml,
    is_last_pose_request,
    parse_last_pose_yaml,
    read_last_pose_file,
    resolve_last_pose_path,
    write_last_pose_file,
)

pytestmark = pytest.mark.unit


class _Memory:
    def __init__(self, objects=None):
        self._objects = list(objects or [])

    def get_nearest_object(self, x, y, radius_m=None):
        for obj in self._objects:
            return obj
        return None


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


# --- 파일 형식 -------------------------------------------------------------------


def test_last_pose_aliases_are_recognized():
    for alias in ("마지막 위치", "마지막위치", "직전 위치", "last pose", "Last Position"):
        assert is_last_pose_request(alias) is True
    for other in ("안방", "", "마지막", "거실 위치"):
        assert is_last_pose_request(other) is False


def test_resolve_last_pose_path_uses_memory_dir():
    assert resolve_last_pose_path("/ros2_ws/memory/memory.json") == (
        f"/ros2_ws/memory/{LAST_POSE_FILENAME}"
    )
    assert resolve_last_pose_path("") == ""
    assert resolve_last_pose_path(None) == ""


def test_format_and_parse_round_trip_keeps_unicode_and_map():
    text = format_last_pose_yaml(
        x=1.23456,
        y=-2.5,
        yaw=1.5708,
        map_name="map_konjiam_L_107-1.yaml",
        near_place='거실: "소파" 앞',
        updated_at="2026-11-20T10:00:00+09:00",
    )
    # bash 쪽 파서가 기대하는 평면 key: value 줄이 있어야 한다.
    assert "\nx: 1.235\n" in text
    assert '\nmap: "map_konjiam_L_107-1.yaml"\n' in text

    pose = parse_last_pose_yaml(text)
    assert pose is not None
    assert (pose["x"], pose["y"], pose["yaw"]) == (
        pytest.approx(1.235),
        pytest.approx(-2.5),
        pytest.approx(1.5708),
    )
    assert pose["map"] == "map_konjiam_L_107-1.yaml"
    assert pose["near_place"] == '거실: "소파" 앞'
    assert pose["frame"] == "map"


@pytest.mark.parametrize(
    "text",
    ["", "garbage", "x: 1.0\n", "x: nan\ny: 1.0\n", "x: 1.0\ny: abc\n", 'x: 1\ny: 2\nmap: "bad\n'],
)
def test_parse_rejects_incomplete_or_invalid(text):
    pose = parse_last_pose_yaml(text)
    if text.startswith("x: 1\ny: 2"):
        # 깨진 문자열 줄은 건너뛰고 좌표는 살린다.
        assert pose is not None and pose["map"] == ""
    else:
        assert pose is None


def test_write_then_read_file_atomically(tmp_path):
    path = str(tmp_path / "memory" / LAST_POSE_FILENAME)
    assert write_last_pose_file(path, x=3.25, y=-4.5, yaw=1.5708, map_name="w2_5f.yaml") is True
    assert oct(os.stat(path).st_mode & 0o777) == "0o644"
    # 임시 파일이 남지 않는다.
    assert sorted(os.listdir(tmp_path / "memory")) == [LAST_POSE_FILENAME]

    pose = read_last_pose_file(path)
    assert pose is not None
    assert pose["x"] == pytest.approx(3.25)
    assert pose["map"] == "w2_5f.yaml"
    assert pose["updated_at"]

    # 덮어쓰기
    assert write_last_pose_file(path, x=1.0, y=1.0, yaw=0.0) is True
    assert read_last_pose_file(path)["x"] == pytest.approx(1.0)


def test_write_rejects_non_finite_and_empty_path(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    assert write_last_pose_file(path, x=float("inf"), y=0.0, yaw=0.0) is False
    assert write_last_pose_file("", x=0.0, y=0.0, yaw=0.0) is False
    assert not os.path.exists(path)


def test_read_missing_or_corrupt_file_returns_none(tmp_path):
    assert read_last_pose_file(str(tmp_path / "missing.yaml")) is None
    assert read_last_pose_file("") is None
    corrupt = tmp_path / LAST_POSE_FILENAME
    corrupt.write_text("x: 1.0\n", encoding="utf-8")
    assert read_last_pose_file(str(corrupt)) is None


# --- TF / AMCL ---------------------------------------------------------------------


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


def test_amcl_std_from_covariance():
    cov = [0.0] * 36
    cov[0], cov[7], cov[35] = 0.09, 0.16, 0.04
    xy_std, yaw_std = amcl_std_from_covariance(cov)
    assert xy_std == pytest.approx(0.5)
    assert yaw_std == pytest.approx(0.2)
    assert amcl_std_from_covariance([]) is None


# --- 트래커 ---------------------------------------------------------------------------


def _good(tracker):
    tracker.set_localization_std(0.1, 0.1)
    return tracker


def test_tracker_persists_on_movement_and_throttles(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    now = [0.0]
    tracker = _good(LastPoseTracker(pose_file=path, clock=lambda: now[0]))

    # 첫 스냅샷은 저장.
    assert tracker.update({"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"}) is True
    assert read_last_pose_file(path)["y"] == pytest.approx(2.0)

    # 정지 상태에서는 시간이 흘러도 다시 쓰지 않는다(저장 매체 쓰기 절감).
    now[0] += 60.0
    assert tracker.update({"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"}) is False

    # 임계 거리 이상 움직여도 최소 간격 전이면 쓰지 않는다.
    assert tracker.update({"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"}) is False
    now[0] += 1.0
    tracker._last_persist_ts = now[0]  # noqa: SLF001 - 간격 규칙 검증
    assert tracker.update({"x": 2.0, "y": 2.0, "yaw": 0.0, "frame": "map"}) is False

    # 간격이 지나면 저장.
    now[0] += 20.0
    assert tracker.update({"x": 2.0, "y": 2.0, "yaw": 0.0, "frame": "map"}) is True
    assert read_last_pose_file(path)["x"] == pytest.approx(2.0)

    # 회전만 해도 임계 이상이면 저장.
    now[0] += 20.0
    assert tracker.update({"x": 2.0, "y": 2.0, "yaw": 1.0, "frame": "map"}) is True


def test_tracker_skips_non_map_frames(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    tracker = _good(LastPoseTracker(pose_file=path))
    assert tracker.update({"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "odom"}) is False
    assert tracker.latest is None
    assert not os.path.exists(path)


def test_tracker_requires_converged_amcl(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    tracker = LastPoseTracker(pose_file=path, require_localization=True)
    pose = {"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"}

    # 수렴 여부를 모르면 메모리 값만 갱신하고 파일에는 쓰지 않는다.
    assert tracker.update(pose) is False
    assert tracker.latest is not None
    assert not os.path.exists(path)

    # 미수렴(공분산 큼)이면 메모리 값도 갱신하지 않는다(틀린 위치 방지).
    tracker.set_localization_std(1.2, 0.1)
    assert tracker.update({"x": 9.0, "y": 9.0, "yaw": 0.0, "frame": "map"}) is False
    assert tracker.latest["x"] == pytest.approx(1.0)

    tracker.set_localization_std(0.2, 0.1)
    assert tracker.update(pose) is True
    assert os.path.exists(path)


def test_tracker_waits_for_map_name_and_rewrites_on_map_change(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    now = [0.0]
    tracker = _good(LastPoseTracker(pose_file=path, clock=lambda: now[0]))
    pose = {"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"}

    tracker.invalidate_map()
    assert tracker.update(pose) is False  # 맵 이름 조회 중에는 저장을 미룬다.

    tracker.set_map_name("/ws/maps/w2_5f.yaml")
    assert tracker.map_name == "w2_5f.yaml"
    assert tracker.update(pose) is True
    assert read_last_pose_file(path)["map"] == "w2_5f.yaml"

    # 같은 자리라도 맵이 바뀌면 바로 다시 쓴다.
    now[0] += 1.0
    tracker.set_map_name("w2_2f.yaml")
    assert tracker.update(pose) is True
    assert read_last_pose_file(path)["map"] == "w2_2f.yaml"


def test_tracker_without_file_keeps_latest_only():
    tracker = _good(LastPoseTracker())
    assert tracker.persist_enabled is False
    assert tracker.update({"x": 1.0, "y": 1.0, "yaw": 0.0, "frame": "map"}) is False
    assert tracker.latest["x"] == pytest.approx(1.0)


def test_tracker_remembers_near_place_name(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    tracker = _good(LastPoseTracker(pose_file=path))
    tracker.update({"x": 3.2, "y": -1.4, "yaw": 0.0, "frame": "map"}, _Memory([{"name": "안방"}]))
    assert read_last_pose_file(path)["near_place"] == "안방"


def test_tracked_pose_prefers_in_memory_then_file(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    write_last_pose_file(path, x=9.0, y=9.0, yaw=0.0, map_name="w2_5f.yaml")
    node = SimpleNamespace(_last_pose_file=path, _last_pose_tracker=None)

    # 트래커가 없으면 파일 기록을 쓴다.
    stored = read_tracked_or_stored_pose(node)
    assert stored is not None and stored["x"] == pytest.approx(9.0)

    # 트래커가 있으면 메모리 최신값이 우선(네비 재기동 직전 pose).
    tracker = _good(LastPoseTracker())
    tracker.update({"x": 4.0, "y": 5.0, "yaw": 0.25, "frame": "map"})
    node._last_pose_tracker = tracker
    latest = read_tracked_or_stored_pose(node)
    assert (latest["x"], latest["y"], latest["yaw"]) == (
        pytest.approx(4.0),
        pytest.approx(5.0),
        pytest.approx(0.25),
    )


def test_tracked_pose_ignores_file_from_other_map(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    write_last_pose_file(path, x=9.0, y=9.0, yaw=0.0, map_name="w2_5f.yaml")
    tracker = LastPoseTracker(pose_file=path)
    tracker.set_map_name("map_konjiam_L_107-1.yaml")
    node = SimpleNamespace(_last_pose_file=path, _last_pose_tracker=tracker)
    assert read_tracked_or_stored_pose(node) is None


# --- 맵 이름 조회 / 타이머 ---------------------------------------------------------


class _Future:
    def __init__(self, result=None):
        self._result = result
        self._done = result is not None
        self.cancelled = False

    def done(self):
        return self._done

    def result(self):
        return self._result

    def cancel(self):
        self.cancelled = True


class _Client:
    def __init__(self, ready=True, future=None):
        self.ready = ready
        self.future = future
        self.requests = []

    def service_is_ready(self):
        return self.ready

    def call_async(self, request):
        self.requests.append(request)
        return self.future


def _query_node(client):
    return SimpleNamespace(create_client=lambda *_args, **_kw: client)


def test_map_name_query_sets_basename_from_param():
    value = SimpleNamespace(string_value="/ws/maps/map_konjiam_L_107-1.yaml")
    client = _Client(future=_Future(SimpleNamespace(values=[value])))
    tracker = LastPoseTracker()
    tracker.invalidate_map()
    query = MapNameQuery(_query_node(client), tracker)

    query.poll()  # 요청 전송
    assert list(client.requests[0].names) == ["yaml_filename"]
    query.poll()  # 결과 반영
    assert tracker.map_name == "map_konjiam_L_107-1.yaml"


def test_map_name_query_gives_up_when_service_missing():
    client = _Client(ready=False)
    tracker = LastPoseTracker()
    tracker.invalidate_map()
    query = MapNameQuery(_query_node(client), tracker, max_wait_polls=2)

    query.poll()
    assert tracker.map_name is None
    query.poll()
    # 확인 불가("")로 두어 저장 자체는 막지 않는다.
    assert tracker.map_name == ""


def test_map_name_query_times_out():
    now = [0.0]
    future = _Future()
    client = _Client(future=future)
    tracker = LastPoseTracker()
    tracker.invalidate_map()
    query = MapNameQuery(_query_node(client), tracker, timeout_sec=3.0, clock=lambda: now[0])

    query.poll()
    now[0] += 5.0
    query.poll()
    assert future.cancelled is True
    assert tracker.map_name == ""


def test_snapshot_tick_resets_state_when_tf_lost(tmp_path):
    path = str(tmp_path / LAST_POSE_FILENAME)
    tracker = _good(LastPoseTracker(pose_file=path))
    tracker.set_map_name("w2_5f.yaml")
    query = MapNameQuery(_query_node(_Client(ready=False)), tracker)

    assert snapshot_tick(SimpleNamespace(_tf_buffer=None), tracker, query) is False
    assert tracker.map_name is None
    assert tracker.localization_state() == "unknown"


def _setup_node(tf_buffer, timers, subs):
    return SimpleNamespace(
        _memory=None,
        _tf_buffer=tf_buffer,
        _map_frame="map",
        _robot_base_frame="base_link",
        create_timer=lambda period, callback, callback_group=None: timers.append(
            (period, callback)
        ),
        create_subscription=lambda msg_type, topic, cb, qos, callback_group=None: (
            subs.append((topic, cb)) or object()
        ),
        create_client=lambda *_args, **_kw: _Client(ready=False),
        get_logger=lambda: SimpleNamespace(info=lambda *_: None, warning=lambda *_: None),
    )


def test_setup_last_pose_tracking_writes_file_in_memory_dir(tmp_path):
    from robo_claw_agent.agent_node.initialization import setup_last_pose_tracking

    timers: list = []
    subs: list = []
    node = _setup_node(_node_with_tf(2.0, 3.0, 0.0)._tf_buffer, timers, subs)
    params = SimpleNamespace(memory_path=str(tmp_path / "memory.json"))

    setup_last_pose_tracking(node, params)

    expected = str(tmp_path / LAST_POSE_FILENAME)
    assert node._last_pose_file == expected
    assert node._last_pose_tracker is not None
    assert timers and timers[0][0] > 0
    assert subs and subs[0][0] == "/amcl_pose"

    # AMCL 수렴 메시지를 받은 뒤 타이머 → 파일 저장까지 수행한다.
    cov = [0.0] * 36
    cov[0] = cov[7] = cov[35] = 0.01
    subs[0][1](SimpleNamespace(pose=SimpleNamespace(covariance=cov)))
    node._last_pose_tracker.set_map_name("w2_5f.yaml")  # 서비스 대신 직접 지정
    timers[0][1]()
    assert node._last_pose_tracker.wait_for_pending() is True
    stored = read_last_pose_file(expected)
    assert stored is not None and stored["x"] == pytest.approx(2.0)
    assert stored["map"] == "w2_5f.yaml"


def test_setup_last_pose_tracking_without_memory_path_keeps_latest():
    from robo_claw_agent.agent_node.initialization import setup_last_pose_tracking

    timers: list = []
    subs: list = []
    node = _setup_node(_node_with_tf(1.0, 1.0, 0.0)._tf_buffer, timers, subs)

    setup_last_pose_tracking(node, SimpleNamespace(memory_path=None))
    timers[0][1]()

    assert node._last_pose_file == ""
    assert subs == []
    assert node._last_pose_tracker.latest["x"] == pytest.approx(1.0)


def test_setup_last_pose_tracking_disabled_without_tf(tmp_path):
    from robo_claw_agent.agent_node.initialization import setup_last_pose_tracking

    timers: list = []
    node = _setup_node(None, timers, [])
    setup_last_pose_tracking(node, SimpleNamespace(memory_path=str(tmp_path / "memory.json")))

    assert node._last_pose_tracker is None
    assert timers == []
    # 파일 경로는 남겨 "마지막 위치" 요청이 파일 값을 쓸 수 있게 한다.
    assert node._last_pose_file.endswith(LAST_POSE_FILENAME)
