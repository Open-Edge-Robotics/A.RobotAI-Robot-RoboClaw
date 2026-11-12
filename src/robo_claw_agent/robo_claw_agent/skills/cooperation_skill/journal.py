"""협동 관측 저널 — 메모리 링 + 선택적 비동기 SQLite 저장.

자율협동에서 로봇이 관측한 일(세션 시작/종료, 메시지 수신/전송, LLM 결정, 정책
판정, 태스크 실행 결과)을 순번이 붙은 이벤트로 남긴다. 관측 API(QueryState)는 이
저널의 스냅샷을 그대로 노출한다.

설계 원칙:
- 메모리 링은 항상 사용 가능하다. 저장소가 없거나 고장 나도 기록은 계속된다.
- ``record`` 는 절대 블로킹/예외로 실행 경로를 막지 않는다. SQLite 쓰기는 단일
  스레드가 맡고(``journal_store``), 큐가 가득 차면 버린 건수를 센다.
- 스냅샷은 복사본이다. 호출자가 반환값을 수정해도 내부 상태는 바뀌지 않는다.
- 기록한 이벤트는 sanitized JSON 한 줄로 서버 로그에도 남긴다(``[CooperationEvent]``).
  로그 handler 가 예외를 던져도 기록/실행 경로는 멈추지 않는다.
- 태스크 상관 정보(우리가 보낸 message_id)와 전달 확인 플래그를 내부에 기억한다.
  이 값들은 관측 API 태스크 dict 에는 ``delivery_confirmed`` 만 노출한다.

ROS2/네트워크 의존이 없다.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..cleanup.activity import CleanupActivityStore
from ..cleanup.observation import SOURCE_AUTONOMOUS_COOPERATE
from .envelope import TASK_STATUS_UNKNOWN, normalize_task_status
from .journal_event import CooperationEvent, log_event
from .journal_store import (
    DEFAULT_EVENT_QUEUE_SIZE,
    DEFAULT_RETENTION_ROWS,
    SqliteEventWriter,
)
from .sanitize import sanitize_details, sanitize_text

logger = logging.getLogger(__name__)

#: 이벤트 타입 — 관측 API 소비자가 해석하는 값. 필요하면 추가할 수 있다.
EVENT_SESSION_STARTED = "session_started"
EVENT_SESSION_STOPPED = "session_stopped"
EVENT_MESSAGE_QUEUED = "message_queued"
EVENT_MESSAGE_RECEIVED = "message_received"
EVENT_MESSAGE_SEND_FAILED = "message_send_failed"
EVENT_MESSAGE_REJECTED = "message_rejected"
EVENT_DECISION = "decision"
EVENT_POLICY = "policy"
EVENT_TASK_STARTED = "task_started"
EVENT_TASK_COMPLETED = "task_completed"
EVENT_TASK_FAILED = "task_failed"
EVENT_TASK_DENIED = "task_denied"
EVENT_TASK_PARTIAL = "task_partial"
EVENT_REPLY_QUEUED = "reply_queued"
EVENT_DELIVERY_CONFIRMED = "delivery_confirmed"

DEFAULT_MAX_EVENTS = 100
DEFAULT_MAX_TASKS = 50
DEFAULT_MAX_TASK_RECORDS = 200
#: 태스크별로 기억하는 outbound message_id 상한(최근 것만 유지).
MAX_OUTBOUND_MESSAGE_IDS = 8

_ROBOT_ID_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_ROBOT_ID_CHARS = 64


class CooperationJournal:
    """로컬 로봇의 협동 관측 기록(메모리 링 + 태스크 상태 + 선택적 SQLite)."""

    def __init__(
        self,
        *,
        local_robot_id: str = "local",
        path: str | Path | None = None,
        max_events: int = DEFAULT_MAX_EVENTS,
        max_tasks: int = DEFAULT_MAX_TASKS,
        max_task_records: int = DEFAULT_MAX_TASK_RECORDS,
        event_queue_size: int = DEFAULT_EVENT_QUEUE_SIZE,
        retention_rows: int = DEFAULT_RETENTION_ROWS,
    ) -> None:
        self._lock = threading.RLock()
        self._local_robot_id = str(local_robot_id or "local")
        self._max_events = max(1, int(max_events))
        self._max_tasks = max(1, int(max_tasks))
        self._max_task_records = max(self._max_tasks, int(max_task_records))
        self._events: deque[CooperationEvent] = deque()
        # 정리 실행(tidy_home/cleanup_demo)의 bounded 활동 상태. 자율협동 세션과
        # 별도로 유지해 서로의 session_id/goal/active 를 덮어쓰지 않는다.
        self._cleanup_activities = CleanupActivityStore()
        self._tasks: dict[str, dict[str, Any]] = {}
        # task_id -> 우리가 이 태스크로 보낸 outbound message_id(최근 것만).
        self._task_outbound: dict[str, list[str]] = {}
        self._stream_id = uuid.uuid4().hex
        self._session_id = ""
        self._goal = ""
        self._active = False
        self._next_sequence = 1
        self._dropped_events = 0
        self._writer = (
            SqliteEventWriter(
                Path(path),
                stream_id=self._stream_id,
                retention_rows=retention_rows,
                queue_size=event_queue_size,
            )
            if path is not None
            else None
        )

    # ── 상태 조회 ─────────────────────────────────────────────────

    @property
    def local_robot_id(self) -> str:
        return self._local_robot_id

    @property
    def stream_id(self) -> str:
        """프로세스마다 새로 발급되는 스트림 ID. 프로세스가 재시작하면 바뀐다."""
        return self._stream_id

    @property
    def session_id(self) -> str:
        with self._lock:
            return self._session_id

    @property
    def goal(self) -> str:
        with self._lock:
            return self._goal

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def cleanup_activities(self) -> CleanupActivityStore:
        """정리 실행 관측기가 갱신하는 bounded 활동 상태 저장소."""
        return self._cleanup_activities

    # ── 세션 ─────────────────────────────────────────────────────

    def start_session(self, *, goal: str = "", session_id: str = "") -> str:
        """협동 세션을 시작하고 세션 ID를 돌려준다."""
        with self._lock:
            self._session_id = str(session_id or "").strip() or uuid.uuid4().hex
            self._goal = sanitize_text(goal, 500)
            self._active = True
            session = self._session_id
            goal_text = self._goal
        self.record(
            EVENT_SESSION_STARTED,
            session_id=session,
            content=goal_text,
            details={"state": "started", "goal": goal_text},
        )
        return session

    def stop_session(self, *, reason: str = "") -> None:
        """로컬 세션만 종료한다. 실행 중 태스크 상태는 바꾸지 않는다."""
        with self._lock:
            self._active = False
            session = self._session_id
        self.record(
            EVENT_SESSION_STOPPED,
            session_id=session,
            content=reason,
            details={"state": "stopped", "reason": reason},
        )

    # ── 이벤트 ───────────────────────────────────────────────────

    def record(
        self,
        event_type: str,
        *,
        peer_id: str = "",
        task_id: str = "",
        message_id: str = "",
        reply_to: str = "",
        session_id: str | None = None,
        status: str = "",
        content: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> int:
        """이벤트 한 건을 기록하고 순번을 반환한다. 실패해도 예외를 던지지 않는다."""
        with self._lock:
            sequence = self._next_sequence
            self._next_sequence += 1
            event = CooperationEvent(
                sequence=sequence,
                event_type=sanitize_text(event_type or "unknown", 48),
                timestamp=time.time(),
                robot_id=sanitize_text(self._local_robot_id, 128),
                peer_id=sanitize_text(peer_id, 128),
                session_id=sanitize_text(
                    self._session_id if session_id is None else session_id, 64
                ),
                task_id=sanitize_text(task_id, 64),
                message_id=sanitize_text(message_id, 64),
                reply_to=sanitize_text(reply_to, 64),
                status=sanitize_text(status, 32),
                content=sanitize_text(content),
                details=sanitize_details(details or {}),
            )
            self._events.append(event)
            while len(self._events) > self._max_events:
                self._events.popleft()
                self._dropped_events += 1
            writer = self._writer
        if writer is not None:
            writer.submit(event)
        log_event(event)
        return sequence

    # ── 태스크 ───────────────────────────────────────────────────

    def upsert_task(
        self,
        task_id: str,
        *,
        peer_id: str = "",
        session_id: str = "",
        description: str = "",
        status: str = "",
        result: str | None = None,
    ) -> None:
        """태스크를 만들거나 메타데이터를 보강한다. 기존 상태는 덮어쓰지 않는다."""
        key = str(task_id or "").strip()
        if not key:
            return
        now = time.time()
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                self._tasks[key] = {
                    "task_id": key,
                    "session_id": str(session_id or ""),
                    "peer_id": str(peer_id or ""),
                    "description": sanitize_text(description, 500),
                    "status": normalize_task_status(status) if status else TASK_STATUS_UNKNOWN,
                    "created_at": now,
                    "updated_at": now,
                    "result": sanitize_text(result, 500) if result else "",
                    "delivery_confirmed": False,
                    # observed=False: 로컬 실행/로컬 관측. True: 상대가 보고한 결과.
                    "observed": False,
                    # 상대 보고 결과일 때만 "peer_report" 로 채운다.
                    "origin": "",
                    # 이 태스크로 우리가 마지막에 보낸 message_id(안전한 ID 표시용).
                    "outbound_message_id": "",
                }
                self._evict_tasks()
                return
            # 이미 가진 peer/session/description 은 다른 경로(재수신·다른 peer)가
            # 덮어쓰지 않는다. 상관이 어긋난 메시지가 태스크 정체성을 바꾸면 안 된다.
            if session_id and not task["session_id"]:
                task["session_id"] = str(session_id)
            if peer_id and not task["peer_id"]:
                task["peer_id"] = str(peer_id)
            if description and not task["description"]:
                task["description"] = sanitize_text(description, 500)
            if result is not None:
                task["result"] = sanitize_text(result, 500)
            task["updated_at"] = now

    def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        result: str | None = None,
        description: str | None = None,
        observed: bool | None = None,
        origin: str | None = None,
    ) -> bool:
        """태스크 상태/결과를 갱신한다. 태스크가 없으면 False.

        ``observed`` 는 결과 출처 표시다. 로컬 실행 결과는 False, 상대가 보고한
        결과는 True 로 두고 ``origin="peer_report"`` 를 함께 남긴다.
        """
        key = str(task_id or "").strip()
        if not key:
            return False
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return False
            if status:
                task["status"] = normalize_task_status(status)
            if result is not None:
                task["result"] = sanitize_text(result, 500)
            if description is not None:
                task["description"] = sanitize_text(description, 500)
            if observed is not None:
                task["observed"] = bool(observed)
            if origin is not None:
                task["origin"] = str(origin)[:32]
            task["updated_at"] = time.time()
        return True

    def mark_observed_result(self, task_id: str, *, status: str, result: str) -> bool:
        """상대가 보고한 결과를 태스크에 반영한다(출처 표시 포함)."""
        return self.update_task(
            task_id,
            status=status,
            result=result,
            observed=True,
            origin="peer_report",
        )

    def task(self, task_id: str) -> dict[str, Any] | None:
        """태스크 사본. 없으면 None."""
        with self._lock:
            task = self._tasks.get(str(task_id or "").strip())
            return dict(task) if task is not None else None

    def task_status(self, task_id: str) -> str | None:
        task = self.task(task_id)
        return task["status"] if task is not None else None

    # ── 상관 메모리 ───────────────────────────────────────────────

    def note_outbound_message(self, task_id: str, message_id: str) -> None:
        """이 태스크로 우리가 보낸 message_id 를 기억한다(상관 검증용).

        태스크별로 최근 ``MAX_OUTBOUND_MESSAGE_IDS`` 개만 유지한다.
        """
        key = str(task_id or "").strip()
        message = str(message_id or "").strip()
        if not key or not message:
            return
        with self._lock:
            if key not in self._tasks:
                return
            ids = self._task_outbound.setdefault(key, [])
            if message in ids:
                ids.remove(message)
            ids.append(message)
            del ids[:-MAX_OUTBOUND_MESSAGE_IDS]
            task = self._tasks.get(key)
            if task is not None:
                task["outbound_message_id"] = message

    def is_outbound_message(self, task_id: str, message_id: str) -> bool:
        """해당 message_id 가 이 태스크로 우리가 보낸 것인지."""
        key = str(task_id or "").strip()
        message = str(message_id or "").strip()
        if not key or not message:
            return False
        with self._lock:
            return message in self._task_outbound.get(key, [])

    def outbound_message_count(self, task_id: str) -> int:
        with self._lock:
            return len(self._task_outbound.get(str(task_id or "").strip(), []))

    def mark_delivery_confirmed(
        self,
        task_id: str,
        *,
        peer_id: str = "",
        message_id: str = "",
        reply_to: str = "",
    ) -> bool:
        """상관된 회신을 받았다는 확인만 기록한다. 태스크 완료로 추론하지 않는다."""
        key = str(task_id or "").strip()
        if not key:
            return False
        with self._lock:
            task = self._tasks.get(key)
            if task is None:
                return False
            if not reply_to or reply_to not in self._task_outbound.get(key, []):
                return False
            if task["peer_id"] and peer_id and task["peer_id"] != peer_id:
                return False
            task["delivery_confirmed"] = True
            task["updated_at"] = time.time()
            session_id = task["session_id"] or None
            task_peer = task["peer_id"]
        self.record(
            EVENT_DELIVERY_CONFIRMED,
            peer_id=peer_id or task_peer,
            task_id=key,
            message_id=message_id,
            reply_to=reply_to,
            session_id=session_id,
            details={"delivery_confirmed": True},
        )
        return True

    def _evict_tasks(self) -> None:
        """레코드 상한을 넘으면 오래 갱신되지 않은 태스크부터 버린다."""
        while len(self._tasks) > self._max_task_records:
            oldest = min(self._tasks, key=lambda key: self._tasks[key]["updated_at"])
            self._tasks.pop(oldest, None)
            self._task_outbound.pop(oldest, None)

    # ── 스냅샷 ───────────────────────────────────────────────────

    def snapshot(
        self,
        *,
        enabled: bool = True,
        max_events: int | None = None,
        max_tasks: int | None = None,
        content_limit: int | None = None,
        detail_budget: int | None = None,
    ) -> dict[str, Any]:
        """관측 API 용 협동 상태 사본.

        ``enabled`` 는 협동이 허용된 구성인지(예: Maestro 미사용)를 호출자가 알려준다.
        ``content_limit``/``detail_budget`` 을 주면 API 응답만 압축한다(원본 이벤트와
        SQLite 에 저장되는 내용은 그대로다). ``oldest_cursor`` 는 실제로 반환한
        첫 이벤트의 sequence 라서, API 가 잘라 보낸 구간의 빈틈을 정확히 가리킨다.
        """
        event_limit = self._max_events if max_events is None else max(0, int(max_events))
        task_limit = self._max_tasks if max_tasks is None else max(0, int(max_tasks))
        with self._lock:
            retained = list(self._events)
            selected = retained[-event_limit:] if event_limit else []
            events = [
                self._snapshot_event(event, content_limit, detail_budget) for event in selected
            ]
            tasks = sorted(self._tasks.values(), key=lambda task: task["updated_at"], reverse=True)[
                :task_limit
            ]
            task_copies = [dict(task) for task in tasks]
            writer = self._writer
            dropped = self._dropped_events + (writer.dropped if writer is not None else 0)
            activity = self._cleanup_activities.snapshot()
            # 표시용 projection — 내부 세션 필드(self._active/_goal/_session_id)는 바꾸지
            # 않는다. 살아 있는 자율협동 세션이 있으면 그것을 우선하고, 없으면 정리
            # 활동의 진행/마지막 상태를 보여준다.
            cooperation_live = bool(self._active and enabled)
            if cooperation_live:
                view_active = True
                view_session_id = self._session_id
                view_goal = self._goal
                activity_source = SOURCE_AUTONOMOUS_COOPERATE
            elif activity is not None:
                view_active = bool(activity["active"])
                view_session_id = str(activity["session_id"] or activity["run_id"])
                view_goal = str(activity["goal"])
                activity_source = str(activity["source"])
            else:
                view_active = False
                view_session_id = self._session_id
                view_goal = self._goal
                activity_source = ""
            return {
                "available": True,
                # 정리 활동은 직접 협동이 꺼져 있어도 표시할 수 있다(Maestro 관제 포함).
                "enabled": bool(enabled or activity is not None),
                "direct_cooperation_enabled": bool(enabled),
                "active": view_active,
                "local_robot_id": self._local_robot_id,
                "session_id": view_session_id,
                "goal": view_goal,
                "stream_id": self._stream_id,
                "activity": activity,
                "activity_source": activity_source,
                "events": events,
                "tasks": task_copies,
                "next_cursor": self._next_sequence,
                "oldest_cursor": events[0]["sequence"] if events else 0,
                "dropped_events": dropped,
                "persistence": {
                    "enabled": bool(writer is not None and writer.enabled),
                    "error": writer.error if writer is not None else None,
                },
            }

    @staticmethod
    def _snapshot_event(
        event: CooperationEvent, content_limit: int | None, detail_budget: int | None
    ) -> dict[str, Any]:
        payload = event.as_dict()
        if content_limit is not None:
            payload["content"] = payload["content"][: max(0, int(content_limit))]
        if detail_budget is not None:
            payload["details"] = sanitize_details(
                payload["details"], limit=max(64, int(detail_budget))
            )
        return payload

    def persistence_stats(self) -> dict[str, Any]:
        """저장소 진단 정보(관측 API 외 내부/테스트용)."""
        writer = self._writer
        if writer is None:
            return {
                "enabled": False,
                "error": None,
                "dropped": 0,
                "written": 0,
                "failures": 0,
                "pending": 0,
            }
        return {
            "enabled": writer.enabled,
            "error": writer.error,
            "dropped": writer.dropped,
            "written": writer.written,
            "failures": writer.failures,
            "pending": writer.pending(),
        }

    def close(self, timeout: float = 2.0) -> None:
        """저장 스레드를 제한 시간 안에서 정리한다."""
        writer = self._writer
        if writer is not None:
            writer.close(timeout=timeout)


def safe_robot_id(robot_id: object) -> str:
    """로봇 ID를 파일명에 안전한 형태로 바꾼다."""
    raw = str(robot_id or "").strip()
    cleaned = _ROBOT_ID_PATTERN.sub("_", raw).strip("._")
    return (cleaned or "robot")[:_MAX_ROBOT_ID_CHARS]


def default_cooperation_state_path(
    robot_id: object, *, env: Mapping[str, str] | None = None
) -> Path:
    """기본 저널 경로 — ``$XDG_STATE_HOME`` 또는 ``~/.local/state`` 아래."""
    environment = os.environ if env is None else env
    state_home = str(environment.get("XDG_STATE_HOME") or "").strip()
    if state_home:
        base = Path(state_home)
    else:
        home = str(environment.get("HOME") or "").strip() or str(Path.home())
        base = Path(home) / ".local" / "state"
    return base / "robo-claw" / "cooperation" / f"{safe_robot_id(robot_id)}.sqlite3"


# 전역 저널 — 에이전트 노드가 lifecycle 에서 configure 하고, 스킬/서비스 핸들러가
# 같은 인스턴스를 공유한다. 기본값은 메모리 전용이라 테스트가 사용자 상태를 쓰지 않는다.
_global_journal: CooperationJournal | None = None
_global_journal_lock = threading.Lock()


def get_cooperation_journal() -> CooperationJournal:
    """전역 협동 저널을 반환한다(없으면 메모리 전용으로 생성)."""
    global _global_journal
    with _global_journal_lock:
        if _global_journal is None:
            _global_journal = CooperationJournal()
        return _global_journal


def configure_cooperation_journal(
    *, local_robot_id: object, path: str | Path | None = None
) -> CooperationJournal:
    """에이전트 노드 lifecycle 에서 전역 저널을 새로 만든다."""
    global _global_journal
    journal = CooperationJournal(local_robot_id=str(local_robot_id or "local"), path=path)
    with _global_journal_lock:
        previous = _global_journal
        _global_journal = journal
    if previous is not None:
        previous.close()
    return journal


def reset_cooperation_journal() -> None:
    """전역 저널을 비운다 (테스트/노드 종료용)."""
    global _global_journal
    with _global_journal_lock:
        previous = _global_journal
        _global_journal = None
    if previous is not None:
        previous.close()
