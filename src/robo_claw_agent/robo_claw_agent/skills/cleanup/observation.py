"""정리 실행 관측 계약 — 이벤트 종류/단계 이름/표시 상태 어휘.

``tidy_home``/``cleanup_demo`` 는 자율협동 세션과 별개로 실행될 수 있다. 이 모듈은
정리 실행을 자체 실행 ID(run_id)로 관측해 협동 저널의 세션 필드(``session_id``/
``goal``/``active``)를 건드리지 않고, 관측 API(QueryState)가 표시할 활동 상태를
따로 유지한다(``activity`` 모듈).

설계 원칙:

- 기록 실패는 삼킨다. 정리 실행이나 자율 슬롯 해제 경로를 막지 않는다.
- 표시 상태(``motion_requested`` 등)는 정리 활동 전용 어휘다. 상대 로봇 태스크 결과
  어휘(``envelope.OBSERVED_RESULT_STATUSES``)를 넓히지 않는다.
- ``motion_requested`` 는 "요청이 접수됐다"는 뜻일 뿐이며 물리 정리/모션 완료를
  확인하지 않는다(두 플래그는 항상 False).
- 이벤트의 ``session_id`` 는 정리 실행 ID다. 자율협동 세션과 섞지 않는다.

ROS2/네트워크 의존이 없다.
"""

from __future__ import annotations

import logging
from typing import Any

from .activity import PHASE_LABELS, observation_id, phase_label

logger = logging.getLogger(__name__)


SOURCE_TIDY_HOME = "tidy_home"
SOURCE_CLEANUP_DEMO = "cleanup_demo"
SOURCE_AUTONOMOUS_COOPERATE = "autonomous_cooperate"

#: 스냅샷 ``activity_source`` 로 노출 가능한 값. 빈 문자열은 "활동 없음".
ACTIVITY_SOURCES = frozenset(
    {SOURCE_TIDY_HOME, SOURCE_CLEANUP_DEMO, SOURCE_AUTONOMOUS_COOPERATE, ""}
)

# 정리 활동이 표시하는 태스크 상태. 상대 로봇 태스크 상태 어휘와 별개다.
TASK_STATUS_MOTION_REQUESTED = "motion_requested"
TASK_STATUS_WAITING_FOR_PEER = "waiting_for_peer"
TASK_STATUS_CANCEL_REQUESTED = "cancel_requested"
TASK_STATUS_REPORTED_TO_MAESTRO = "reported_to_maestro"
#: 상대 결과를 확인하지 못한 종료 상태(대기 상태를 남기지 않는다).
TASK_STATUS_UNKNOWN = "unknown"
CLEANUP_TASK_STATUSES = frozenset(
    {
        TASK_STATUS_MOTION_REQUESTED,
        TASK_STATUS_WAITING_FOR_PEER,
        TASK_STATUS_CANCEL_REQUESTED,
        TASK_STATUS_REPORTED_TO_MAESTRO,
        TASK_STATUS_UNKNOWN,
    }
)
#: 원본 request_id(protocol 상한 80자)는 details 에만 남긴다.
MAX_REQUEST_ID_CHARS = 80

EVENT_CLEANUP_RUN_STARTED = "cleanup_run_started"
EVENT_CLEANUP_PHASE = "cleanup_phase"
EVENT_CLEANUP_RUN_FINISHED = "cleanup_run_finished"
EVENT_CLEANUP_HANDOFF = "cleanup_handoff"
EVENT_CLEANUP_DELEGATE_STARTED = "cleanup_delegate_started"
EVENT_CLEANUP_DELEGATE_FINISHED = "cleanup_delegate_finished"
EVENT_CLEANUP_DEMO_STARTED = "cleanup_demo_started"
EVENT_CLEANUP_DEMO_PHASE = "cleanup_demo_phase"
EVENT_CLEANUP_DEMO_MOTION_REQUESTED = "cleanup_demo_motion_requested"
EVENT_CLEANUP_DEMO_FAILED = "cleanup_demo_failed"
EVENT_CLEANUP_DEMO_FINISHED = "cleanup_demo_finished"

