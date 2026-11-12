"""지도 목표 요청의 결과 기록기(노드별, 유계).

대시보드는 요청 수락(202)과 실제 도착을 구분해서 본다. 그래서 에이전트는 요청별
기록을 유지하고, ``navigate_to`` 의 실제 결과 증거가 있을 때만 ``reached`` 로
확정한다. 결과를 확인하지 못한 요청(``unknown``)은 지도 목표 lease 를 계속 잡고
있어 새 지도 목표를 자동으로 다시 보낼 수 없다.

이 모듈은 ROS/LMM 의존성이 없다. 스레드 간 공유되므로 자체 락을 쓴다.
"""

import threading
from typing import Any

from .contract import (
    COORD_TOLERANCE_M,
    MapGoalContext,
    now_epoch,
)

STATUS_QUEUED = "queued"
STATUS_PLANNING = "planning"
STATUS_RUNNING = "running"
STATUS_REACHED = "reached"
STATUS_FAILED = "failed"
STATUS_CANCEL_REQUESTED = "cancel_requested"
STATUS_CANCELLED = "cancelled"
STATUS_UNKNOWN = "unknown"

RECORD_STATUSES = (
    STATUS_QUEUED,
    STATUS_PLANNING,
    STATUS_RUNNING,
    STATUS_REACHED,
    STATUS_FAILED,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_UNKNOWN,
)
# 실제 결과가 확정된 상태. ``unknown`` 은 확정이 아니므로 종료로 보지 않는다.
TERMINAL_STATUSES = frozenset({STATUS_REACHED, STATUS_FAILED, STATUS_CANCELLED})
# 실제 주행 결과가 확인되기 전에는 다른 상태로 덮지 않는 보호 상태.
PROTECTED_STATUSES = frozenset({STATUS_UNKNOWN, STATUS_CANCEL_REQUESTED})

# 보관하는 요청 기록 수 상한(오래된 종료 기록부터 버린다).
MAX_RECORDS = 16

_TRACKER_ATTR = "_map_goal_tracker"
_FALLBACK_TRACKERS: dict[int, "MapGoalTracker"] = {}


def build_record(
    context: MapGoalContext,
    *,
    status: str = STATUS_QUEUED,
    message: str = "",
    execution_started: bool = False,
    cancel_confirmed: bool = False,
    reason: str = "",
    progress: int | None = None,
    created_at: float | None = None,
    updated_at: float | None = None,
    can_submit: bool = False,
) -> dict[str, Any]:
    """대시보드/에이전트가 공유하는 요청 기록 형태를 만든다."""
    created = now_epoch() if created_at is None else float(created_at)
    return {
        "request_id": context.request_id,
        "robot_id": context.robot_id,
        "status": status,
        "message": str(message or ""),
        "target": context.target(),
        "geometry_id": context.geometry_id,
        "execution_started": bool(execution_started),
        "cancel_confirmed": bool(cancel_confirmed),
        "reason": str(reason or ""),
        "progress": None if progress is None else max(0, min(100, int(progress))),
        "created_at": created,
        "updated_at": created if updated_at is None else float(updated_at),
        "can_submit": bool(can_submit),
    }


