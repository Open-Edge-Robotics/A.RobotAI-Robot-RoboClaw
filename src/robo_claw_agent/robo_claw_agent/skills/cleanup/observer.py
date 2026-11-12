"""정리 실행 관측기 — 협동 저널 이벤트 기록과 활동 상태 갱신.

관측기는 정리 실행 하나(run_id)에 묶여 시작/단계/위임/종료 이벤트를 남기고
``CleanupActivityStore`` 를 갱신한다. 기록 실패는 삼켜 정리 실행과 자율 슬롯
해제 경로를 막지 않는다.

ROS2/네트워크 의존이 없다.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from .activity import CleanupActivityStore, clip_text, observation_id
from .handoff_observer import HandoffObservationMixin
from .observation import (
    EVENT_CLEANUP_DEMO_FAILED,
    EVENT_CLEANUP_DEMO_FINISHED,
    EVENT_CLEANUP_DEMO_MOTION_REQUESTED,
    EVENT_CLEANUP_DEMO_PHASE,
    EVENT_CLEANUP_DEMO_STARTED,
    EVENT_CLEANUP_PHASE,
    EVENT_CLEANUP_RUN_FINISHED,
    EVENT_CLEANUP_RUN_STARTED,
    SOURCE_CLEANUP_DEMO,
    TASK_STATUS_MOTION_REQUESTED,
    phase_label,
)

logger = logging.getLogger(__name__)


class CleanupObserver(HandoffObservationMixin):
    """정리 실행 1회를 협동 저널 이벤트 + 활동 상태로 관측한다.

    ``recorder`` 는 ``CooperationJournal.record`` 같은 호출 가능 객체다. 기록기나
    저장소가 없어도(테스트/저널 초기화 실패) 조용히 아무것도 하지 않는다.
    """

    def __init__(
        self,
        *,
        source: str,
        run_id: str = "",
        goal: str = "",
        robot_id: str = "",
        task_id: str = "",
        session_id: str = "",
        store: CleanupActivityStore | None = None,
        recorder: Any = None,
    ) -> None:
        self._source = str(source or "")
        raw_run_id = str(run_id or "").strip() or uuid.uuid4().hex
        self._run_id = observation_id(raw_run_id)
        self._session_id = observation_id(str(session_id or "").strip() or raw_run_id)
        self._robot_id = str(robot_id or "")
        self._task_id = observation_id(task_id)
        self._goal = clip_text(goal)
        self._store = store
        self._recorder = recorder
        self._started = False

    @property
    def source(self) -> str:
        return self._source

    @property
    def run_id(self) -> str:
        return self._run_id

    @property
    def session_id(self) -> str:
        return self._session_id

    def activity(self) -> dict[str, Any] | None:
        return self._store.get(self._key()) if self._store is not None else None

    def start(
        self,
        *,
        content: str = "",
        place: str = "",
        target: str = "",
        peer: str = "",
        task_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            if self._started:
                return
            self._started = True
            if self._store is not None:
                store = self._store
                self._store = None
                claimed = store.begin(
                    source=self._source,
                    run_id=self._run_id,
                    robot_id=self._robot_id,
                    goal=self._goal,
                    phase="start",
                    place=place,
                    target=target,
                    task_id=self._mapped_task_id(task_id),
                    peer=peer,
                )
                if claimed is not None:
                    self._store = store
            event_type = (
                EVENT_CLEANUP_DEMO_STARTED
                if self._source == SOURCE_CLEANUP_DEMO
                else EVENT_CLEANUP_RUN_STARTED
            )
            self._record(
                event_type,
                content=content or f"'{self._goal}' 정리를 시작합니다.",
                task_id=self._mapped_task_id(task_id),
                details={
                    "phase": "start",
                    "goal": self._goal,
                    "place": place,
                    "target": target,
                    **(details or {}),
                },
            )
        except Exception as exc:  # noqa: BLE001 - 관측 실패는 실행을 막지 않는다
            self._warn("start", exc)

    def phase(
        self,
        *,
        phase: str,
        content: str = "",
        place: str = "",
        target: str = "",
        peer: str = "",
        task_id: str = "",
        observed: Any = None,
        failure_reason: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            if self._store is not None:
                self._store.update(
                    self._key(),
                    phase=phase,
                    place=place or None,
                    target=target or None,
                    peer=peer or None,
                    task_id=self._mapped_task_id(task_id) or None,
                    failure_reason=failure_reason or None,
                    observed=observed if isinstance(observed, bool) else None,
                )
            event_type = (
                EVENT_CLEANUP_DEMO_PHASE
                if self._source == SOURCE_CLEANUP_DEMO
                else EVENT_CLEANUP_PHASE
            )
            self._record(
                event_type,
                content=content or f"{phase_label(phase)} 단계입니다.",
                task_id=self._mapped_task_id(task_id),
                details={
                    "phase": str(phase or ""),
                    "phase_label": phase_label(phase),
                    "place": place,
                    "target": target,
                    "peer": peer,
                    "observed": observed,
                    "failure_reason": failure_reason,
                    **(details or {}),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("phase", exc)

    def demo_motion_requested(
        self,
        *,
        place: str = "",
        target: str = "",
        request_id: str = "",
        motion_name: str = "",
        motion_id: Any = None,
    ) -> None:
        try:
            if self._store is not None:
                self._store.update(
                    self._key(),
                    phase="demo_motion",
                    place=place or None,
                    target=target or None,
                    task_id=self._mapped_task_id(request_id) or None,
                    task_status=TASK_STATUS_MOTION_REQUESTED,
                    observed=False,
                )
            self._record(
                EVENT_CLEANUP_DEMO_MOTION_REQUESTED,
                content=(
                    f"'{place}'에서 '{target}' 정리 시연 모션 '{motion_name}' 실행을 "
                    "요청했습니다. 실제 물체 정리와 모션 완료는 확인되지 않았습니다."
                ),
                task_id=self._mapped_task_id(request_id),
                status=TASK_STATUS_MOTION_REQUESTED,
                details={
                    "phase": "demo_motion",
                    "phase_label": phase_label("demo_motion"),
                    "place": place,
                    "target": target,
                    "request_id": clip_text(request_id, 80),
                    "observed": False,
                    "motion_name": motion_name,
                    "motion_id": motion_id,
                    "physical_cleanup_confirmed": False,
                    "motion_completion_confirmed": False,
                    "task_status": TASK_STATUS_MOTION_REQUESTED,
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("demo_motion_requested", exc)

    def finished(
        self,
        *,
        status: str = "finished",
        content: str = "",
        result: str = "",
        failure_reason: str = "",
        task_status: str | None = None,
        duration_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        try:
            if self._store is not None:
                self._store.finish(
                    self._key(),
                    status=status,
                    result=result,
                    failure_reason=failure_reason,
                    task_status=task_status,
                    duration_ms=duration_ms,
                    observed=False,
                )
            if self._source == SOURCE_CLEANUP_DEMO:
                event_type = (
                    EVENT_CLEANUP_DEMO_FAILED if status == "failed" else EVENT_CLEANUP_DEMO_FINISHED
                )
            else:
                event_type = EVENT_CLEANUP_RUN_FINISHED
            self._record(
                event_type,
                content=content or result or f"정리 작업이 {status} 상태로 끝났습니다.",
                status=str(task_status or ""),
                details={
                    "phase": "finish",
                    "phase_label": phase_label("finish"),
                    "status": status,
                    "result": result,
                    "failure_reason": failure_reason,
                    "task_status": str(task_status or ""),
                    # 로컬 종료 관측은 상대 결과를 확인한 것이 아니다.
                    "observed": False,
                    "duration_ms": duration_ms,
                    **(details or {}),
                },
            )
        except Exception as exc:  # noqa: BLE001
            self._warn("finished", exc)

    def _key(self) -> tuple[str, str]:
        return (self._source, self._run_id)

    def _mapped_task_id(self, task_id: str) -> str:
        return observation_id(task_id) if task_id else self._task_id

    def _record(
        self,
        event_type: str,
        *,
        content: str = "",
        peer_id: str = "",
        task_id: str = "",
        status: str = "",
        details: dict[str, Any] | None = None,
    ) -> None:
        recorder = self._recorder
        if recorder is None:
            return
        payload: dict[str, Any] = {"source": self._source, "run_id": self._run_id}
        for key, value in (details or {}).items():
            if value is not None:
                payload[key] = value
        recorder(
            event_type,
            peer_id=str(peer_id or ""),
            task_id=observation_id(task_id),
            session_id=self._session_id,
            status=str(status or ""),
            content=content,
            details=payload,
        )

    @staticmethod
    def _warn(operation: str, exc: Exception) -> None:
        # 로거 자체가 깨져도 관측 실패가 실행 경로로 전파되지 않게 한다.
        try:
            logger.debug("[CleanupObservation] %s failed: %s", operation, exc)
        except Exception:  # noqa: BLE001
            pass


__all__ = ["CleanupObserver"]