CLEANUP_EVENT_TYPES = frozenset(
    {
        EVENT_CLEANUP_RUN_STARTED,
        EVENT_CLEANUP_PHASE,
        EVENT_CLEANUP_RUN_FINISHED,
        EVENT_CLEANUP_HANDOFF,
        EVENT_CLEANUP_DELEGATE_STARTED,
        EVENT_CLEANUP_DELEGATE_FINISHED,
        EVENT_CLEANUP_DEMO_STARTED,
        EVENT_CLEANUP_DEMO_PHASE,
        EVENT_CLEANUP_DEMO_MOTION_REQUESTED,
        EVENT_CLEANUP_DEMO_FAILED,
        EVENT_CLEANUP_DEMO_FINISHED,
    }
)


def _safe_debug(message: str, *args: Any) -> None:
    """로거 자체가 깨져도 관측/실행 경로로 예외를 전파하지 않는다."""
    try:
        logger.debug(message, *args)
    except Exception:  # noqa: BLE001 - broken logging sink must not break execution
        pass


def safe_observe(observer: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    """관측 객체 메서드 호출. 관측 객체가 없거나 예외를 던져도 실행을 막지 않는다."""
    if observer is None:
        return None
    try:
        return getattr(observer, method)(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 관측 실패는 실행을 막지 않는다
        _safe_debug("[CleanupObservation] %s failed: %s", method, exc)
        return None


def open_cleanup_observation(
    *,
    source: str,
    run_id: str = "",
    goal: str = "",
    task_id: str = "",
    session_id: str = "",
) -> Any:
    """전역 협동 저널에 연결된 정리 관측기를 만든다(저널이 없어도 동작)."""
    from .observer import CleanupObserver

    store: Any = None
    recorder: Any = None
    robot_id = ""
    try:
        from ..cooperation_skill.journal import get_cooperation_journal

        journal = get_cooperation_journal()
        store = journal.cleanup_activities
        recorder = journal.record
        robot_id = journal.local_robot_id
    except Exception as exc:  # noqa: BLE001 - 저널 초기화 실패가 정리를 막지 않는다
        _safe_debug("[CleanupObservation] journal unavailable: %s", exc)
    return CleanupObserver(
        source=source,
        run_id=run_id,
        goal=goal,
        robot_id=robot_id,
        task_id=task_id,
        session_id=session_id,
        store=store,
        recorder=recorder,
    )


__all__ = [
    "ACTIVITY_SOURCES",
    "CLEANUP_EVENT_TYPES",
    "CLEANUP_TASK_STATUSES",
    "EVENT_CLEANUP_DELEGATE_FINISHED",
    "EVENT_CLEANUP_DELEGATE_STARTED",
    "EVENT_CLEANUP_DEMO_FAILED",
    "EVENT_CLEANUP_DEMO_FINISHED",
    "EVENT_CLEANUP_DEMO_MOTION_REQUESTED",
    "EVENT_CLEANUP_DEMO_PHASE",
    "EVENT_CLEANUP_DEMO_STARTED",
    "EVENT_CLEANUP_HANDOFF",
    "EVENT_CLEANUP_PHASE",
    "EVENT_CLEANUP_RUN_FINISHED",
    "EVENT_CLEANUP_RUN_STARTED",
    "MAX_REQUEST_ID_CHARS",
    "PHASE_LABELS",
    "SOURCE_AUTONOMOUS_COOPERATE",
    "SOURCE_CLEANUP_DEMO",
    "SOURCE_TIDY_HOME",
    "TASK_STATUS_CANCEL_REQUESTED",
    "TASK_STATUS_MOTION_REQUESTED",
    "TASK_STATUS_REPORTED_TO_MAESTRO",
    "TASK_STATUS_UNKNOWN",
    "TASK_STATUS_WAITING_FOR_PEER",
    "observation_id",
    "open_cleanup_observation",
    "phase_label",
    "safe_observe",
]
