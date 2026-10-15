"""CLOiD 주행 전 boxing(최소 부피) 자세 prelude 스킬 — 런타임 내부 전용.

CLOiD 는 모션이나 조작을 마친 뒤 상체 자세가 바뀌어 팔이 몸 밖으로 나올 수 있다. 그
상태로 베이스가 움직이면 벽, 문틀, 가구에 팔이 걸릴 수 있으므로, 주행 전에 부피를
최소로 줄이는 boxing 자세(``cloid.move_boxing``)를 먼저 요청한다.

- 등록: ``cloid_navigation_pose_enabled=true``(env ``RC_CLOID_NAVIGATION_POSE_ENABLED``)
  인 배포에서만 launch 가 이 모듈을 등록한다. 스킬 모듈이 없으면 주행 전 자세 게이트도
  함께 비활성화된다.
- 삽입: ``agent_node/nav_safety.py`` 의 ``ensure_cloid_navigation_safety`` 가 베이스
  이동 스킬 직전에 이 스킬을 넣고, raw Nav2 goal 경로는 ``prepare_navigation`` 이 직접
  실행한다.
- confirm 없음: 사용자 승인 게이트가 있는 공개 스킬 ``set_cloid_packing_pose`` 와 달리
  이 스킬은 런타임 안전 절차이므로 confirm 을 요구하지 않는다. 대신 ``is_internal=True``
  로 LLM/사용자에게 노출하지 않는다.
- 한계: ``RunScript`` 는 실제 자세 도달을 확인하지 않는다. 이 스킬은 스크립트 정상
  종료만 보고하고(``completion_confirmed=false``), 자세 복원도 수행하지 않는다.

ROS 인터페이스는 지연 import 하여 이 모듈이 ROS 없이도 import 되도록 유지한다.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills._param_utils import bounded_timeout as _bounded_timeout

logger = logging.getLogger(__name__)

# 스킬 이름은 스킬 카탈로그/정적 검사가 AST 로 읽을 수 있도록 클래스 본문에 리터럴로 둔다.
# nav_safety 의 게이트 상수와 일치해야 하므로 두 값이 같은지 확인하는 테스트가 있다.
NAVIGATION_POSE_SKILL_NAME = "cloid_navigation_pose"

_DEFAULT_TIMEOUT_SEC = 60.0
_MIN_TIMEOUT_SEC = 5.0
_MAX_TIMEOUT_SEC = 180.0


def _request_packing_pose(skill: Any, timeout_sec: float) -> dict[str, Any]:
    """공개 스킬과 동일한 tofu 실행 계약을 사용한다(중복 구현 방지)."""
    from robo_claw_agent.skills.cloid_packing_pose_skill import request_packing_pose

    return request_packing_pose(skill, timeout_sec)


def _is_navigation_pose_ready(node: Any) -> bool:
    """주행 전 자세가 이미 준비됐는지 nav_safety 의 상태를 통해 확인한다.

    같은 이동 구간에서 베이스 이동만 연속하는 체인(예: [navigate_to, navigate_to])에
    자세 요청이 중복 삽입되면 이동마다 60초 스크립트를 다시 실행하게 된다. 상체를
    움직이는 스킬이 성공하면 nav_safety 가 준비 상태를 지우므로, 준비된 상태에서는
    요청을 건너뛰어도 안전하다.
    """
    try:
        from robo_claw_agent.agent_node.nav_safety import is_cloid_navigation_pose_ready
    except Exception as exc:  # noqa: BLE001 - 상태를 읽지 못하면 실행을 건너뛰지 않는다
        logger.debug("Could not read CLOiD navigation pose state: %s", exc)
        return False
    try:
        return bool(is_cloid_navigation_pose_ready(node))
    except Exception as exc:  # noqa: BLE001
        logger.debug("CLOiD navigation pose state check failed: %s", exc)
        return False


class CloidNavigationPoseSkill(BaseSkill):
    """CLOiD 를 주행 전 최소 부피(boxing) 자세로 만드는 실행을 요청한다."""

    name = "cloid_navigation_pose"
    risk_level = "dangerous"
    # LLM/외부 호출에 노출하지 않는다. 주행 안전 게이트가 내부에서만 호출한다.
    is_internal = True
    answer_mode = "action"
    side_effects = ("arm_motion",)
    exclusive_resources = ("cloid_motion",)
    input_schema = {
        "type": "object",
        "properties": {
            "timeout_sec": {
                "type": "number",
                "minimum": 5.0,
                "maximum": 180.0,
            },
        },
        "additionalProperties": False,
    }
    description = (
        "CLOiD 를 주행 전 최소 부피(boxing) 자세로 만드는 실행을 tofu script_runner 에 "
        "요청합니다. 주행 안전 게이트가 베이스 이동 직전에 내부적으로 호출하며, 스크립트 "
        "정상 종료만 확인하고 실제 자세 도달은 확인하지 않습니다."
    )
    preconditions = (
        "cloid 프로필이면서 cloid_navigation_pose_enabled=true 인 배포에서만 등록됩니다.",
        "tofu_control 과 script_runner 가 실행 중이어야 합니다.",
        "주행을 시작하기 전 정지 상태에서만 실행합니다.",
    )
    postconditions = (
        "CLOiD boxing 자세 실행 요청이 발행되며 실제 자세 도달은 확인하지 않습니다.",
        "자세 복원은 수행하지 않습니다.",
    )

    def __init__(self) -> None:
        super().__init__()
        self._publish_lock = threading.Lock()

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (error := self.require_node()) is not None:
            return error

        timeout_sec = _bounded_timeout(
            params.get("timeout_sec"),
            default=_DEFAULT_TIMEOUT_SEC,
            minimum=_MIN_TIMEOUT_SEC,
            maximum=_MAX_TIMEOUT_SEC,
        )
        if timeout_sec is None:
            return self.fail_result(
                f"실행 타임아웃은 {_MIN_TIMEOUT_SEC:.0f}~{_MAX_TIMEOUT_SEC:.0f}초 범위여야 합니다.",
                failure_reason="invalid_timeout",
            )

        if _is_navigation_pose_ready(self.node):
            # 이미 주행 전 자세가 준비되어 있고 그 뒤 상체를 움직이지 않았다.
            return self.success_result(
                "CLOiD 주행 전 boxing 자세가 이미 준비되어 있어 실행을 건너뛰었습니다.",
                status="skipped",
                pose="packing",
                completion_confirmed=False,
            )

        if not self._publish_lock.acquire(blocking=False):
            return self.fail_result(
                "다른 CLOiD 주행 전 자세 요청을 처리 중입니다.",
                failure_reason="motion_busy",
                recoverable=True,
            )

        try:
            result = _request_packing_pose(self, timeout_sec)
        finally:
            self._publish_lock.release()

        if not result.get("success"):
            # 주행 전 자세를 보장하지 못하면 이동을 허용하지 않는다(fail closed).
            logger.warning("CLOiD navigation pose request failed: %s", result.get("message", ""))
        return result
