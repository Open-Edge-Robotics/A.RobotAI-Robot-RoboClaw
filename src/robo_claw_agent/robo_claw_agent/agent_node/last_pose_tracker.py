"""마지막 위치 스냅샷 트래커.

로봇이 위치를 파악하고 있는 동안(TF `map → base_link` 가 유효한 동안) 주기적으로
pose 를 읽어 "마지막 위치"를 기억하고, 메모리 디렉터리의 `last_pose.yaml` 에 저장한다.
former nav2 기동 스크립트가 이 파일을 AMCL 초기 위치로 쓴다.

설계 원칙:

* **map 프레임만 기록한다.** `/odom` 폴백 좌표는 프레임이 달라 AMCL 초기 위치로 쓸 수
  없다.
* **AMCL 이 수렴했을 때만 저장한다.** TF 는 AMCL 이 엉뚱한 곳에 있어도 유효하므로,
  `/amcl_pose` 공분산이 크면 기록하지 않는다(틀린 위치가 좋은 값을 덮지 않게).
* **맵 이름을 함께 남긴다.** `map_server` 의 `yaml_filename` 을 조회해 기록하고, 기동
  스크립트는 맵이 다르면 이 파일을 무시한다.
* **이동했을 때만 쓴다.** 거리·회전 임계값을 넘고 최소 간격이 지났을 때만 저장한다
  (정지 중에는 쓰지 않아 저장 매체 쓰기를 줄인다).
* **에이전트가 살아 있으면 메모리 값이 가장 최신이다.** 네비게이션 재기동 중에는 TF 가
  끊겨 스냅샷이 갱신되지 않으므로, 마지막 스냅샷이 곧 "네비 재기동 직전 위치"가 된다.
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from typing import Any

from robo_claw_agent.geo_utils import yaw_from_quaternion
from robo_claw_agent.memory_manager.last_pose import (
    read_last_pose_file,
    write_last_pose_file,
)

logger = logging.getLogger(__name__)

#: 스냅샷 주기(초). TF 조회는 저렴하므로 촘촘히 돈다.
SNAPSHOT_INTERVAL_SEC = 5.0
#: 파일 저장 최소 간격(초). 이동 중에도 이보다 자주 쓰지 않는다.
MIN_PERSIST_INTERVAL_SEC = 15.0
#: 이 거리(m) 이상 움직였을 때 저장한다.
MIN_PERSIST_DISTANCE_M = 0.3
#: 이 회전(rad) 이상 돌았을 때 저장한다.
MIN_PERSIST_YAW_RAD = 0.5
#: AMCL 위치 표준편차(m, sqrt(var_x + var_y))가 이보다 크면 미수렴으로 본다.
MAX_LOCALIZATION_XY_STD_M = 0.5
#: AMCL 방향 표준편차(rad)가 이보다 크면 미수렴으로 본다.
MAX_LOCALIZATION_YAW_STD_RAD = 0.5

AMCL_POSE_TOPIC = "/amcl_pose"
MAP_SERVER_PARAM_SERVICE = "/map_server/get_parameters"
MAP_YAML_PARAM = "yaml_filename"

#: PoseWithCovariance 6x6 행렬의 x, y, yaw 분산 인덱스.
_COV_XX = 0
_COV_YY = 7
_COV_YAWYAW = 35


def read_map_pose(node: Any) -> dict[str, Any] | None:
    """TF `map → base_link` 로 현재 pose 를 읽는다(odom 폴백 없음).

    Returns:
        ``{"x", "y", "yaw", "frame"}`` 또는 TF 를 읽을 수 없으면 None.
    """
    tf_buffer = getattr(node, "_tf_buffer", None)
    if tf_buffer is None:
        return None
    map_frame = getattr(node, "_map_frame", "map")
    base_frame = getattr(node, "_robot_base_frame", "base_link")
    try:
        import rclpy.time

        tf = tf_buffer.lookup_transform(map_frame, base_frame, rclpy.time.Time())
    except Exception as exc:  # noqa: BLE001
        logger.debug("마지막 위치 스냅샷: map TF 조회 실패: %s", exc)
        return None

    translation = tf.transform.translation
    yaw = yaw_from_quaternion(tf.transform.rotation)
    if not all(math.isfinite(v) for v in (translation.x, translation.y, yaw)):
        return None
    return {
        "x": round(float(translation.x), 3),
        "y": round(float(translation.y), 3),
        "yaw": round(float(yaw), 4),
        "frame": "map",
    }


def amcl_std_from_covariance(covariance: Any) -> tuple[float, float] | None:
    """`/amcl_pose` 공분산에서 (위치 표준편차, 방향 표준편차)를 계산한다."""
    try:
        x_var = float(covariance[_COV_XX])
        y_var = float(covariance[_COV_YY])
        yaw_var = float(covariance[_COV_YAWYAW])
    except (IndexError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x_var, y_var, yaw_var)):
        return None
    xy_std = math.sqrt(max(x_var, 0.0) + max(y_var, 0.0))
    yaw_std = math.sqrt(max(yaw_var, 0.0))
    return xy_std, yaw_std


class LastPoseTracker:
    """마지막 위치를 메모리에 유지하고, 조건이 맞으면 `last_pose.yaml` 에 저장한다.

    ``pose_file`` 이 비어 있으면 파일 저장 없이 메모리 최신 pose 만 유지한다. 그래도
    에이전트 프로세스가 살아 있는 동안에는 "마지막 위치" 복원이 동작한다.

    ``require_localization=True`` 이면 `/amcl_pose` 로 수렴이 확인된 뒤에만 파일에
    쓴다. 맵 이름은 ``map_name`` 이 None(조회 중)인 동안 저장을 미룬다.
    """

    def __init__(
        self,
        *,
        pose_file: str = "",
        min_interval_sec: float = MIN_PERSIST_INTERVAL_SEC,
        min_distance_m: float = MIN_PERSIST_DISTANCE_M,
        min_yaw_rad: float = MIN_PERSIST_YAW_RAD,
        max_xy_std_m: float = MAX_LOCALIZATION_XY_STD_M,
        max_yaw_std_rad: float = MAX_LOCALIZATION_YAW_STD_RAD,
        require_localization: bool = False,
        background_persist: bool = False,
        clock: Any = time.monotonic,
    ):
        self._pose_file = str(pose_file or "")
        self._min_interval_sec = max(0.0, float(min_interval_sec))
        self._min_distance_m = max(0.0, float(min_distance_m))
        self._min_yaw_rad = max(0.0, float(min_yaw_rad))
        self._max_xy_std_m = float(max_xy_std_m)
        self._max_yaw_std_rad = float(max_yaw_std_rad)
        self._require_localization = bool(require_localization)
        # 로봇 노드 타이머 콜백을 파일/DB 대기로 막지 않도록 저장은 백그라운드 스레드로
        # 넘길 수 있다.
        self._background_persist = bool(background_persist)
        self._persist_lock = threading.Lock()
        self._pending_thread: threading.Thread | None = None
        self._clock = clock
        self._latest: dict[str, Any] | None = None
        self._persisted: dict[str, Any] | None = None
        self._last_persist_ts: float | None = None
        #: None = 아직 모름(조회 중), "" = 확인 불가, 그 외 = 맵 yaml 파일 이름.
        self._map_name: str | None = ""
        self._localization_std: tuple[float, float] | None = None

    @property
    def pose_file(self) -> str:
        return self._pose_file

    @property
    def persist_enabled(self) -> bool:
        return bool(self._pose_file)

    @property
    def latest(self) -> dict[str, Any] | None:
        return dict(self._latest) if self._latest else None

    @property
    def map_name(self) -> str | None:
        return self._map_name

    def set_map_name(self, name: str | None) -> None:
        """현재 맵 yaml 이름을 기록한다(경로가 오면 basename 만 남긴다)."""
        self._map_name = os.path.basename(str(name or "").strip())

    def invalidate_map(self) -> None:
        """네비게이션 재기동 등으로 맵이 바뀌었을 수 있을 때 다시 조회하게 한다."""
        self._map_name = None

    def set_localization_std(self, xy_std: float, yaw_std: float) -> None:
        self._localization_std = (float(xy_std), float(yaw_std))

    def reset_localization(self) -> None:
        self._localization_std = None

    def localization_state(self) -> str:
        """``"good"`` / ``"poor"`` / ``"unknown"``."""
        if self._localization_std is None:
            return "unknown"
        xy_std, yaw_std = self._localization_std
        if xy_std <= self._max_xy_std_m and yaw_std <= self._max_yaw_std_rad:
            return "good"
        return "poor"

    def should_persist(self, pose: dict[str, Any], now: float | None = None) -> bool:
        """주어진 pose 를 파일에 저장할 시점인지 판정한다(이동량·간격·맵 기준)."""
        if self._persisted is None or self._last_persist_ts is None:
            return True
        if (self._map_name or "") != str(self._persisted.get("map") or ""):
            return True
        now = self._clock() if now is None else now
        if now - self._last_persist_ts < self._min_interval_sec:
            return False
        moved = math.hypot(
            float(pose["x"]) - float(self._persisted["x"]),
            float(pose["y"]) - float(self._persisted["y"]),
        )
        turned = abs(_angle_diff(float(pose["yaw"]), float(self._persisted["yaw"])))
        return moved >= self._min_distance_m or turned >= self._min_yaw_rad

    def update(self, pose: dict[str, Any], memory: Any = None) -> bool:
        """pose 를 반영한다. 파일에 저장(또는 저장 시작)했으면 True.

        프레임이 map 이 아니거나 AMCL 이 미수렴(poor)이면 기록하지 않는다.
        """
        if not pose or str(pose.get("frame") or "map") != "map":
            return False
        state = self.localization_state()
        if state == "poor":
            return False
        self._latest = dict(pose)
        if not self.persist_enabled:
            return False
        if self._require_localization and state != "good":
            return False
        if self._map_name is None:
            return False
        if not self.should_persist(pose):
            return False

        snapshot = dict(pose, map=self._map_name or "")
        # 낙관적으로 throttle 상태를 먼저 갱신한다(백그라운드 저장 중 중복 쓰기 방지).
        self._persisted = snapshot
        self._last_persist_ts = self._clock()
        if not self._background_persist:
            return self._persist(snapshot, memory)
        self._start_background_persist(snapshot, memory)
        return True

    def _persist(self, pose: dict[str, Any], memory: Any) -> bool:
        near_place = ""
        if memory is not None and hasattr(memory, "get_nearest_object"):
            try:
                nearest = memory.get_nearest_object(pose["x"], pose["y"], radius_m=2.0)
                if nearest:
                    near_place = str(nearest.get("name") or "")
            except Exception as exc:  # noqa: BLE001
                logger.debug("마지막 위치 주변 장소 조회 실패(무시): %s", exc)

        stored = write_last_pose_file(
            self._pose_file,
            x=pose["x"],
            y=pose["y"],
            yaw=pose["yaw"],
            frame=str(pose.get("frame") or "map"),
            map_name=str(pose.get("map") or ""),
            near_place=near_place,
        )
        if stored:
            logger.debug(
                "last_pose_saved x=%.2f y=%.2f yaw=%.2f map=%s near_place=%s",
                pose["x"],
                pose["y"],
                pose["yaw"],
                pose.get("map") or "",
                near_place,
            )
        return stored

    def _start_background_persist(self, pose: dict[str, Any], memory: Any) -> None:
        with self._persist_lock:
            if self._pending_thread is not None and self._pending_thread.is_alive():
                # 이전 저장이 아직 진행 중이면 이번 스냅샷은 버린다.
                return
            thread = threading.Thread(
                target=self._persist,
                args=(dict(pose), memory),
                name="last-pose-persist",
                daemon=True,
            )
            self._pending_thread = thread
        thread.start()

    def wait_for_pending(self, timeout: float = 5.0) -> bool:
        """진행 중인 백그라운드 저장이 끝날 때까지 기다린다(테스트/종료 경로용)."""
        thread = self._pending_thread
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    def update_from_node(self, node: Any) -> bool:
        """노드의 TF 에서 pose 를 읽어 반영한다(타이머 콜백용)."""
        pose = read_map_pose(node)
        if pose is None:
            return False
        return self.update(pose, getattr(node, "_memory", None))


def _angle_diff(a: float, b: float) -> float:
    return (a - b + math.pi) % (2.0 * math.pi) - math.pi


class MapNameQuery:
    """`map_server` 의 `yaml_filename` 파라미터를 비동기로 조회해 트래커에 넣는다.

    타이머 콜백을 막지 않도록 ``call_async`` 만 쓰고 매 ``poll()`` 에서 결과를 확인한다.
    서비스가 없거나 시간 초과면 맵 이름을 ""(확인 불가)로 두어 저장을 막지 않는다.
    """

    def __init__(
        self,
        node: Any,
        tracker: LastPoseTracker,
        *,
        service_name: str = MAP_SERVER_PARAM_SERVICE,
        timeout_sec: float = 3.0,
        max_wait_polls: int = 3,
        clock: Any = time.monotonic,
    ):
        self._node = node
        self._tracker = tracker
        self._service_name = service_name
        self._timeout_sec = float(timeout_sec)
        self._max_wait_polls = max(1, int(max_wait_polls))
        self._clock = clock
        self._client: Any = None
        self._future: Any = None
        self._future_ts = 0.0
        self._wait_polls = 0

    def poll(self) -> None:
        if self._tracker.map_name is not None:
            self._wait_polls = 0
            return
        if self._future is not None:
            self._check_future()
            return
        client = self._ensure_client()
        if client is None:
            self._tracker.set_map_name("")
            return
        try:
            ready = bool(client.service_is_ready())
        except Exception:  # noqa: BLE001
            ready = False
        if not ready:
            self._wait_polls += 1
            if self._wait_polls >= self._max_wait_polls:
                logger.warning("last_pose_map_name_unavailable service=%s", self._service_name)
                self._tracker.set_map_name("")
                self._wait_polls = 0
            return
        from rcl_interfaces.srv import GetParameters

        request = GetParameters.Request()
        request.names = [MAP_YAML_PARAM]
        self._future = client.call_async(request)
        self._future_ts = self._clock()

    def _check_future(self) -> None:
        future = self._future
        if not future.done():
            if self._clock() - self._future_ts >= self._timeout_sec:
                future.cancel()
                self._future = None
                logger.warning("last_pose_map_name_timeout service=%s", self._service_name)
                self._tracker.set_map_name("")
            return
        self._future = None
        name = ""
        try:
            result = future.result()
            values = list(getattr(result, "values", []) or [])
            if values:
                name = str(getattr(values[0], "string_value", "") or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("last_pose_map_name_failed err=%s", exc)
        self._tracker.set_map_name(name)
        logger.info("last_pose_map_name map=%s", self._tracker.map_name or "")

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from rcl_interfaces.srv import GetParameters

            self._client = self._node.create_client(GetParameters, self._service_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("last_pose_map_client_failed err=%s", exc)
            self._client = None
        return self._client


def snapshot_tick(node: Any, tracker: LastPoseTracker, map_query: MapNameQuery | None) -> bool:
    """타이머 콜백: TF 스냅샷 → 맵 이름 조회 → 트래커 갱신.

    TF 가 끊기면(네비 재기동) 맵과 AMCL 수렴 상태를 초기화해, 다시 뜬 뒤 새 맵·새
    수렴 결과로 판단하게 한다.
    """
    pose = read_map_pose(node)
    if pose is None:
        if map_query is not None and tracker.map_name is not None:
            tracker.invalidate_map()
        tracker.reset_localization()
        return False
    if map_query is not None:
        map_query.poll()
    return tracker.update(pose, getattr(node, "_memory", None))


def read_tracked_or_stored_pose(node: Any) -> dict[str, Any] | None:
    """트래커의 최신 pose 를 우선 쓰고, 없으면 `last_pose.yaml` 을 쓴다.

    현재 맵 이름을 알고 있고 파일의 맵과 다르면 파일 값은 쓰지 않는다.
    """
    tracker = getattr(node, "_last_pose_tracker", None)
    latest = tracker.latest if tracker is not None else None
    if latest and str(latest.get("frame") or "map") == "map":
        return latest
    pose_file = getattr(node, "_last_pose_file", "") or (tracker.pose_file if tracker else "")
    stored = read_last_pose_file(pose_file)
    if stored is None:
        return None
    current_map = tracker.map_name if tracker is not None else ""
    if current_map and stored.get("map") and stored["map"] != current_map:
        logger.warning("last_pose_map_mismatch stored=%s current=%s", stored["map"], current_map)
        return None
    return stored
