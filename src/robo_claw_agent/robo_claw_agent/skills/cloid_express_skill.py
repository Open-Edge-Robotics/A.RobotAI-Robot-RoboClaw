"""CLOiD 상황별 표현 모션 자동 재생 내부 스킬.

사용자 상호작용 상황(인사·대화·탐색·실패)에 맞는 등록 모션을 카탈로그에서 고르고
실행을 요청한다. 자동 재생 경로에서만 호출되는 **내부 스킬**이며, 태스크당 1회
실행을 전제로 한다. 실제 모션 발행은 검증된 ``execute_cloid_motion`` 경로에
위임하고, 이 스킬은 상황 선택과 반복 회피만 담당한다.

`cloid_expression_enabled=true` 인 배포에서만 launch 가 이 모듈을 등록하므로,
등록 여부 자체가 기능 on/off 게이트다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .cloid_expression_policy import SITUATION_MOTION_IDS, select_expression_motion

logger = logging.getLogger(__name__)

_CATALOG_TIMEOUT_SEC = 3.0
_EXECUTE_TIMEOUT_SEC = 15.0
_EXPRESSION_LOCK = threading.Lock()


class CloidExpressSkill(BaseSkill):
    """상황에 맞는 CLOiD 등록 모션 하나를 골라 실행을 요청한다."""

    name = "cloid_express"
    is_internal = True
    risk_level = "action"
    allow_with_others = False
    answer_mode = "action"
    side_effects = ("arm_motion",)
    exclusive_resources = ("cloid_motion",)
    input_schema = {
        "type": "object",
        "properties": {"situation": {"type": "string", "enum": list(SITUATION_MOTION_IDS)}},
        "required": ["situation"],
        "additionalProperties": False,
    }
    description = (
        "상황(인사·대화·탐색·실패)에 맞는 CLOiD 등록 모션 하나를 골라 실행을 요청합니다. "
        "자동 표현 경로에서만 쓰는 내부 스킬입니다."
    )
    preconditions = (
        "cloid 프로필이고 cloid_expression_enabled=true 일 때만 등록됩니다.",
        "CLOi 모션 카탈로그와 모션 실행 스킬이 함께 등록되어 있어야 합니다.",
    )
    postconditions = ("모션 실행 요청만 확인하며 실제 자세 도달과 완료는 확인하지 않습니다.",)

    def __init__(self) -> None:
        super().__init__()
        self._previous_motion_id: int | None = None

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (error := self.require_node()) is not None:
            return error

        situation = str(params.get("situation", "")).strip()
        if situation not in SITUATION_MOTION_IDS:
            return self.fail_result(
                "알 수 없는 표현 상황입니다.", failure_reason="invalid_situation"
            )

        skills = getattr(self.node, "_skills", None)
        if skills is None:
            return self.fail_result(
                "스킬 매니저에 접근할 수 없습니다.", failure_reason="skill_manager_unavailable"
            )
        if not skills.has_skill("list_cloid_motions") or not skills.has_skill(
            "execute_cloid_motion"
        ):
            return self.fail_result(
                "CLOi 모션 카탈로그/실행 스킬이 등록되어 있지 않습니다.",
                failure_reason="motion_catalog_unavailable",
                situation=situation,
            )

        catalog = skills.execute(
            "list_cloid_motions",
            {"timeout_sec": _CATALOG_TIMEOUT_SEC},
            timeout_sec=_CATALOG_TIMEOUT_SEC + 2.0,
        )
        if not catalog.success:
            return self.fail_result(
                "CLOi 모션 목록을 읽지 못해 표현 모션을 실행하지 않았습니다.",
                failure_reason=str(
                    catalog.result_data.get("failure_reason") or "motion_catalog_unavailable"
                ),
                situation=situation,
            )

        motions = catalog.result_data.get("motions")
        if not isinstance(motions, list):
            return self.fail_result(
                "CLOi 모션 목록 형식이 올바르지 않습니다.",
                failure_reason="invalid_motion_catalog",
                situation=situation,
            )

        motion = select_expression_motion(
            motions, situation, previous_motion_id=self._previous_motion_id
        )
        if motion is None:
            return self.fail_result(
                "이 상황에 맞는 검토된 모션이 카탈로그에 없습니다.",
                failure_reason="no_approved_expression_motion",
                situation=situation,
            )

        if not _EXPRESSION_LOCK.acquire(blocking=False):
            return self.fail_result(
                "다른 표현 모션을 처리 중입니다.",
                failure_reason="motion_busy",
                recoverable=True,
                situation=situation,
            )
        try:
            result = skills.execute(
                "execute_cloid_motion",
                {
                    "motion_name": motion["motion_name"],
                    "motion_id": motion["motion_id"],
                    "confirm": True,
                },
                timeout_sec=_EXECUTE_TIMEOUT_SEC,
            )
        finally:
            _EXPRESSION_LOCK.release()

        if not result.success:
            return self.fail_result(
                f"표현 모션 실행 요청이 실패했습니다: {result.message}",
                failure_reason=str(result.result_data.get("failure_reason") or "motion_failed"),
                situation=situation,
                motion_id=motion["motion_id"],
            )

        self._previous_motion_id = motion["motion_id"]
        return self.success_result(
            f"상황 표현 모션을 요청했습니다: {motion['motion_name']} (ID {motion['motion_id']}).",
            status="sent",
            situation=situation,
            motion_name=motion["motion_name"],
            motion_id=motion["motion_id"],
            completion_confirmed=False,
        )
