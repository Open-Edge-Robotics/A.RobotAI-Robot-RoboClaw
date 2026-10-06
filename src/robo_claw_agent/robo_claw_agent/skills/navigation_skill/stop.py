import logging
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .core import _cancel_active_goal, _cancel_cmd_vel_rotation, _get_nav2_dependency_error

logger = logging.getLogger(__name__)


class StopSkill(BaseSkill):
    """로봇 정지 스킬"""

    name = "stop"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = (
        "현재 진행 중인 이동/회전/웨이포인트 작업을 즉시 취소합니다. "
        "긴급 위험 상황이 아니라 일반적인 이동 중단일 때 사용하세요."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        logger.info("Executing stop command")

        cmd_vel_stopped, cmd_vel_message = _cancel_cmd_vel_rotation()
        nav2_error = _get_nav2_dependency_error()
        nav_cancelled = False
        nav_message = ""
        if nav2_error:
            nav_message = nav2_error
        else:
            nav_cancelled, nav_message = _cancel_active_goal()
            if not nav_cancelled and "활성 내비게이션 작업이 없습니다" in nav_message:
                nav_cancelled = True

        if cmd_vel_stopped or nav_cancelled:
            self.send_user_message("🛑 로봇을 즉시 정지시켰습니다.")
            return self.success_result(
                cmd_vel_message if cmd_vel_stopped else nav_message,
                cmd_vel_stopped=cmd_vel_stopped,
                navigation_cancelled=nav_cancelled,
                navigation_message=nav_message,
            )

        return self.fail_result(
            f"정지할 활성 작업을 취소하지 못했습니다: {cmd_vel_message}; {nav_message}",
            failure_reason="cancel_failed",
            recoverable=True,
            cmd_vel_stopped=False,
            navigation_cancelled=False,
        )
