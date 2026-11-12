"""지도 목표 비동기 실행 서비스 — 접수/대기/실행/결과 병합과 lease 관리.

HTTP 스레드는 202 응답만 돌려주고, 실제 ExecuteTask 위임은 단일 워커 스레드가
순차 수행한다. rclpy / ROS 메시지 import 금지(backend protocol 로만 통신).
"""

import logging
import queue
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Any

from ..domain.map_goal import (
    SELECTION_TTL_SEC,
    adopt_external_record,
    goal_signature,
    merge_records,
    new_record,
    normalize_record,
    parse_map_goal_request,
    records_compatible,
    validate_map_goal_against_map,
)
from .map_goal_worker import WORKER_JOIN_TIMEOUT_SEC, MapGoalWorkerMixin

logger = logging.getLogger(__name__)

#: 보관하는 요청 기록 수 상한.
MAX_RETAINED_RECORDS = 20


class MapGoalService(MapGoalWorkerMixin):
    """지도 선택 목표를 검증하고 비동기로 에이전트에 위임한다."""

    def __init__(
        self,
        backend: Any,
        *,
        map_snapshot: Callable[[], dict[str, Any]],
        agent_identity: Callable[[], dict[str, Any] | None],
        wall_clock: Callable[[], float] | None = None,
        max_records: int = MAX_RETAINED_RECORDS,
    ) -> None:
        self._backend = backend
        self._map_snapshot = map_snapshot
        self._agent_identity = agent_identity
        self._wall_clock = wall_clock or time.time
        self._max_records = max(1, int(max_records))

        self._lock = threading.RLock()
        self._records: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._signatures: dict[str, str] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_id: str | None = None
        #: 대시보드 재시작/외부 peer 로 관측한 에이전트 측 lease(자동 해제 금지).
        self._external_lease: dict[str, Any] | None = None
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._shutdown = False
        self._idle = threading.Event()
        self._idle.set()

    # ---- 공개 API ---------------------------------------------------------
    def submit(self, payload: Any) -> dict[str, Any]:
        """map_goal 모드 요청을 접수한다. HTTP 코드가 담긴 dict 를 돌려준다."""
        ok, error, goal = parse_map_goal_request(payload)
        if not ok or goal is None:
            return {"success": False, "error": error, "status_code": 400}

        signature = goal_signature(goal)
        request_id = goal["request_id"]
        with self._lock:
            if self._shutdown:
                return {
                    "success": False,
                    "error": "대시보드가 종료 중입니다",
                    "status_code": 503,
                }
            existing = self._records.get(request_id)
            if existing is not None:
                if self._signatures.get(request_id) == signature:
                    return {
                        "success": True,
                        "request": self._copy_record(existing),
                        "status_code": 202,
                    }
                return {
                    "success": False,
                    "error": "같은 request_id 로 다른 목표를 제출할 수 없습니다",
                    "status_code": 409,
                    "request": self._copy_record(existing),
                }
            if self._active_id is not None:
                active = self._records.get(self._active_id)
                return {
                    "success": False,
                    "error": "다른 지도 목표가 진행 중입니다",
                    "status_code": 409,
                    **({"request": self._copy_record(active)} if active is not None else {}),
                }

            snapshot = self._map_snapshot()
            identity = self._agent_identity()
            valid, error, kind = validate_map_goal_against_map(goal, snapshot, identity)
            if not valid:
                return {
                    "success": False,
                    "error": error,
                    "status_code": 503 if kind == "unavailable" else 400,
                }

            self._observe_agent_record_locked(
                normalize_record((identity or {}).get("map_navigation_request"))
            )
            if self._external_lease is not None:
                external = self._copy_record(self._external_lease)
                return {
                    "success": False,
                    "error": "에이전트가 이미 지도 목표를 수행 중입니다",
                    "status_code": 409,
                    "request": external,
                }

            created_at = self._wall_clock()
            record = new_record(
                request_id=request_id,
                robot_id=goal["robot_id"],
                target=goal["target"],
                geometry_id=goal["geometry_id"],
                created_at=created_at,
            )
            self._active_id = request_id
            self._records[request_id] = record
            self._signatures[request_id] = signature
            self._jobs[request_id] = {
                "goal": goal,
                "map_topic": str(snapshot.get("topic") or ""),
                "expires_at": created_at + SELECTION_TTL_SEC,
            }
            self._prune_locked()
            self._idle.clear()
            self._ensure_worker_locked()
            self._queue.put(request_id)
            return {
                "success": True,
                "request": self._copy_record(record),
                "status_code": 202,
            }

    def status_record(self, agent_state: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        """/api/status 에 싣는 현재 요청 기록(에이전트 권위 기록과 병합)."""
        raw_record = None
        if isinstance(agent_state, Mapping):
            raw_record = normalize_record(agent_state.get("map_navigation_request"))
        with self._lock:
            current = self._current_record_locked()
            owned = (
                raw_record is not None
                and current is not None
                and self._active_id == raw_record.get("request_id")
                and records_compatible(current, raw_record)
            )
            # A fresh QueryState sample can report a long-running owned goal
            # without changing the timestamp of its last state transition.
            agent_record = (
                raw_record if owned else adopt_external_record(raw_record, now=self._wall_clock())
            )
            self._observe_agent_record_locked(agent_record)
            local = self._current_record_locked()
            if local is None:
                if self._external_lease is None:
                    return None
                return self._copy_record(self._external_lease)
            merged = merge_records(local, agent_record)
            if merged is None:
                return None
            self._merge_authority_locked(local, agent_record, merged)
            result = self._copy_record(merged)
            return result

    def observe_agent_record(self, record: Mapping[str, Any] | None) -> None:
        """QueryState 에서 본 에이전트 기록을 lease 판정에 반영한다(초기 위치 차단용)."""
        with self._lock:
            self._observe_agent_record_locked(normalize_record(record))

    def holds_lease(self) -> bool:
        """진행 중이거나 결과 미확인인 지도 목표가 있는지(초기 위치 변경 차단)."""
        with self._lock:
            return self._active_id is not None or self._external_lease is not None

    def wait_for_idle(self, timeout: float) -> bool:
        """워커가 유휴 상태가 될 때까지 기다린다(테스트/종료 경로)."""
        return self._idle.wait(timeout)

    def shutdown(self) -> None:
        """새 요청을 막고 진행 중인 자기 goal 만 취소 요청한 뒤 워커를 정리한다."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            cancel_event = self._cancel_event
            worker = self._worker
        if cancel_event is not None:
            cancel_event.set()
        if worker is not None and worker.is_alive():
            worker.join(timeout=WORKER_JOIN_TIMEOUT_SEC)
