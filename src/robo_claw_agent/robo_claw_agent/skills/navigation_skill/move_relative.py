import logging
import math
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .core import (
    _get_nav2_dependency_error,
    _send_navigation_goal,
    _wait_for_goal_result,
    notify_goal_outcome,
)

logger = logging.getLogger(__name__)


class MoveRelativeSkill(BaseSkill):
    """로봇 헤딩 기준 상대 거리 이동 스킬"""

    name = "move_relative"
    exclusive_resources = ("base_control",)
    description = (
        "로봇의 현재 위치와 방향을 기준으로 상대 거리만큼 이동합니다. "
        "'앞으로 Nm', '뒤로 Nm', '왼쪽으로 Nm', '오른쪽으로 Nm' 같은 상대 이동 명령에 사용하세요. "
        "파라미터: forward(float, 미터, 양수=앞, 음수=뒤), lateral(float, 미터, 양수=왼쪽, 음수=오른쪽, 선택). "
        "예: {'forward': 5.0} → 앞으로 5m / {'forward': -0.5} → 뒤로 0.5m / "
        "{'forward': 0.0, 'lateral': 1.0} → 왼쪽으로 1m"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "forward": {"type": "number", "description": "이동 거리(m), 양수=앞"},
            "lateral": {"type": "number", "description": "이동 거리(m), 양수=왼쪽", "default": 0.0},
        },
        "required": ["forward"],
        "additionalProperties": False,
    }
    side_effects = ("base_motion",)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (err := self.require_node()):
            return err

        forward = params.get("forward", 0.0)
        lateral = params.get("lateral", 0.0)

        try:
            forward = float(forward)
            lateral = float(lateral)
        except (ValueError, TypeError):
            return self.fail_result(
                f"파라미터 오류: forward={forward!r}, lateral={lateral!r}",
                failure_reason="invalid_input",
            )

        if forward == 0.0 and lateral == 0.0:
            return self.fail_result(
                "forward 또는 lateral 중 하나는 0이 아니어야 합니다.",
                failure_reason="invalid_input",
            )

        from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

        gate_ok, gate_err = check_navigation_safety_gate(
            self.node, forward=forward, lateral=lateral
        )
        if not gate_ok:
            logger.warning("move_relative safety gate rejection: %s", gate_err)
            return self.fail_result(gate_err, failure_reason="safety_rejected")

        nav2_error = _get_nav2_dependency_error()
        if nav2_error:
            return self.fail_result(nav2_error, failure_reason="backend_unavailable")

        # 현재 위치 + 헤딩 조회. 다른 navigation 스킬과 동일하게 TF map 프레임을
        # 우선 사용하고 실패 시 /odom으로 폴백한다(get_map_pose). move_relative만
        # 별도로 /odom을 직접 읽고 목표를 "odom" 프레임으로 고정 전송하던 이전 방식은
        # map TF가 정상인 상황에서도 나머지 내비게이션 스택과 다른 프레임 컨벤션을 쓰는
        # 불일치를 야기했다.
        pose = self.get_map_pose()
        if not pose:
            return self.fail_result(
                "현재 로봇 위치를 가져올 수 없습니다. TF(map→base_link) 또는 /odom을 확인하세요.",
                failure_reason="localization_unavailable",
                recoverable=True,
            )

        cur_x, cur_y, yaw = pose["x"], pose["y"], pose["yaw"]
        frame = pose["frame"]

        # 로봇 프레임 → (map 또는 odom) 프레임 좌표 변환 (REP-103: x=앞, y=왼쪽)
        target_x = cur_x + forward * math.cos(yaw) - lateral * math.sin(yaw)
        target_y = cur_y + forward * math.sin(yaw) + lateral * math.cos(yaw)

        gate_ok_tgt, gate_err_tgt = check_navigation_safety_gate(
            self.node, target_x=target_x, target_y=target_y
        )
        if not gate_ok_tgt:
            logger.warning("move_relative target forbidden/invalid: %s", gate_err_tgt)
            return self.fail_result(gate_err_tgt, failure_reason="safety_rejected")

        # 이동 방향 설명 문자열
        parts = []
        if forward > 0:
            parts.append(f"앞으로 {forward:.2f}m")
        elif forward < 0:
            parts.append(f"뒤로 {abs(forward):.2f}m")
        if lateral > 0:
            parts.append(f"왼쪽으로 {lateral:.2f}m")
        elif lateral < 0:
            parts.append(f"오른쪽으로 {abs(lateral):.2f}m")
        move_desc = ", ".join(parts)

        logger.info(
            "Relative move: %s | current (%.2f, %.2f, yaw=%.1f°) -> target (%.2f, %.2f)",
            move_desc,
            cur_x,
            cur_y,
            math.degrees(yaw),
            target_x,
            target_y,
        )

        accepted, message, goal_handle = _send_navigation_goal(
            self.node, target_x, target_y, frame
        )
        if not accepted:
            logger.error("Failed to send relative move goal: %s", message)
            return self.fail_result(message, failure_reason="goal_rejected", recoverable=True)

        self.send_user_message(
            f"📍 {move_desc} 이동을 시작합니다. "
            f"(목표 좌표: {target_x:.2f}, {target_y:.2f})"
        )

        success, result_message = _wait_for_goal_result(
            goal_handle,
            timeout_sec=120.0,
            label="상대 이동",
        )
        notify_goal_outcome(
            self, success, result_message,
            label="상대 이동",
            success_message=f"✅ {move_desc} 이동 완료!",
            cancel_message=f"🛑 {move_desc} 이동이 취소되었습니다.",
            fail_message=lambda m: f"❌ {move_desc} 이동 중 문제가 발생했습니다. {m}",
        )

        from_to = {
            "from": {"x": round(cur_x, 2), "y": round(cur_y, 2)},
            "to": {"x": round(target_x, 2), "y": round(target_y, 2)},
        }
        if success:
            return self.success_result(f"{move_desc} 이동 성공", **from_to)
        return self.fail_result(
            f"{move_desc} 이동 실패: {result_message}",
            failure_reason="cancelled" if "취소" in result_message else "execution_failed",
            recoverable=True,
            **from_to,
        )