class MapGoalTracker:
    """요청 멱등성/lease/기록 전이를 관리한다."""

    def __init__(self, max_records: int = MAX_RECORDS) -> None:
        self._lock = threading.Lock()
        self._max_records = max(1, int(max_records))
        # 삽입 순서를 유지해 오래된 기록부터 정리한다.
        self._records: dict[str, dict[str, Any]] = {}
        self._fingerprints: dict[str, str] = {}
        self._active_request_id: str | None = None
        # 결과 미확정(unknown) 요청. 이 요청의 권위 있는 종료 증거가 올 때까지
        # 새 지도 목표를 받지 않는다.
        self._lease_request_id: str | None = None

    # ── 조회 ────────────────────────────────────────────────────────────────
    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._records.get(request_id)
            return dict(record) if record is not None else None

    def matches_payload(self, context: MapGoalContext) -> bool:
        """이미 접수된 같은 요청 ID 가 같은 페이로드인지 판정한다."""
        with self._lock:
            return self._fingerprints.get(context.request_id) == context.fingerprint()

    def latest_record(self) -> dict[str, Any] | None:
        """가장 최근에 갱신된 기록(대시보드 상태 노출용)."""
        with self._lock:
            if not self._records:
                return None
            latest = max(self._records.values(), key=lambda item: item.get("updated_at", 0.0))
            return dict(latest)

    # ── 요청 수락 ──────────────────────────────────────────────────────────
    def admit(
        self, context: MapGoalContext, *, now: float | None = None
    ) -> tuple[dict | None, str]:
        """요청을 등록한다. 거부 사유가 있으면 ``(None, reason)`` 을 돌려준다.

        같은 ``request_id`` + 같은 페이로드는 기존 기록을 그대로 돌려주고 절대
        재실행하지 않는다. 같은 ID 의 다른 페이로드와, 이미 진행 중인 다른 요청은
        거부한다.
        """
        current = now_epoch() if now is None else float(now)
        fingerprint = context.fingerprint()
        with self._lock:
            known = self._records.get(context.request_id)
            if known is not None:
                if self._fingerprints.get(context.request_id) == fingerprint:
                    return dict(known), ""
                return None, "same_request_id_different_payload"
            if self._active_request_id is not None:
                return None, "active_request_in_progress"
            if self._lease_request_id is not None:
                return None, "previous_request_unconfirmed"

            record = build_record(context, status=STATUS_QUEUED, created_at=current)
            self._records[context.request_id] = record
            self._fingerprints[context.request_id] = fingerprint
            self._active_request_id = context.request_id
            self._prune_locked()
            return dict(record), ""

    def transition(
        self,
        request_id: str,
        status: str,
        *,
        message: str | None = None,
        reason: str | None = None,
        progress: int | None = None,
        execution_started: bool | None = None,
        cancel_confirmed: bool | None = None,
        authoritative: bool = False,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        """기록 상태를 전이한다. 종료 상태는 덮어쓰지 않는다.

        ``authoritative=True`` 는 "실제 주행 결과/증거로 확인된 전이"라는 뜻이다.
        ``unknown``/``cancel_requested`` 는 결과가 확정되지 않은 상태이므로, 권위
        있는 실제 결과가 아니면 다른 상태로 덮지 않고 lease 도 유지한다. 취소 접수
        (ACK)만으로 ``cancel_confirmed`` 를 True 로 만들지도 않는다.
        """
        if status not in RECORD_STATUSES:
            raise ValueError(f"알 수 없는 지도 목표 상태입니다: {status}")
        current = now_epoch() if now is None else float(now)
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                return None
            previous = str(record.get("status") or "")
            if previous in TERMINAL_STATUSES:
                return dict(record)
            if previous in PROTECTED_STATUSES and status != previous:
                if not authoritative or status not in TERMINAL_STATUSES:
                    # 결과 미확정/취소 진행 상태는 실제 결과 증거로만 종료된다.
                    return dict(record)

            record["status"] = status
            record["updated_at"] = current
            if message is not None:
                record["message"] = str(message)
            if reason is not None:
                record["reason"] = str(reason)
            if progress is not None:
                record["progress"] = max(0, min(100, int(progress)))
            if execution_started is not None:
                record["execution_started"] = bool(execution_started)
            if cancel_confirmed is not None and authoritative:
                # 취소 접수/ACK 는 실제 Nav2 중단 확인이 아니다.
                record["cancel_confirmed"] = bool(cancel_confirmed)

            if status in TERMINAL_STATUSES or status == STATUS_UNKNOWN:
                if self._active_request_id == request_id:
                    self._active_request_id = None
            if status in TERMINAL_STATUSES:
                if self._lease_request_id == request_id:
                    self._lease_request_id = None
                record["can_submit"] = True
            elif status == STATUS_UNKNOWN:
                self._lease_request_id = request_id
                record["can_submit"] = False
            else:
                record["can_submit"] = False
            return dict(record)

    def apply_evidence(self, evidence: Any) -> dict[str, Any] | None:
        """``navigate_to`` 결과의 요청 증거로 기록을 확정한다.

        증거의 ``request_id``/좌표/프레임이 기록과 일치할 때만 반영한다. 일치하지
        않는 증거는 다른 요청/목표의 결과이므로 무시한다(남의 목표로 도착 판정 금지).
        """
        if not isinstance(evidence, dict):
            return None
        request_id = str(evidence.get("request_id") or "")
        record = self.get(request_id)
        if record is None:
            return None
        target = record.get("target") or {}
        frame_id = str(evidence.get("frame_id") or "")
        try:
            x = float(evidence.get("x"))
            y = float(evidence.get("y"))
        except (TypeError, ValueError):
            return None
        if frame_id != str(target.get("frame_id") or ""):
            return None
        if abs(x - float(target.get("x", 0.0))) > COORD_TOLERANCE_M:
            return None
        if abs(y - float(target.get("y", 0.0))) > COORD_TOLERANCE_M:
            return None
        geometry_id = str(evidence.get("geometry_id") or "")
        if geometry_id and geometry_id != str(record.get("geometry_id") or ""):
            return None
        status = str(evidence.get("status") or "")
        if status not in RECORD_STATUSES:
            return None
        return self.transition(
            request_id,
            status,
            message=evidence.get("message"),
            reason=evidence.get("reason"),
            progress=evidence.get("progress"),
            execution_started=evidence.get("execution_started"),
            cancel_confirmed=evidence.get("cancel_confirmed"),
            # 실제 navigate_to 결과에서 나온 증거만 권위 있는 전이로 인정한다.
            authoritative=True,
        )

    def _prune_locked(self) -> None:
        if len(self._records) <= self._max_records:
            return
        protected = {self._active_request_id, self._lease_request_id}
        for request_id in list(self._records.keys()):
            if len(self._records) <= self._max_records:
                break
            record = self._records[request_id]
            if request_id in protected:
                continue
            if str(record.get("status") or "") not in TERMINAL_STATUSES:
                continue
            self._records.pop(request_id, None)
            self._fingerprints.pop(request_id, None)


def get_map_goal_tracker(node: Any) -> MapGoalTracker:
    """노드별 지도 목표 기록기를 반환한다(없으면 만들어 붙인다)."""
    tracker = getattr(node, _TRACKER_ATTR, None)
    if isinstance(tracker, MapGoalTracker):
        return tracker
    tracker = MapGoalTracker()
    try:
        setattr(node, _TRACKER_ATTR, tracker)
    except Exception:  # noqa: BLE001
        # 속성 설정이 막힌 테스트 더블은 id 기반으로 공유한다.
        return _FALLBACK_TRACKERS.setdefault(id(node), tracker)
    return tracker


def map_navigation_request_snapshot(node: Any) -> dict[str, Any] | None:
    """QueryState 에 노출할 지도 목표 기록(없으면 None)."""
    try:
        return get_map_goal_tracker(node).latest_record()
    except Exception:  # noqa: BLE001
        return None
