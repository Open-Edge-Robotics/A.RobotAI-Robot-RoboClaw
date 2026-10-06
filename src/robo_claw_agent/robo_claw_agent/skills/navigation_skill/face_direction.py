import logging
import math
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .core import (
    _cancel_active_goal,
    _cancel_cmd_vel_rotation,
    _get_nav2_dependency_error,
    _resolve_target_coordinates,
    _send_spin_goal,
    _spin_time_allowance_sec,
    _wait_for_goal_result,
    notify_goal_outcome,
)
from .orientation import (
    direction_to_yaw_rad,
    shortest_angular_delta_rad,
    yaw_to_point_rad,
)

logger = logging.getLogger(__name__)


class FaceDirectionSkill(BaseSkill):
    """절대 방향 또는 대상 좌표를 바라보도록 제자리 회전하는 스킬."""

    name = "face_direction"
    exclusive_resources = ("base_control",)
    preconditions = ("navigation_ready",)
    postconditions = ("heading_reached_or_failed",)
    description = (
        "로봇이 특정 절대 방향 또는 특정 좌표/대상을 바라보도록 제자리 스핀/회전합니다. "
        "'북쪽을 봐', '남쪽으로 방향 맞춰', 'yaw 180도로 맞춰', '창고를 바라봐', '좌표(-1,-4)를 향해 봐' 같은 요청에 사용하세요. "
        "단순 상대 회전량(예: '90도 돌아')이 아니라 최종적으로 바라볼 방향이 지정된 경우 `rotate`보다 이 스킬을 우선 사용하세요. "
        "파라미터: yaw_deg(절대 yaw, 도) 또는 yaw_rad(라디안), direction(north/east/south/west/북쪽/동쪽/남쪽/서쪽), "
        "target_name(시맨틱/RAG 위치명), 또는 x,y(바라볼 좌표). "
        "예: {'direction': 'north'}, {'yaw_deg': 180}, {'target_name': '창고'}, {'x': -1.0, 'y': -4.0}"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "yaw_deg": {"type": "number", "description": "절대 yaw(도)"},
            "yaw_rad": {"type": "number", "description": "절대 yaw(라디안)"},
            "direction": {"type": "string", "enum": ["north", "east", "south", "west", "북쪽", "동쪽", "남쪽", "서쪽"]},
            "target_name": {"type": "string", "description": "기억된 장소명"},
            "x": {"type": "number"},
            "y": {"type": "number"},
        },
        "oneOf": [
            {"required": ["yaw_deg"]}, {"required": ["yaw_rad"]},
            {"required": ["direction"]}, {"required": ["target_name"]},
            {"required": ["x", "y"]},
        ],
        "additionalProperties": False,
    }
    side_effects = ("base_rotation",)

    def cancel(self) -> bool:
        """진행 중인 회전/내비게이션 액션 취소"""
        logger.info("FaceDirectionSkill cancel requested")
        cmd_cancelled, _ = _cancel_cmd_vel_rotation()
        goal_cancelled, _ = _cancel_active_goal()
        return cmd_cancelled or goal_cancelled

    def recover(self, params: dict[str, Any], error_msg: str) -> None:
        """회전/정렬 스킬 최종 실패 시 복구 및 액션 취소"""
        logger.warning("FaceDirectionSkill recovery triggered: %s", error_msg)
        _cancel_cmd_vel_rotation()
        _cancel_active_goal()

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (err := self.require_node()):
            return err

        pose = self.get_map_pose()
        if not pose:
            return self.fail_result("현재 로봇 위치/방향을 가져올 수 없습니다. TF 또는 /odom을 확인하세요.")

        target = self._resolve_target_yaw(params, pose)
        if not target["success"]:
            return target

        current_yaw = float(pose["yaw"])
        target_yaw = float(target["target_yaw"])
        angle_rad = shortest_angular_delta_rad(current_yaw, target_yaw)
        angle_deg = math.degrees(angle_rad)

        if abs(angle_deg) < 1.0:
            return self.success_result(
                f"이미 {target['description']} 방향을 바라보고 있습니다.",
                angle_deg=0.0,
                target_yaw_deg=round(math.degrees(target_yaw), 1),
            )

        from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

        gate_ok, gate_err = check_navigation_safety_gate(
            self.node, rotation_deg=angle_deg
        )
        if not gate_ok:
            logger.warning("face_direction safety gate rejection: %s", gate_err)
            return self.fail_result(gate_err)

        nav2_error = _get_nav2_dependency_error()
        if nav2_error:
            return self.fail_result(nav2_error)

        from .core import _rotate_via_cmd_vel, _rotate_via_navigation

        self.send_user_message(
            f"🧭 {target['description']} 방향을 바라보도록 {angle_deg:.1f}도 회전합니다."
        )

        # 1순위: cmd_vel direct P-control 회전 (가장 빠르고 제자리 회전 시 NavigateToPose ABORT 유발 없음)
        cmd_ok, cmd_msg = _rotate_via_cmd_vel(self, target_yaw, timeout_sec=10.0)
        if cmd_ok:
            notify_goal_outcome(
                self, True, cmd_msg,
                label="방향 정렬",
                success_message=f"✅ {target['description']} 방향 정렬을 완료했습니다.",
                cancel_message="🛑 방향 정렬 회전이 취소되었습니다.",
                fail_message=lambda m: f"❌ 방향 정렬 중 문제가 발생했습니다. {m}",
            )
            return self.success_result(
                f"{target['description']} 방향 정렬 완료",
                method="cmd_vel_pcontrol",
                target_yaw_deg=round(math.degrees(target_yaw), 1),
            )

        logger.warning(
            "cmd_vel direct rotation failed (%s), attempting Nav2 Spin fallback",
            cmd_msg,
        )

        # 2순위: Nav2 Spin action 폴백
        allowance = _spin_time_allowance_sec(angle_rad)
        accepted, message, goal_handle = _send_spin_goal(
            self.node,
            angle_rad,
            time_allowance_sec=allowance,
        )

        success = False
        result_message = ""
        if accepted:
            success, result_message = _wait_for_goal_result(
                goal_handle,
                timeout_sec=allowance + 15.0,
                label="방향 정렬 회전",
            )
        else:
            result_message = message

        if success:
            notify_goal_outcome(
                self, True, result_message,
                label="방향 정렬",
                success_message=f"✅ {target['description']} 방향 정렬을 완료했습니다.",
                cancel_message="🛑 방향 정렬 회전이 취소되었습니다.",
                fail_message=lambda m: f"❌ 방향 정렬 중 문제가 발생했습니다. {m}",
            )
            return self.success_result(
                f"{target['description']} 방향 정렬 완료",
                method="spin",
                target_yaw_deg=round(math.degrees(target_yaw), 1),
            )

        logger.warning(
            "Nav2 Spin failed (%s), attempting NavigateToPose rotation fallback",
            result_message or message,
        )

        # 3순위: 네비게이션 방식 회전 (NavigateToPose 폴백)
        nav_ok, nav_msg = _rotate_via_navigation(
            self, target_yaw, timeout_sec=20.0, label="방향 정렬 내비게이션"
        )
        notify_goal_outcome(
            self, nav_ok, nav_msg,
            label="방향 정렬",
            success_message=f"✅ {target['description']} 방향 정렬을 완료했습니다.",
            cancel_message="🛑 방향 정렬 회전이 취소되었습니다.",
            fail_message=lambda m: f"❌ 방향 정렬 중 문제가 발생했습니다. {m}",
        )

        result = {
            "success": nav_ok,
            "message": "",
            "angle_deg": round(angle_deg, 1),
            "target_yaw_deg": round(math.degrees(target_yaw), 1),
            "current_yaw_deg": round(math.degrees(current_yaw), 1),
            "method": "navigate_to_pose",
        }
        if nav_ok:
            result["message"] = f"{target['description']} 방향 정렬 완료"
        elif nav_msg == "취소되었습니다.":
            result["message"] = "방향 정렬이 취소되었습니다."
        else:
            result["message"] = f"{target['description']} 방향 정렬 실패: {nav_msg}"
        return result

    def _resolve_target_yaw(
        self, params: dict[str, Any], pose: dict[str, Any]
    ) -> dict[str, Any]:
        if "yaw_rad" in params:
            try:
                return {
                    "success": True,
                    "target_yaw": float(params["yaw_rad"]),
                    "description": f"yaw {math.degrees(float(params['yaw_rad'])):.1f}도",
                }
            except (TypeError, ValueError):
                return {"success": False, "message": "yaw_rad는 숫자여야 합니다."}

        if "yaw_deg" in params:
            try:
                yaw_deg = float(params["yaw_deg"])
            except (TypeError, ValueError):
                return {"success": False, "message": "yaw_deg는 숫자여야 합니다."}
            return {
                "success": True,
                "target_yaw": math.radians(yaw_deg),
                "description": f"yaw {yaw_deg:.1f}도",
            }

        if params.get("direction"):
            yaw = direction_to_yaw_rad(params.get("direction"))
            if yaw is None:
                return {
                    "success": False,
                    "message": "direction은 north/east/south/west 또는 북쪽/동쪽/남쪽/서쪽 중 하나여야 합니다.",
                }
            return {
                "success": True,
                "target_yaw": yaw,
                "description": str(params.get("direction")),
            }

        target_name = str(params.get("target_name") or "").strip()
        x = params.get("x")
        y = params.get("y")
        if target_name:
            memory = getattr(self.node, "_memory", None)
            if memory is None:
                return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}
            obj_info = _resolve_target_coordinates(memory, target_name)
            if not obj_info:
                return {"success": False, "message": f"'{target_name}'의 위치를 찾을 수 없습니다."}
            x = obj_info["position"]["x"]
            y = obj_info["position"]["y"]

        if x is not None and y is not None:
            try:
                yaw = yaw_to_point_rad(float(pose["x"]), float(pose["y"]), float(x), float(y))
            except (TypeError, ValueError):
                return {"success": False, "message": "x, y는 숫자여야 합니다."}
            if yaw is None:
                return {"success": False, "message": "바라볼 좌표가 현재 위치와 너무 가깝습니다."}
            desc = f"'{target_name}'" if target_name else f"좌표 ({float(x):.2f}, {float(y):.2f})"
            return {"success": True, "target_yaw": yaw, "description": desc}

        return {
            "success": False,
            "message": "yaw_deg/yaw_rad, direction, target_name 또는 x,y 중 하나가 필요합니다.",
        }
