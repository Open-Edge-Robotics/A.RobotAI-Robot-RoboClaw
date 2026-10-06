import logging
import math
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .core import (
    _cancel_active_goal,
    _cancel_cmd_vel_rotation,
    _get_nav2_dependency_error,
    _send_spin_goal,
    _spin_time_allowance_sec,
    _wait_for_goal_result,
    notify_goal_outcome,
)

logger = logging.getLogger(__name__)


class RotateSkill(BaseSkill):
    """제자리 회전 스킬"""

    name = "rotate"
    exclusive_resources = ("base_control",)
    preconditions = ("navigation_ready",)
    postconditions = ("rotation_reached_or_failed",)
    description = (
        "현재 바라보는 방향을 기준으로 지정한 상대 각도 `angle_deg`(도)만큼 제자리 회전합니다. "
        "'90도 돌아', '왼쪽으로 45도 회전'처럼 상대 회전량이 명확할 때만 사용하세요. "
        "북쪽/남쪽/동쪽/서쪽, yaw 180도, 특정 좌표/장소를 바라보라는 절대 방향 정렬 요청에는 `face_direction`을 사용하세요."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "angle_deg": {"type": "number", "description": "현재 방향 기준 상대 회전각(도)"}
        },
        "required": ["angle_deg"],
        "additionalProperties": False,
    }
    side_effects = ("base_rotation",)
    max_retries = 1
    retry_delay_sec = 0.5

    def cancel(self) -> bool:
        """진행 중인 회전 액션 취소"""
        logger.info("RotateSkill cancel requested")
        cmd_cancelled, _ = _cancel_cmd_vel_rotation()
        goal_cancelled, _ = _cancel_active_goal()
        return cmd_cancelled or goal_cancelled

    def recover(self, params: dict[str, Any], error_msg: str) -> None:
        """회전 스킬 최종 실패 시 복구 및 액션 취소"""
        logger.warning("RotateSkill recovery triggered: %s", error_msg)
        _cancel_cmd_vel_rotation()
        _cancel_active_goal()

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (err := self.require_node()):
            return err

        angle_deg = math.fmod(self.get_float_param(params, "angle_deg", 90.0), 360.0)
        angle_rad = math.radians(angle_deg)

        logger.info("Executing rotation: %.1f degrees (%.2f rad)", angle_deg, angle_rad)

        from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

        gate_ok, gate_err = check_navigation_safety_gate(
            self.node, rotation_deg=angle_deg
        )
        if not gate_ok:
            logger.warning("rotate safety gate rejection: %s", gate_err)
            return self.fail_result(gate_err)

        nav2_error = _get_nav2_dependency_error()
        if nav2_error:
            logger.error(nav2_error)
            return self.fail_result(nav2_error)

        from .core import _rotate_via_cmd_vel, _rotate_via_navigation
        from .orientation import normalize_angle_rad

        pose = self.get_map_pose()
        if not pose:
            return self.fail_result("현재 로봇 위치/방향을 가져올 수 없습니다.")

        current_yaw = float(pose["yaw"])
        target_yaw = normalize_angle_rad(current_yaw + angle_rad)

        self.send_user_message(f"🔄 로봇을 {angle_deg:.1f}도 회전시킵니다.")

        # 1순위: cmd_vel direct P-control 회전 (가장 빠르고 제자리 회전 시 NavigateToPose ABORT 유발 없음)
        cmd_ok, cmd_msg = _rotate_via_cmd_vel(self, target_yaw, timeout_sec=10.0)
        if cmd_ok:
            notify_goal_outcome(
                self, True, cmd_msg,
                label="회전",
                success_message=f"✅ 회전을 완료했습니다 ({angle_deg:.1f}도).",
                cancel_message="🛑 회전 작업이 취소되었습니다.",
                fail_message=lambda m: f"❌ 회전 시도 중 문제가 발생했습니다. {m}",
            )
            return self.success_result(f"{angle_deg:.1f}도 회전 완료", angle_deg=angle_deg)

        logger.warning(
            "cmd_vel direct rotation failed (%s), attempting Nav2 Spin fallback",
            cmd_msg,
        )

        # 2순위: Nav2 Spin action 폴백
        allowance = _spin_time_allowance_sec(angle_rad)
        accepted, message, goal_handle = _send_spin_goal(
            self.node, angle_rad, time_allowance_sec=allowance
        )
        success = False
        result_message = ""
        if accepted:
            success, result_message = _wait_for_goal_result(
                goal_handle,
                timeout_sec=allowance + 15.0,
                label="회전",
            )
        else:
            result_message = message

        if success:
            notify_goal_outcome(
                self, True, result_message,
                label="회전",
                success_message=f"✅ 회전을 완료했습니다 ({angle_deg:.1f}도).",
                cancel_message="🛑 회전 작업이 취소되었습니다.",
                fail_message=lambda m: f"❌ 회전 시도 중 문제가 발생했습니다. {m}",
            )
            return self.success_result(f"{angle_deg:.1f}도 회전 완료", angle_deg=angle_deg)

        logger.warning(
            "Nav2 Spin failed (%s), attempting NavigateToPose rotation fallback",
            result_message or message,
        )

        # 3순위: 네비게이션 방식 회전 (NavigateToPose 폴백)
        nav_ok, nav_msg = _rotate_via_navigation(
            self, target_yaw, timeout_sec=20.0, label="상대 회전 내비게이션"
        )
        notify_goal_outcome(
            self, nav_ok, nav_msg,
            label="회전",
            success_message=f"✅ 회전을 완료했습니다 ({angle_deg:.1f}도).",
            cancel_message="🛑 회전 작업이 취소되었습니다.",
            fail_message=lambda m: f"❌ 회전 시도 중 문제가 발생했습니다. {m}",
        )

        if nav_ok:
            return self.success_result(f"{angle_deg:.1f}도 회전 완료", angle_deg=angle_deg)

        if nav_msg == "취소되었습니다.":
            return self.fail_result("회전 작업 취소됨", angle_deg=angle_deg)

        _cancel_active_goal()
        return self.fail_result(f"회전 실패: {nav_msg}", angle_deg=angle_deg)
