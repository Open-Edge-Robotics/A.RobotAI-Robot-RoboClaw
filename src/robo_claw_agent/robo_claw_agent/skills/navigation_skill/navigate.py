import logging
import math
import threading
from typing import Any

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped

from robo_claw_agent.skill_manager import BaseSkill

from .core import (
    _cancel_active_goal,
    _clear_active_goal,
    _distance_to_target,
    _get_arrival_tolerance_m,
    _get_nav2_dependency_error,
    _pose_matches_frame,
    _resolve_target_coordinates,
    _send_follow_waypoints_goal,
    _send_navigation_goal,
    _send_spin_goal,
    _wait_for_future,
    _wait_for_goal_result,
    notify_goal_outcome,
)
from .orientation import shortest_angular_delta_rad, yaw_to_point_rad

logger = logging.getLogger(__name__)


class NavigateToSkill(BaseSkill):
    """지정 좌표 또는 객체명(시맨틱) 기반 이동 스킬 (Phase 9 지원)"""

    name = "navigate_to"
    exclusive_resources = ("base_control",)
    preconditions = ("navigation_ready",)
    postconditions = ("navigation_goal_reached_or_failed",)
    description = (
        "로봇을 지정된 x, y 좌표 또는 객체 이름(target_name)으로 이동시킵니다. "
        "x, y는 반드시 실제 숫자값(float)을 전달해야 합니다. "
        "예: {'target_name': '냉장고'} 또는 {'x': 1.5, 'y': -2.0, 'frame_id': 'map'}"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target_name": {"type": "string", "description": "기억된 장소/객체명"},
            "x": {"type": "number", "description": "map frame x(m)"},
            "y": {"type": "number", "description": "map frame y(m)"},
            "frame_id": {"type": "string", "default": "map"},
            "yaw": {"type": "number", "description": "목표 위치 도착 시 heading 각도(rad)"},
            "align_heading_first": {
                "type": "boolean",
                "default": False,
                "description": "이동 전 목표 방향으로 먼저 제자리 회전을 완료할지 여부",
            },
        },
        "oneOf": [
            {"required": ["target_name"]},
            {"required": ["x", "y"]},
        ],
        "additionalProperties": False,
    }
    side_effects = ("base_motion",)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_name = params.get("target_name")
        x = params.get("x")
        y = params.get("y")
        frame = str(params.get("frame_id") or "map")
        explicit_yaw = params.get("yaw")
        align_heading_first = bool(params.get("align_heading_first", False))

        if target_name:
            if self.node and hasattr(self.node, "_memory"):
                memory = self.node._memory
                obj_info = _resolve_target_coordinates(memory, target_name)
                if obj_info:
                    x = obj_info["position"]["x"]
                    y = obj_info["position"]["y"]
                    logger.info(
                        "Target coordinate lookup succeeded: %s -> (%.2f, %.2f)", target_name, x, y
                    )
                else:
                    return self.fail_result(
                        f"'{target_name}'의 위치를 시맨틱 맵에서 찾을 수 없습니다. 먼저 analyze_scene을 실행하세요."
                    )
            else:
                return self.fail_result("메모리 시스템에 접근할 수 없습니다.")

        if x is None or y is None:
            return self.fail_result("이동을 위해 좌표(x, y) 또는 객체명(target_name)이 필요합니다.")

        if err := self.require_node():
            return err

        nav2_error = _get_nav2_dependency_error()
        if nav2_error:
            logger.error(nav2_error)
            return self.fail_result(nav2_error)

        try:
            x, y = float(x), float(y)
        except (ValueError, TypeError):
            return self.fail_result(
                f"좌표값이 올바르지 않습니다: x={x!r}, y={y!r}. 숫자값을 직접 입력해주세요."
            )

        parsed_explicit_yaw: float | None = None
        if explicit_yaw is not None:
            try:
                parsed_explicit_yaw = float(explicit_yaw)
            except (ValueError, TypeError):
                return self.fail_result(f"yaw 각도값이 올바르지 않습니다: {explicit_yaw!r}")

        from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

        gate_ok, gate_err = check_navigation_safety_gate(self.node, target_x=x, target_y=y)
        if not gate_ok:
            logger.warning("navigate_to safety gate rejection: %s", gate_err)
            return self.fail_result(gate_err)

        dest_str = f"{(target_name + ' ') if target_name else ''}({x:.2f}, {y:.2f})"

        arrival_tolerance_m = _get_arrival_tolerance_m(self.node)
        current_pose = self.get_map_pose()
        frame_matches = _pose_matches_frame(current_pose, frame)
        start_pose = current_pose if (frame_matches and isinstance(current_pose, dict)) else None

        # 이미 도착 허용 오차 안이면 Nav2 goal을 보내지 않는다. 목표가 오차 안인데도
        # Nav2는 yaw 정렬/진행 검사 실패로 ABORTED를 반환할 수 있어(실측 0.19m),
        # 불필요한 실패 루프와 재계획을 막는다.
        if frame_matches and start_pose is not None:
            current_distance = _distance_to_target(start_pose, x, y)
            if current_distance is not None and current_distance <= arrival_tolerance_m:
                logger.info(
                    "Already within arrival tolerance (%.2fm <= %.2fm); skipping navigation goal",
                    current_distance,
                    arrival_tolerance_m,
                )
                self.send_user_message(
                    f"✅ 이미 {dest_str}지점 부근에 있습니다 (오차 {current_distance:.2f}m)."
                )
                return self.success_result(
                    f"{dest_str}지점에 이미 도착해 있습니다 (오차 {current_distance:.2f}m)",
                    x=x,
                    y=y,
                    distance_m=round(current_distance, 3),
                )

        # 진행 방향 yaw 각도 계산. 목표 좌표와 같은 frame의 pose를 써야 한다.
        # (odom 좌표를 map 목표와 섞으면 방향이 어긋나 정렬 실패로 이어진다.)
        computed_yaw: float | None = None
        if start_pose is not None:
            computed_yaw = yaw_to_point_rad(start_pose["x"], start_pose["y"], x, y)

        if parsed_explicit_yaw is not None:
            yaw = parsed_explicit_yaw
        elif computed_yaw is not None:
            yaw = computed_yaw
            logger.info("Computed heading yaw angle toward target point: %.2f rad", yaw)
        else:
            yaw = 0.0

        # align_heading_first 옵션: 대각도 회전이 필요할 때 출발 전 Nav2 Spin으로 먼저 정렬
        # (Nav2 controller의 progress_checker 10초 타임아웃 방지)
        if align_heading_first and computed_yaw is not None and start_pose is not None:
            current_yaw = float(start_pose.get("yaw", 0.0))
            delta_yaw = shortest_angular_delta_rad(current_yaw, computed_yaw)
            if abs(delta_yaw) >= 0.05:
                logger.info(
                    "Performing pre-navigation heading alignment spin: delta=%.2f rad (from %.2f to %.2f)",
                    delta_yaw,
                    current_yaw,
                    computed_yaw,
                )
                self.send_user_message(
                    f"🔄 목표 방향으로 정렬 회전을 시작합니다 ({math.degrees(delta_yaw):.1f}°)."
                )
                spin_ok, spin_msg, spin_handle = _send_spin_goal(self.node, delta_yaw)
                if spin_ok and spin_handle is not None:
                    spin_res_ok, spin_res_msg = _wait_for_goal_result(
                        spin_handle, timeout_sec=60.0, label="사전 회전 정렬"
                    )
                    if not spin_res_ok:
                        logger.warning(
                            "Pre-navigation heading alignment failed: %s; proceeding to navigate goal",
                            spin_res_msg,
                        )
                else:
                    logger.warning(
                        "Failed to send pre-navigation spin goal: %s; proceeding to navigate goal",
                        spin_msg,
                    )

        accepted, message, goal_handle = _send_navigation_goal(self.node, x, y, frame, yaw=yaw)
        if not accepted:
            logger.error("Failed to send navigation goal: %s", message)
            return self.fail_result(message)

        self.send_user_message(f"📍 {dest_str}지점으로 이동을 시작합니다.")

        success, result_message = _wait_for_goal_result(
            goal_handle,
            timeout_sec=300.0,
            label="내비게이션",
        )

        early_abort_diagnosed = False

        # Nav2가 ABORTED를 반환해도 로봇이 이미 도착 허용 오차 안이면 도착으로 판정한다.
        # 좁은 공간·정밀 정렬 실패로 위치는 도달했지만 status만 실패인 경우를 구제한다.
        if not success and result_message != "취소되었습니다.":
            final_pose = self.get_map_pose()
            if _pose_matches_frame(final_pose, frame):
                final_distance = _distance_to_target(final_pose, x, y)
                if final_distance is not None and final_distance <= arrival_tolerance_m:
                    logger.info(
                        "Nav2 reported '%s' but robot is within arrival tolerance "
                        "(%.2fm <= %.2fm); treating as arrived",
                        result_message,
                        final_distance,
                        arrival_tolerance_m,
                    )
                    success = True
                    result_message = "success"
                elif start_pose is not None:
                    # 출발 위치 대비 로봇이 거의 이동하지 못한 채 ABORT된 경우
                    moved_dist = _distance_to_target(final_pose, start_pose["x"], start_pose["y"])
                    if moved_dist is not None and moved_dist < 0.2:
                        early_abort_diagnosed = True
                        diagnostic_hint = (
                            f" (로봇이 {moved_dist:.2f}m만 이동한 상태에서 중단되었습니다. "
                            "제자리 회전 지연 또는 Nav2 진행 검사(progress checker) 시간 초과 가능성을 확인하세요)"
                        )
                        result_message = f"{result_message}{diagnostic_hint}"
                        logger.warning("Early navigation abort diagnosed: %s", diagnostic_hint)

        notify_goal_outcome(
            self,
            success,
            result_message,
            label="내비게이션",
            success_message=f"✅ {dest_str}지점에 무사히 도착했습니다.",
            cancel_message=f"🛑 {dest_str}지점으로의 이동이 취소되었습니다.",
            fail_message=lambda m: f"❌ {dest_str}지점으로 이동 중 문제가 발생했습니다. {m}",
        )

        # RAG 저장 (취소 제외)
        if (
            result_message != "취소되었습니다."
            and self.node
            and hasattr(self.node, "_memory")
            and getattr(self.node, "_enable_rag", False)
        ):
            try:
                coord_str = f"x={x:.2f}, y={y:.2f}"
                if success:
                    rag_text = f"이동 성공 좌표: {coord_str} ({dest_str.strip()})"
                    rag_type = "navigated_coordinate"
                    rag_kind = "place"
                else:
                    rag_text = f"이동 실패 좌표: {coord_str} — {result_message}"
                    rag_type = "blocked_coordinate"
                    rag_kind = "blocked"
                self.node._memory.add_knowledge(  # type: ignore
                    rag_text,
                    {"type": rag_type, "kind": rag_kind, "x": x, "y": y, "source": "navigate_to"},
                )
                logger.info("[navigate_to] RAG save complete (%s)", rag_type)
            except Exception as _rag_e:
                logger.debug("[navigate_to] RAG save failed: %s", _rag_e)

        if success:
            return self.success_result(f"{dest_str}지점으로 이동 완료", x=x, y=y)
        fail_kwargs: dict[str, Any] = {"x": x, "y": y}
        if early_abort_diagnosed:
            fail_kwargs["failure_reason"] = "progress_checker_timeout"
        return self.fail_result(f"{dest_str}지점으로 이동 실패: {result_message}", **fail_kwargs)


