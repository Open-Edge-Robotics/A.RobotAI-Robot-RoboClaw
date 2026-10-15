"""스킬 매니저 — 기존 import 경로 호환 shim.

구현은 ``robo_claw_agent.skills.base``(BaseSkill/SkillResult) 와
``robo_claw_agent.skills.manager``(SkillManager) 로 분리했다.
외부 모듈이 이 경로로 import 하던 이름을 그대로 재-export 한다.
"""

from .skills.base import (
    BaseSkill,
    SkillResult,
    _standard_failure_data,
    _subscription_slot_cache,
    _SubscriptionSlot,
    _TimeoutSentinel,
    _trace_skill_preconditions,
    _trace_skill_schema,
)
from .skills.manager import SkillManager

__all__ = [
    "BaseSkill",
    "SkillManager",
    "SkillResult",
    "_SubscriptionSlot",
    "_TimeoutSentinel",
    "_standard_failure_data",
    "_subscription_slot_cache",
    "_trace_skill_preconditions",
    "_trace_skill_schema",
]
