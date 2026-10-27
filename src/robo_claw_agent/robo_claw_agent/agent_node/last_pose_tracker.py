"""마지막 위치 스냅샷 트래커.

로봇이 위치를 파악하고 있는 동안(TF `map → base_link` 가 유효한 동안) 주기적으로
pose 를 읽어 "마지막 위치"를 기억한다. 네비게이션이 재기동되어 AMCL 이 초기 위치를
잃었을 때 "마지막 위치를 초기 위치로" 요청으로 복구하기 위한 기반이다.

설계 원칙:

* **map 프레임만 기록한다.** `/odom` 폴백 좌표는 프레임이 달라 AMCL 초기 위치로 쓸 수
  없다(`0,0` 근처의 odom 좌표를 map 좌표로 넣으면 로봇이 엉뚱한 곳에 자신을 놓는다).
* **항상 최신 pose 는 메모리에 유지하고, RAG 저장은 throttle 한다.** 임베딩 호출은
  비싸므로 이동이 임계값을 넘고 최소 간격이 지났을 때만 저장한다. 정지 중에는 쓰지
  않는다.
* **에이전트 노드가 살아 있으면 메모리 값이 가장 최신이다.** 네비게이션 재기동 중에는
  TF 가 끊겨 스냅샷이 갱신되지 않으므로, 마지막으로 성공한 스냅샷이 곧 "네비 재기동
  직전 위치"가 된다.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any

from robo_claw_agent.geo_utils import yaw_from_quaternion
from robo_claw_agent.memory_manager.last_pose import (
    read_last_pose,
    remember_last_pose,
)

logger = logging.getLogger(__name__)

#: 스냅샷 주기(초). TF 조회는 저렴하므로 촘촘히 돈다.
SNAPSHOT_INTERVAL_SEC = 5.0
#: RAG 저장 최소 간격(초). 이동 중에도 이보다 자주 쓰지 않는다.
MIN_PERSIST_INTERVAL_SEC = 15.0
#: 이 거리(m) 이상 움직였을 때 저장한다.
MIN_PERSIST_DISTANCE_M = 0.3
#: 이 회전(rad) 이상 돌았을 때 저장한다.
MIN_PERSIST_YAW_RAD = 0.5


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


class LastPoseTracker:
    """마지막 위치를 메모리에 유지하고 주기적으로 RAG 에 저장한다.

    ``persist_enabled=False``(RAG 비활성)여도 메모리 최신 pose 는 계속 유지한다.
    그러면 에이전트 프로세스가 살아 있는 동안에는 "마지막 위치" 복원이 동작한다.
    """

    def __init__(
        self,
        *,
        min_interval_sec: float = MIN_PERSIST_INTERVAL_SEC,
        min_distance_m: float = MIN_PERSIST_DISTANCE_M,
        min_yaw_rad: float = MIN_PERSIST_YAW_RAD,
        persist_enabled: bool = True,
        background_persist: bool = False,
        clock: Any = time.monotonic,
    ):
        self._min_interval_sec = max(0.0, float(min_interval_sec))
        self._min_distance_m = max(0.0, float(min_distance_m))
        self._min_yaw_rad = max(0.0, float(min_yaw_rad))
        self._persist_enabled = bool(persist_enabled)
        # 로봇 노드 타이머 콜백을 임베딩 대기로 막지 않도록 저장은 백그라운드 스레드로
        # 넘길 수 있다(스킬 교훈 추출 타이머와 같은 패턴).
        self._background_persist = bool(background_persist)
        self._persist_lock = threading.Lock()
        self._pending_thread: threading.Thread | None = None
        self._clock = clock
        self._latest: dict[str, Any] | None = None
        self._persisted: dict[str, Any] | None = None
        self._last_persist_ts: float | None = None

    @property
    def latest(self) -> dict[str, Any] | None:
        return dict(self._latest) if self._latest else None

    def should_persist(self, pose: dict[str, Any], now: float | None = None) -> bool:
        """주어진 pose 를 RAG 에 저장할 시점인지 판정한다."""
        if self._persisted is None or self._last_persist_ts is None:
            return True
        now = self._clock() if now is None else now
        if now - self._last_persist_ts >= self._min_interval_sec:
            return True
        moved = math.hypot(
            float(pose["x"]) - float(self._persisted["x"]),
            float(pose["y"]) - float(self._persisted["y"]),
        )
        turned = abs(_angle_diff(float(pose["yaw"]), float(self._persisted["yaw"])))
        return moved >= self._min_distance_m or turned >= self._min_yaw_rad

    def update(self, pose: dict[str, Any], memory: Any) -> bool:
        """pose 를 반영한다. RAG 에 저장했으면 True.

        프레임이 map 이 아니면(예: odom 폴백) 기록하지 않는다 — AMCL 초기 위치로
        쓸 수 없는 좌표이기 때문이다.
        """
        if not pose or str(pose.get("frame") or "map") != "map":
            return False
        self._latest = dict(pose)
        if not self._persist_enabled:
            return False
        if not self.should_persist(pose):
            return False

        # 낙관적으로 throttle 상태를 먼저 갱신한다(백그라운드 저장 중 중복 쓰기 방지).
        self._persisted = dict(pose)
        self._last_persist_ts = self._clock()
        if not self._background_persist:
            return self._persist(pose, memory)
        self._start_background_persist(pose, memory)
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

        stored = remember_last_pose(
            memory,
            x=pose["x"],
            y=pose["y"],
            yaw=pose["yaw"],
            frame=str(pose.get("frame") or "map"),
            near_place=near_place,
        )
        if stored:
            logger.info(
                "마지막 위치 저장: x=%.2f, y=%.2f, yaw=%.2f%s",
                pose["x"],
                pose["y"],
                pose["yaw"],
                f" ({near_place} 부근)" if near_place else "",
            )
        return stored

    def _start_background_persist(self, pose: dict[str, Any], memory: Any) -> None:
        with self._persist_lock:
            if self._pending_thread is not None and self._pending_thread.is_alive():
                # 이전 저장이 아직 진행 중이면 이번 스냅샷은 버린다(임베딩 큐 적체 방지).
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

    def remember_latest(self, memory: Any) -> bool:
        """간격/throttle 을 무시하고 현재 최신 pose 를 즉시(동기) 저장한다."""
        if self._latest is None or not self._persist_enabled:
            return False
        self._persisted = dict(self._latest)
        self._last_persist_ts = self._clock()
        return self._persist(self._latest, memory)


def _angle_diff(a: float, b: float) -> float:
    diff = (a - b + math.pi) % (2.0 * math.pi) - math.pi
    return diff


def read_tracked_or_stored_pose(node: Any) -> dict[str, Any] | None:
    """트래커의 최신 pose 를 우선 쓰고, 없으면 RAG 에 저장된 마지막 위치를 쓴다."""
    tracker = getattr(node, "_last_pose_tracker", None)
    latest = tracker.latest if tracker is not None else None
    if latest and str(latest.get("frame") or "map") == "map":
        return latest
    return read_last_pose(getattr(node, "_memory", None))