class FollowWaypointsSkill(BaseSkill):
    """웨이포인트 연속 이동 스킬"""

    name = "follow_waypoints"
    exclusive_resources = ("base_control",)
    preconditions = ("navigation_ready",)
    postconditions = ("navigation_goal_reached_or_failed",)
    description = (
        "지정된 웨이포인트 목록을 순서대로 연속 이동합니다. "
        "waypoints 파라미터로 좌표 리스트를 전달하세요. "
        "예: {'waypoints': [{'x': 1.0, 'y': 2.0}, {'x': 3.0, 'y': 4.0}]} "
        "각 웨이포인트에 'name'을 추가하면 이동 시 해당 이름으로 안내합니다. "
        "웨이포인트 항목에 `target_name`을 넣어 시맨틱 위치를 사용할 수도 있습니다. "
        "이 스킬은 이동을 시작하는 성격이며, 완료 알림은 별도로 전송될 수 있습니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "waypoints": {
                "type": "array",
                "description": "순서대로 이동할 위치 목록",
                "items": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "target_name": {"type": "string"},
                        "name": {"type": "string"},
                    },
                    "oneOf": [{"required": ["x", "y"]}, {"required": ["target_name"]}],
                },
            },
            "frame_id": {"type": "string", "default": "map"},
        },
        "required": ["waypoints"],
        "additionalProperties": False,
    }
    side_effects = ("base_motion",)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        waypoints_raw: list[dict[str, Any]] = params.get("waypoints", [])
        frame = str(params.get("frame_id") or "map")

        if not waypoints_raw:
            return {"success": False, "message": "waypoints 파라미터가 비어 있습니다."}

        nav2_error = _get_nav2_dependency_error()
        if nav2_error:
            return {"success": False, "message": nav2_error}

        # 메모리에서 시맨틱 좌표 해석
        memory = getattr(self.node, "_memory", None)

        poses: list[PoseStamped] = []
        resolved: list[str] = []

        for wp in waypoints_raw:
            x = wp.get("x")
            y = wp.get("y")
            name = str(wp.get("name") or "")
            target_name = str(wp.get("target_name") or "")

            if target_name and memory:
                obj_info = _resolve_target_coordinates(memory, target_name)
                if obj_info:
                    x = obj_info["position"]["x"]
                    y = obj_info["position"]["y"]
                    name = name or target_name
                else:
                    return {
                        "success": False,
                        "message": f"'{target_name}'의 위치를 시맨틱 맵에서 찾을 수 없습니다.",
                    }

            if x is None or y is None:
                return {
                    "success": False,
                    "message": f"웨이포인트 좌표가 없습니다: {wp}",
                }

            try:
                wp_x = float(x)
                wp_y = float(y)
            except (TypeError, ValueError):
                return self.fail_result(f"웨이포인트 좌표값이 올바르지 않습니다: {wp}")

            from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

            gate_ok, gate_err = check_navigation_safety_gate(
                self.node, target_x=wp_x, target_y=wp_y
            )
            if not gate_ok:
                logger.warning(
                    "follow_waypoints safety gate rejection (%s): %s",
                    name or f"({wp_x},{wp_y})",
                    gate_err,
                )
                return self.fail_result(gate_err)

            pose = PoseStamped()
            pose.header.frame_id = frame
            try:
                pose.header.stamp = self.node.get_clock().now().to_msg()
            except Exception:
                pose.header.stamp.sec = 0
                pose.header.stamp.nanosec = 0
            pose.pose.position.x = wp_x
            pose.pose.position.y = wp_y
            pose.pose.orientation.w = 1.0
            poses.append(pose)
            resolved.append(
                f"{name} ({wp_x:.2f}, {wp_y:.2f})" if name else f"({wp_x:.2f}, {wp_y:.2f})"
            )

        logger.info("Starting waypoint navigation: %d points", len(poses))

        accepted, message, goal_handle = _send_follow_waypoints_goal(self.node, poses)
        if not accepted:
            logger.error("Failed to send waypoint goal: %s", message)
            return {"success": False, "message": message}

        route_str = " → ".join(resolved)
        self.send_user_message(f"📍 웨이포인트 이동을 시작합니다: {route_str}")

        token = getattr(goal_handle, "_goal_token", None)

        def wait_for_completion() -> None:
            result_future = goal_handle.get_result_async()
            ok, result = _wait_for_future(result_future, timeout_sec=600.0, label="웨이포인트 이동")
            if not ok or result is None:
                _cancel_active_goal(token=token)
                self.send_user_message("❌ 웨이포인트 이동 결과를 받지 못했습니다.")
                return

            _clear_active_goal(goal_handle=goal_handle, token=token)

            if result.status == GoalStatus.STATUS_SUCCEEDED:
                missed = list(result.result.missed_waypoints)
                if missed:
                    missed_str = ", ".join(
                        resolved[wp.index] for wp in missed if wp.index < len(resolved)
                    )
                    self.send_user_message(
                        f"⚠️ 웨이포인트 이동 완료. 도달하지 못한 지점: {missed_str}"
                    )
                else:
                    self.send_user_message(f"✅ 모든 웨이포인트 이동 완료: {route_str}")
            elif result.status == GoalStatus.STATUS_CANCELED:
                self.send_user_message("🛑 웨이포인트 이동이 취소되었습니다.")
            else:
                self.send_user_message(f"❌ 웨이포인트 이동 실패 (status={result.status})")

        thread = threading.Thread(target=wait_for_completion, daemon=True)
        thread.start()

        return {
            "success": True,
            "message": f"{len(poses)}개 웨이포인트 이동을 시작했습니다. 도착 시 알려드릴게요.",
            "waypoints": resolved,
        }
