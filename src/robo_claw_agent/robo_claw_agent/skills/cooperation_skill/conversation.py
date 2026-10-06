"""피어(동료 로봇) 간 자율협동 대화 상태 관리.

각 동료 로봇과의 대화 히스토리와 진행 중인 협업 태스크를 스레드 세이프하게
보관하고, LLM 프롬프트에 주입할 수 있는 컨텍스트 문자열을 생성한다.

ROS2 의존성이 없어 단위 테스트가 가능하다.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# 대화에서 "self"는 이 로봇이 보낸 메시지, "peer"는 동료가 보낸 메시지.
_ROLE_SELF = "self"
_ROLE_PEER = "peer"

# 협업 태스크 상태
_TASK_ACTIVE = "active"
_TASK_COMPLETED = "completed"
_TASK_FAILED = "failed"
_TASK_CANCELLED = "cancelled"
_TASK_EXPIRED = "expired"
_TASK_PARTIAL = "partial"
_TASK_CANCEL_REQUESTED = "cancel_requested"


class PeerConversationManager:
    """동료 로봇별 대화 히스토리와 협업 태스크를 관리한다.

    스레드 세이프(RLock)하며, 동시에 여러 자율협동 루프/스킬이 접근해도 안전하다.
    """

    def __init__(self, max_history_per_peer: int = 30) -> None:
        self._lock = threading.RLock()
        self._max_history = max_history_per_peer
        # peer_name -> list[dict{role, content, ts}]
        self._history: dict[str, list[dict[str, Any]]] = {}
        # task_id -> dict{peer, description, status, created_ts, updated_ts, result, note}
        self._tasks: dict[str, dict[str, Any]] = {}

    # ── 대화 히스토리 ─────────────────────────────────────────────

    def add_message(self, peer_name: str, role: str, content: str) -> None:
        """대화 히스토리에 메시지 한 건을 추가한다.

        role 은 ``"self"``(이 로봇이 보냄) 또는 ``"peer"``(동료가 보냄).
        """
        if not peer_name or not content:
            return
        if role not in (_ROLE_SELF, _ROLE_PEER):
            role = _ROLE_PEER
        with self._lock:
            history = self._history.setdefault(peer_name, [])
            history.append(
                {"role": role, "content": str(content), "ts": time.time()}
            )
            if len(history) > self._max_history:
                del history[: len(history) - self._max_history]

    def get_history(self, peer_name: str, limit: int | None = None) -> list[dict[str, Any]]:
        """특정 동료와의 대화 히스토리를 최신순으로 반환한다."""
        with self._lock:
            history = list(self._history.get(peer_name, []))
        if limit is not None and limit > 0:
            history = history[-limit:]
        return history

    def get_context(self, peer_name: str, limit: int | None = None) -> str:
        """LLM 프롬프트에 주입할 대화 컨텍스트 문자열을 생성한다."""
        history = self.get_history(peer_name, limit)
        if not history:
            return "  • 대화 이력 없음"
        lines = []
        for msg in history:
            who = "나" if msg["role"] == _ROLE_SELF else "동료"
            lines.append(f"  - {who}: {msg['content']}")
        return "\n".join(lines)

    def clear_peer(self, peer_name: str) -> None:
        """특정 동료와의 대화 히스토리를 비운다."""
        with self._lock:
            self._history.pop(peer_name, None)

    def clear(self) -> None:
        """모든 대화 히스토리와 협업 태스크를 비운다."""
        with self._lock:
            self._history.clear()
            self._tasks.clear()

    # ── 협업 태스크 ───────────────────────────────────────────────

    def start_task(self, peer_name: str, description: str) -> str:
        """동료와의 협업 태스크를 시작하고 task_id 를 반환한다."""
        task_id = uuid.uuid4().hex[:12]
        now = time.time()
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "peer": peer_name,
                "description": str(description),
                "status": _TASK_ACTIVE,
                "created_ts": now,
                "updated_ts": now,
                "result": "",
                "note": "",
            }
        logger.info("[Coop] Task started: %s (%s) — %s", task_id, peer_name, description)
        return task_id

    def update_task(self, task_id: str, *, note: str = "", status: str | None = None) -> bool:
        """협업 태스크의 상태/메모를 갱신한다. 없으면 False."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            if note:
                task["note"] = str(note)
            if status is not None:
                task["status"] = status
            task["updated_ts"] = time.time()
        return True

    def complete_task(self, task_id: str, result: str = "") -> bool:
        """협업 태스크를 완료 처리한다."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task["status"] = _TASK_COMPLETED
            task["result"] = str(result)
            task["updated_ts"] = time.time()
        logger.info("[Coop] Task completed: %s — %s", task_id, result)
        return True

    def fail_task(self, task_id: str, note: str = "") -> bool:
        """협업 태스크를 실패 처리한다."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return False
            task["status"] = _TASK_FAILED
            if note:
                task["note"] = str(note)
            task["updated_ts"] = time.time()
        return True

    def get_active_tasks(self, peer_name: str | None = None) -> list[dict[str, Any]]:
        """진행 중인 협업 태스크 목록을 반환한다."""
        with self._lock:
            tasks = [
                dict(t) for t in self._tasks.values() if t["status"] == _TASK_ACTIVE
            ]
        if peer_name is not None:
            tasks = [t for t in tasks if t["peer"] == peer_name]
        return tasks

    def expire_tasks(self, max_age_sec: float = 3600.0) -> int:
        """오래된 비종료 태스크를 만료 처리하고 처리 건수를 반환한다."""
        cutoff = time.time() - max(0.0, float(max_age_sec))
        expired = 0
        with self._lock:
            for task in self._tasks.values():
                if (
                    task["status"] in (_TASK_ACTIVE, _TASK_CANCEL_REQUESTED, _TASK_PARTIAL)
                    and task["updated_ts"] < cutoff
                ):
                    task["status"] = _TASK_EXPIRED
                    task["updated_ts"] = time.time()
                    expired += 1
        return expired

    def get_tasks_context(self, peer_name: str | None = None) -> str:
        """LLM 프롬프트에 주입할 협업 태스크 컨텍스트 문자열을 생성한다."""
        tasks = self.get_active_tasks(peer_name)
        if not tasks:
            return "  • 진행 중인 협업 태스크 없음"
        lines = []
        for t in tasks:
            note = f" (메모: {t['note']})" if t.get("note") else ""
            lines.append(f"  - [{t['task_id']}] {t['peer']}: {t['description']}{note}")
        return "\n".join(lines)

    def snapshot(self) -> dict[str, Any]:
        """디버깅/진단용 전체 상태 스냅샷."""
        with self._lock:
            return {
                "history": {k: list(v) for k, v in self._history.items()},
                "tasks": {k: dict(v) for k, v in self._tasks.items()},
            }


# 전역 공유 대화 관리자 — 자율협동 루프와 autonomous_act BT 루프가 같은 대화 상태를
# 공유하기 위해 사용한다.
_global_conversation: PeerConversationManager | None = None
_global_conversation_lock = threading.Lock()


def get_peer_conversation() -> PeerConversationManager:
    """전역 공유 대화 관리자 싱글턴을 반환한다."""
    global _global_conversation
    with _global_conversation_lock:
        if _global_conversation is None:
            _global_conversation = PeerConversationManager()
        return _global_conversation


def reset_peer_conversation() -> None:
    """전역 대화 관리자를 초기화한다 (테스트용)."""
    global _global_conversation
    with _global_conversation_lock:
        _global_conversation = None
