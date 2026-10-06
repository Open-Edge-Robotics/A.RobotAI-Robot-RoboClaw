"""수신측 협동 작업의 위험도 기반 실행 정책."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CooperationPolicyDecision:
    """원격 작업을 실행할지와 적용할 제한을 표현한다."""

    decision: str
    reason: str
    limits: dict[str, Any] = field(default_factory=dict)
    fallback_action: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision in {"allow", "allow_with_limits"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_decision": self.decision,
            "policy_reason": self.reason,
            **({"execution_limits": dict(self.limits)} if self.limits else {}),
            **({"fallback_action": self.fallback_action} if self.fallback_action else {}),
        }


def evaluate_remote_skill(
    skill: Any,
    *,
    enabled: bool,
    allowlisted: bool,
    max_duration_sec: float = 120.0,
) -> CooperationPolicyDecision:
    """수신 로봇의 로컬 정책으로 원격 실행을 평가한다.

    ``dangerous``와 ``write``는 계속 fail-closed로 유지하고, 일반 action은
    실행 시간 제한을 가진 제한 허용으로 처리한다. 정책은 LLM 응답이 아니라
    수신측에서만 결정된다.
    """
    if not enabled:
        return CooperationPolicyDecision(
            "deny",
            "remote_task_execution_disabled",
            fallback_action="query_peer_status",
        )
    if not allowlisted:
        return CooperationPolicyDecision(
            "deny",
            "skill_not_in_remote_allowlist",
            fallback_action="query_peer_capabilities",
        )
    risk = str(getattr(skill, "risk_level", "action") or "action").lower()
    if risk in {"dangerous", "write"}:
        return CooperationPolicyDecision(
            "deny",
            f"remote_risk_level_{risk}_requires_local_authority",
            fallback_action="report_and_request_local_confirmation",
        )
    if getattr(skill, "requires_remote_confirmation", False) is True:
        return CooperationPolicyDecision(
            "needs_confirmation",
            "skill_requires_local_confirmation",
            fallback_action="report_and_request_local_confirmation",
        )

    declared_timeout = getattr(skill, "remote_max_duration_sec", None)
    if isinstance(declared_timeout, (int, float)) and not isinstance(declared_timeout, bool):
        try:
            max_duration_sec = min(max_duration_sec, max(1.0, float(declared_timeout)))
        except (TypeError, ValueError):
            pass
    if risk == "read":
        return CooperationPolicyDecision("allow", "read_only_remote_skill")
    return CooperationPolicyDecision(
        "allow_with_limits",
        "action_allowed_with_execution_budget",
        limits={"max_duration_sec": max_duration_sec, "max_retries": 0},
        fallback_action="report_partial_result",
    )
