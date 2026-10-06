import logging
import subprocess
import time
from typing import Any

from geometry_msgs.msg import Twist

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)


class EmergencyStopSkill(BaseSkill):
    """비상 정지 스킬 (Phase 12: 안전 가드레일)"""

    name = "emergency_stop"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    risk_level = "dangerous"
    allow_with_others = False
    description = (
        "로봇의 모든 동작을 즉시 중단하고 비상 정지 상태로 전환합니다. "
        "충돌 위험이나 안전 사고 가능성이 있는 긴급 상황에서 최우선으로 사용하세요."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        logger.error("!!! Emergency stop command received (Emergency Stop) !!!")

        if self.node is None:
            return self.fail_result(
                "노드가 설정되지 않아 정지 명령을 보낼 수 없습니다.",
                failure_reason="node_unavailable",
            )

        stop_published = False
        try:
            from robo_claw_agent.skills.navigation_skill.core import _get_cmd_vel_topic

            cmd_vel_topic = _get_cmd_vel_topic(self.node)
            pub = self.node.create_publisher(Twist, cmd_vel_topic, 10)
            stop_msg = Twist()

            for _ in range(5):
                pub.publish(stop_msg)
                time.sleep(0.05)

            logger.info("Stop command (/cmd_vel) published successfully")
            stop_published = True
        except Exception as e:
            logger.error("Failed to publish cmd_vel stop command: %s", e)

        action_cancelled = False
        cancel_message = ""
        try:
            from robo_claw_agent.skills.navigation_skill import _cancel_active_goal  # type: ignore

            canceled, cancel_message = _cancel_active_goal()
            if canceled:
                action_cancelled = True
                logger.info("Internal action cancellation complete: %s", cancel_message)
            elif "활성 내비게이션 작업이 없습니다" in cancel_message:
                # 정지할 goal이 없는 것은 취소 실패가 아니라 이미 정지된 상태다.
                action_cancelled = True
            else:
                nav_result = subprocess.run(
                    ["ros2", "action", "cancel", "/navigate_to_pose"],
                    capture_output=True,
                    check=False,
                    timeout=3.0,
                )
                waypoint_result = subprocess.run(
                    ["ros2", "action", "cancel", "/follow_waypoints"],
                    capture_output=True,
                    check=False,
                    timeout=3.0,
                )
                action_cancelled = (
                    nav_result.returncode == 0 and waypoint_result.returncode == 0
                )
                cancel_message = "CLI action cancel 완료" if action_cancelled else "CLI action cancel 실패"
        except Exception as e:
            cancel_message = f"action cancel 오류: {e}"
            logger.error(cancel_message)

        try:
            self.node._emergency_stop_latched = True
            self.node._emergency_stopped = True
        except Exception:
            pass

        success = stop_published and action_cancelled

        if success:
            return self.success_result(
                "로봇 정지 및 이동 액션 취소가 확인되었습니다. (비상 정지 래치됨)",
                emergency_active=True,
                stop_published=stop_published,
                action_cancelled=action_cancelled,
                cancel_message=cancel_message,
            )
        return self.fail_result(
            "비상 정지 일부 경로가 실패했습니다. 하드웨어 E-stop을 확인하세요.",
            failure_reason="emergency_stop_incomplete",
            recoverable=False,
            emergency_active=True,
            stop_published=stop_published,
            action_cancelled=action_cancelled,
            cancel_message=cancel_message,
        )


class ResetEmergencyStopSkill(BaseSkill):
    """비상 정지 래치 상태를 해제하는 스킬"""

    name = "reset_emergency_stop"
    input_schema = {
        "type": "object",
        "properties": {
            "confirm": {"type": "boolean", "default": True}
        },
        "required": ["confirm"],
        "additionalProperties": False,
    }
    risk_level = "dangerous"
    allow_with_others = False
    description = (
        "비상 정지 래치(latched) 상태를 안전하게 해제하여 로봇의 이동 및 조작 스킬 실행을 다시 허용합니다. "
        "주변 안전 확인 후 confirm=True로 호출해야 합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        confirm = bool(params.get("confirm", False))
        if not confirm:
            return self.fail_result(
                "안전 확인을 위해 confirm=True 파라미터가 필요합니다.",
                failure_reason="confirmation_required",
            )

        if self.node is not None:
            try:
                self.node._emergency_stop_latched = False
                self.node._emergency_stopped = False
            except Exception:
                pass

        logger.info("Emergency stop latch cleared by reset_emergency_stop")
        return self.success_result(
            "비상 정지 래치 상태가 정상적으로 해제되었습니다. 동작 재개가 가능합니다.",
            emergency_active=False,
        )
