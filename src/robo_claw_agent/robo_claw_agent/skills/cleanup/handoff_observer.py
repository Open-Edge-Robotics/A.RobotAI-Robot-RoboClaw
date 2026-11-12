"""정리 위임/시연 요청 관측 믹스인.

``CleanupObserver`` 가 실제 위임 호출 직전/직후의 대기·결과를 같은 활동 상태에
기록하도록 메서드만 제공한다. 활동 저장소/기록기는 관측기가 소유한다.

표시 상태(``waiting_for_peer``/``motion_requested``)는 정리 활동 전용 어휘다.
상대 로봇 태스크 결과 어휘(``envelope.OBSERVED_RESULT_STATUSES``)를 넓히지 않는다.

ROS2/네트워크 의존이 없다.
"""

from __future__ import annotations

from .activity import clip_text
from .observation import (
    EVENT_CLEANUP_DELEGATE_FINISHED,
    EVENT_CLEANUP_DELEGATE_STARTED,
    EVENT_CLEANUP_HANDOFF,
    MAX_REQUEST_ID_CHARS,
    TASK_STATUS_MOTION_REQUESTED,
    TASK_STATUS_REPORTED_TO_MAESTRO,
    TASK_STATUS_UNKNOWN,
    TASK_STATUS_WAITING_FOR_PEER,
    phase_label,
)


class HandoffObservationMixin:
    """위임 결정/호출 시작/호출 종료를 정리 활동 상태 + 저널 이벤트로 남긴다."""

    def handoff(
        self,
        *,
        place: str = "",
        target: str = "",
        handoff_kind: str = "",
        peer: str = "",
        transport: str = "",
        send_path: str = "",
        blocked: bool = False,
        reason: str = "",
        request_id: str = "",
    ) -> None:
        try:
            task_status = ""
            phase = "select_peer"
            if blocked:
                phase = "policy"
            elif send_path == "maestro_report":
                # Maestro 보고는 "원격 작업 완료"가 아니라 보고 대기 상태다.
                task_status = TASK_STATUS_REPORTED_TO_MAESTRO
                phase = "peer_result"
            if self._store is not None:
                self._store.update(
                    self._key(),
                    phase=phase,
                    place=place or None,
                    target=target or None,
                    peer=peer or None,
                    transport=transport or None,
                    task_status=task_status or None,
                    task_id=request_id or None,
                )
            self._record(
                EVENT_CLEANUP_HANDOFF,
                content=reason or f"'{place}' 정리 대상의 처리 경로를 결정했습니다.",
                peer_id=peer,
                task_id=request_id or self._task_id,
                status=task_status,
                details={
                    "phase": phase,
                    "phase_label": phase_label(phase),
                    "place": place,
                    "target": target,
                    "handoff_kind": handoff_kind,
                    "peer": peer,
                    "transport": transport,
                    "send_path": send_path,
                    "blocked": bool(blocked),
                    "observed": False,
                    "failure_reason": reason if blocked else "",
                    "task_status": task_status,
                    "request_id": clip_text(request_id, MAX_REQUEST_ID_CHARS),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("handoff", exc)

    def delegate_started(
        self, *, peer: str = "", transport: str = "", skill: str = "", request_id: str = ""
    ) -> None:
        try:
            if self._store is not None:
                self._store.update(
                    self._key(),
                    phase="wait_peer",
                    peer=peer or None,
                    transport=transport or None,
                    task_id=request_id or None,
                    task_status=TASK_STATUS_WAITING_FOR_PEER,
                    failure_reason="",
                )
            self._record(
                EVENT_CLEANUP_DELEGATE_STARTED,
                content=f"{peer or '동료'}에게 정리 작업을 요청하고 응답을 기다립니다.",
                peer_id=peer,
                task_id=request_id or self._task_id,
                details={
                    "phase": "wait_peer",
                    "phase_label": phase_label("wait_peer"),
                    "peer": peer,
                    "transport": transport,
                    "send_path": skill,
                    "request_id": clip_text(request_id, MAX_REQUEST_ID_CHARS),
                    "task_status": TASK_STATUS_WAITING_FOR_PEER,
                    "observed": False,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("delegate_started", exc)

    def delegate_finished(
        self,
        *,
        peer: str = "",
        request_id: str = "",
        success: bool = False,
        message: str = "",
        duration_ms: float = 0.0,
        motion_requested: bool = False,
        observed: bool = False,
        failure_reason: str = "",
    ) -> None:
        try:
            if motion_requested:
                task_status = TASK_STATUS_MOTION_REQUESTED
                reason = failure_reason
            else:
                # 일반 성공/산문/큐 ack/timeout/예외는 시연 상태를 추론하지 않는다.
                # 결과를 확인하지 못했으므로 waiting_for_peer 를 남기지 않고 unknown.
                task_status = TASK_STATUS_UNKNOWN
                if success and observed:
                    reason = failure_reason
                elif success:
                    reason = failure_reason or "result_unconfirmed"
                else:
                    reason = failure_reason or (
                        "peer_reported_failure" if observed else "result_unconfirmed"
                    )
            if self._store is not None:
                self._store.update(
                    self._key(),
                    phase="peer_result",
                    peer=peer or None,
                    task_id=request_id or None,
                    task_status=task_status,
                    failure_reason=reason,
                    observed=bool(observed),
                    result=message or None,
                )
            self._record(
                EVENT_CLEANUP_DELEGATE_FINISHED,
                content=message or f"{peer or '동료'}의 정리 요청 결과를 확인했습니다.",
                peer_id=peer,
                task_id=request_id or self._task_id,
                status=str(task_status or ""),
                details={
                    "phase": "peer_result",
                    "phase_label": phase_label("peer_result"),
                    "peer": peer,
                    "request_id": clip_text(request_id, MAX_REQUEST_ID_CHARS),
                    "success": bool(success),
                    "observed": bool(observed),
                    "motion_requested": bool(motion_requested),
                    "physical_cleanup_confirmed": False,
                    "motion_completion_confirmed": False,
                    "duration_ms": round(max(0.0, float(duration_ms)), 1),
                    "failure_reason": reason,
                    "task_status": str(task_status or ""),
                    "message": message,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("delegate_finished", exc)
