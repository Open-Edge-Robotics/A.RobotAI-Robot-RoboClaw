"""동료가 위임한 원격 작업 실행 — 정책 판정, 실행, 결과 기록.

권한/allowlist/위험도 판단은 기존 정책(``policy``)을 그대로 쓴다. 이 모듈이
추가로 하는 일은 판단과 실행 결과를 저널에 남기는 것뿐이며, 실행 권한이나
모션 동작을 바꾸지 않는다. LLM 산문에서 성공을 추론하지 않고 실제 스킬 결과만
상태로 기록한다.

ROS2 의존이 없다(스킬 실행은 노드의 SkillManager 에 위임).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from robo_claw_agent.skills.yield_precheck import precheck_yield_for_move

from .envelope import (
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DENIED,
    TASK_STATUS_FAILED,
    TASK_STATUS_PARTIAL,
    TASK_STATUS_RUNNING,
    normalize_task_status,
)
from .journal import (
    EVENT_POLICY,
    EVENT_TASK_COMPLETED,
    EVENT_TASK_DENIED,
    EVENT_TASK_FAILED,
    EVENT_TASK_PARTIAL,
    EVENT_TASK_STARTED,
    CooperationJournal,
    get_cooperation_journal,
)
from .policy import evaluate_remote_skill

logger = logging.getLogger(__name__)

#: 실행 결과 상태 → 관측 이벤트 타입.
RESULT_EVENT_TYPES = {
    TASK_STATUS_COMPLETED: EVENT_TASK_COMPLETED,
    TASK_STATUS_FAILED: EVENT_TASK_FAILED,
    TASK_STATUS_DENIED: EVENT_TASK_DENIED,
    TASK_STATUS_PARTIAL: EVENT_TASK_PARTIAL,
}


def result_status(result: dict[str, Any]) -> str:
    """실행 결과 dict 를 태스크 상태로 매핑한다."""
    status = normalize_task_status(result.get("task_status"), default="")
    if status:
        return status
    return TASK_STATUS_COMPLETED if result.get("success") else TASK_STATUS_FAILED


def _elapsed_ms(started: float) -> int:
    """monotonic 기준 경과 시간(ms). 실행 지연을 관측에 남기기 위해 쓴다."""
    return max(0, int((time.monotonic() - started) * 1000))


def execute_remote_task(
    *,
    node: Any,
    instruction: str,
    skill_name: str = "",
    skill_params: dict[str, Any] | None = None,
    allow_remote_execution: bool,
    allowed_skills: Any = (),
    journal: CooperationJournal | None = None,
    task_id: str = "",
    message_id: str = "",
    peer_id: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    """동료가 위임한 작업을 실행하고 결과를 저널에 남긴다.

    ``peer_id``/``session_id`` 는 관측 이벤트에 그대로 실린다. 로컬 세션이 아니라
    이 태스크를 요청한 세션에 이벤트를 묶기 위해 호출자가 명시적으로 넘긴다.
    """
    target_journal = journal if journal is not None else get_cooperation_journal()
    if not allow_remote_execution:
        return record_denied(
            journal=target_journal,
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
            message="원격 작업 실행이 안전 정책에 의해 차단되었습니다.",
            reason="remote_task_execution_disabled",
        )
    allowed = set(allowed_skills or ())
    if not allowed or not skill_name or skill_name not in allowed:
        return record_denied(
            journal=target_journal,
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
            message="원격 작업 스킬이 allowlist에 없습니다.",
            reason="skill_not_in_remote_allowlist",
        )
    if not isinstance(skill_params, dict):
        return record_denied(
            journal=target_journal,
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
            message="원격 작업 스킬 파라미터 형식이 잘못되었습니다.",
            reason="invalid_skill_params",
        )
    if node is None or not hasattr(node, "_skills"):
        return record_denied(
            journal=target_journal,
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
            message="스킬 매니저에 접근할 수 없습니다.",
            reason="skill_manager_unavailable",
        )

    get_skill = getattr(node._skills, "get_skill", None)
    if not callable(get_skill):
        return record_denied(
            journal=target_journal,
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
            message=f"원격 작업 스킬을 확인할 수 없습니다: {skill_name}",
            reason="skill_lookup_unavailable",
            skill_name=skill_name,
            extra={"fallback_action": "query_peer_capabilities"},
        )
    skill = get_skill(skill_name)
    if skill is None:
        return record_denied(
            journal=target_journal,
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
            message=f"원격 작업 스킬이 현재 로봇에 등록되어 있지 않습니다: {skill_name}",
            reason="skill_not_registered",
            skill_name=skill_name,
        )
    policy = evaluate_remote_skill(skill, enabled=True, allowlisted=True)
    if not policy.allowed:
        message = (
            f"위험도가 높은 스킬은 원격 실행할 수 없습니다: {skill_name}"
            if policy.reason.startswith("remote_risk_level_")
            else f"원격 작업 정책으로 실행하지 않았습니다: {policy.reason}"
        )
        return record_denied(
            journal=target_journal,
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
            message=message,
            reason=policy.reason,
            skill_name=skill_name,
            extra=policy.as_dict(),
        )

    target_journal.record(
        EVENT_POLICY,
        task_id=task_id,
        message_id=message_id,
        peer_id=peer_id,
        session_id=session_id,
        details={
            "skill_name": skill_name,
            "decision": policy.decision,
            "reason": policy.reason,
            "limits": dict(policy.limits),
        },
    )
    target_journal.record(
        EVENT_TASK_STARTED,
        task_id=task_id,
        message_id=message_id,
        peer_id=peer_id,
        session_id=session_id,
        status=TASK_STATUS_RUNNING,
        content=instruction,
        details={
            "skill_name": skill_name,
            "skill_params": dict(skill_params),
            # 로컬 실행 관측이다(상대 보고 아님).
            "observed": False,
        },
    )
    if task_id:
        target_journal.update_task(task_id, status=TASK_STATUS_RUNNING, observed=False)

    started = time.monotonic()
    try:
        timeout_sec = float(policy.limits.get("max_duration_sec", 120.0))
        # 동료가 요청한 이동도 출발 전에 자리를 확인한다. 장소 이름이 있으면
        # navigate_to 가 직접 묻고, 좌표만 있으면 여기서 시멘틱 맵으로 장소를
        # 되짚어 확인한다. 선확인 실패는 이동을 막지 않는다(fail-open).
        precheck_yield_for_move(node, skill_name, skill_params)
        result = node._skills.execute(skill_name, skill_params, timeout_sec=timeout_sec)
    except Exception as exc:  # noqa: BLE001
        logger.error("[Coop] Task execution error: %s", exc)
        message = f"작업 실행 실패: {exc}"
        target_journal.record(
            EVENT_TASK_FAILED,
            task_id=task_id,
            message_id=message_id,
            peer_id=peer_id,
            session_id=session_id,
            status=TASK_STATUS_FAILED,
            content=message,
            details={
                "skill_name": skill_name,
                "error": str(exc),
                "duration_ms": _elapsed_ms(started),
                "observed": False,
            },
        )
        if task_id:
            target_journal.update_task(
                task_id, status=TASK_STATUS_FAILED, result=message, observed=False
            )
        return {
            "success": False,
            "message": message,
            "skill_name": skill_name,
            "task_status": TASK_STATUS_FAILED,
        }

    response: dict[str, Any] = {
        "success": bool(getattr(result, "success", False)),
        "message": str(getattr(result, "message", "완료"))
        + (f" (지시: {instruction})" if instruction else ""),
        "skill_name": skill_name,
    }
    response.update(policy.as_dict())
    if response["success"]:
        status = TASK_STATUS_COMPLETED
    else:
        # 허용된 실행이 실패했다. 스킬 결과에는 부분 실행 신호가 없으므로 성공으로
        # 추론하지 않고 failed 로 남긴다(partial 은 상대가 보고한 경우에만 쓴다).
        status = TASK_STATUS_FAILED
        response["fallback_action"] = "report_partial_result"
    response["task_status"] = status
    target_journal.record(
        RESULT_EVENT_TYPES[status],
        task_id=task_id,
        message_id=message_id,
        peer_id=peer_id,
        session_id=session_id,
        status=status,
        content=response["message"],
        details={
            "skill_name": skill_name,
            "duration_ms": _elapsed_ms(started),
            "observed": False,
        },
    )
    if task_id:
        target_journal.update_task(
            task_id, status=status, result=response["message"], observed=False
        )
    return response


def record_denied(
    *,
    journal: CooperationJournal | None,
    task_id: str,
    message_id: str,
    message: str,
    reason: str,
    skill_name: str = "",
    extra: dict[str, Any] | None = None,
    peer_id: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    """원격 실행을 거부한 경로를 저널에 남기고 결과 dict 를 만든다."""
    target_journal = journal if journal is not None else get_cooperation_journal()
    details: dict[str, Any] = {"reason": reason, "observed": False}
    if skill_name:
        details["skill_name"] = skill_name
    if extra:
        details.update(extra)
    target_journal.record(
        EVENT_TASK_DENIED,
        task_id=task_id,
        message_id=message_id,
        peer_id=peer_id,
        session_id=session_id,
        status=TASK_STATUS_DENIED,
        content=message,
        details=details,
    )
    if task_id:
        target_journal.update_task(
            task_id, status=TASK_STATUS_DENIED, result=message, observed=False
        )
    response: dict[str, Any] = {
        "success": False,
        "message": message,
        "task_status": TASK_STATUS_DENIED,
        "policy_decision": "deny",
        "policy_reason": reason,
    }
    if skill_name:
        response["skill_name"] = skill_name
    for key, value in (extra or {}).items():
        response.setdefault(key, value)
    return response
