import logging
from typing import Any

import numpy as np

from robo_claw_agent.skill_manager import BaseSkill

from .core import BTNode, NodeStatus
from .globals import _AUTONOMOUS_ACTIVE, _SLEEP_WAKE

logger = logging.getLogger(__name__)


class EmergencyLowBattery(BTNode):
    def __init__(self, skill: BaseSkill, check_only: bool = True) -> None:
        super().__init__("EmergencyLowBattery")
        self._skill = skill
        self._check_only = check_only
        self._warned = False

    def tick(self) -> NodeStatus:
        pct = self._blackboard.get("low_battery_pct", 0)
        if not self._check_only:
            msg = f"[자율 행동] 배터리 잔량이 {pct:.0f}%로 부족합니다. 자율 행동을 안전하게 종료합니다."
            logger.warning("[BT] %s", msg)
            self._skill.send_user_message(msg)
            _AUTONOMOUS_ACTIVE.clear()
            return NodeStatus.FAILURE

        if not self._warned:
            msg = (
                f"[자율 행동] 배터리 신호가 없거나 잔량이 {pct:.0f}%로 부족합니다. "
                "(체크 전용 모드로 자율 행동/협동을 계속 유지합니다)"
            )
            logger.warning("[BT] %s", msg)
            self._skill.send_user_message(msg)
            self._warned = True
        return NodeStatus.SUCCESS


class RunExploreOnce(BTNode):
    """프론티어 1개를 찾아 blocking으로 이동 (최대 60초)."""

    def __init__(self, skill: BaseSkill, exploration_radius: float = 3.0) -> None:
        super().__init__("RunExploreOnce")
        self._skill = skill
        self._radius = exploration_radius

    def tick(self) -> NodeStatus:
        from nav_msgs.msg import OccupancyGrid

        from robo_claw_agent.skills.explore_skill import (
            _EXPLORE_ACTIVE,
            _cluster_frontiers,
            _extract_frontiers,
            _filter_frontiers_by_radius,
            _get_robot_pose,
            _select_nearest_frontier,
        )
        from robo_claw_agent.skills.navigation_skill import (
            _send_navigation_goal,
            _wait_for_goal_result,
        )

        if _EXPLORE_ACTIVE.is_set():
            logger.debug("[BT] ExploreSkill already running — skipping RunExploreOnce")
            return NodeStatus.SUCCESS

        map_msg = self._skill.wait_for_message(
            OccupancyGrid, "/map", timeout_sec=5.0, use_transient_local=True
        )
        if not map_msg:
            return NodeStatus.FAILURE

        info = map_msg.info
        data = np.array(map_msg.data).reshape((info.height, info.width))

        pose = _get_robot_pose(self._skill)
        if not pose:
            return NodeStatus.FAILURE
        robot_x, robot_y = pose

        frontiers = _extract_frontiers(
            data, info.resolution, info.origin.position.x, info.origin.position.y
        )
        clusters = _cluster_frontiers(frontiers, cluster_dist=0.5)
        in_radius = _filter_frontiers_by_radius(clusters, robot_x, robot_y, self._radius)
        target = _select_nearest_frontier(in_radius, robot_x, robot_y)

        if not target:
            logger.info("[BT] No frontier to explore — treating exploration as complete")
            self._blackboard["has_map"] = True
            return NodeStatus.SUCCESS

        tx, ty = target
        logger.info("[BT] Short-term exploration target: (%.2f, %.2f)", tx, ty)
        accepted, _, goal_handle = _send_navigation_goal(self._skill.node, tx, ty, "map")
        if not accepted or goal_handle is None:
            return NodeStatus.FAILURE

        success, _ = _wait_for_goal_result(goal_handle, timeout_sec=60.0, label="RunExploreOnce")
        return NodeStatus.SUCCESS if success else NodeStatus.FAILURE


class LocalObservationNode(BTNode):
    """기억된 순찰 장소가 없을 때 제자리에서 4방향 관찰을 한 번 수행한다."""

    def __init__(self, skill: BaseSkill, scan_factory: Any = None) -> None:
        super().__init__("LocalObservationNode")
        self._skill = skill
        self._scan_factory = scan_factory

    def tick(self) -> NodeStatus:
        if self._blackboard.get("patrol_has_destination", False):
            self._blackboard["stationary_observation_only"] = False
            self._blackboard["stationary_scan_done"] = False
            return NodeStatus.SUCCESS

        self._blackboard["stationary_observation_only"] = True
        if self._blackboard.get("stationary_scan_done", False):
            return NodeStatus.SUCCESS

        self._blackboard["stationary_scan_done"] = True
        node = self._skill.node
        if node is None:
            return NodeStatus.SUCCESS

        self._skill.send_user_message(
            "기억된 순찰 장소가 없어 제자리에서 주변을 관찰합니다."
        )
        try:
            from robo_claw_agent.agent_node.nav_safety import prepare_stretch_navigation

            ready, reason = prepare_stretch_navigation(node)
            if not ready:
                self._blackboard["stationary_scan_result"] = {
                    "success": False,
                    "message": reason,
                }
                logger.warning("[BT] Stationary scan blocked by navigation safety: %s", reason)
                return NodeStatus.SUCCESS

            from robo_claw_agent.skills.perception_skill.scan import ScanRoomSkill

            scanner = (
                self._scan_factory(_AUTONOMOUS_ACTIVE)
                if self._scan_factory is not None
                else ScanRoomSkill(cancel_event=_AUTONOMOUS_ACTIVE)
            )
            scanner.set_node(node)
            result = scanner.execute({"step_deg": 90.0, "settle_sec": 0.5})
            self._blackboard["stationary_scan_result"] = result
            if not result.get("success"):
                logger.warning(
                    "[BT] Stationary scan incomplete: %s", result.get("message", "unknown error")
                )
        except Exception as exc:
            self._blackboard["stationary_scan_result"] = {
                "success": False,
                "message": str(exc),
            }
            logger.exception("[BT] Stationary scan failed")
        return NodeStatus.SUCCESS


class ExecuteDecidedAction(BTNode):
    """task_queue에서 꺼내거나 단일 decided_skill을 실행한다."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("ExecuteDecidedAction")
        self._skill = skill

    def tick(self) -> NodeStatus:
        node = self._skill.node

        task_queue: list[dict[str, Any]] = self._blackboard.get("task_queue", [])
        if task_queue:
            step = task_queue.pop(0)
            self._blackboard["task_queue"] = task_queue
            skill_name = step.get("skill", "")
            params = step.get("params", {})
            reason = self._blackboard.get("decided_reason", "")
            logger.info(
                "[BT] Executing from queue: %s (%d steps remaining)", skill_name, len(task_queue)
            )
        else:
            skill_name = self._blackboard.get("decided_skill", "")
            params = self._blackboard.get("decided_params", {})
            reason = self._blackboard.get("decided_reason", "")

        if skill_name == "none":
            self._blackboard["last_action_result"] = {"noop": True}
            self._blackboard["last_action_success"] = True
            logger.info("[BT] No actionable response required: %s", reason)
            return NodeStatus.SUCCESS
        if not skill_name:
            return NodeStatus.FAILURE

        self._skill.send_user_message(
            f"[자율 행동] {skill_name} 실행 중... (판단: {reason})"
            if reason
            else f"[자율 행동] {skill_name} 실행 중..."
        )

        success = False
        try:
            from robo_claw_agent.agent_node.nav_safety import (
                _uses_stretch_backend,
                ensure_stretch_navigation_safety,
            )

            backend = getattr(node, "_manipulation_backend", None)
            chain = (
                ensure_stretch_navigation_safety(
                    [{"skill": skill_name, "params": params}],
                    node._skills,
                    logger,
                )
                if backend is not None and _uses_stretch_backend(node, backend)
                else [{"skill": skill_name, "params": params}]
            )
            result = None
            for step in chain:
                result = node._skills.execute(
                    step["skill"], step.get("params", {}), timeout_sec=120.0
                )
                if not result.success:
                    break
            if result is None:
                raise RuntimeError("실행할 자율행동 스킬이 없습니다.")
            self._blackboard["last_action_result"] = getattr(result, "result_data", {})
            self._blackboard["last_action_success"] = getattr(result, "success", False)
            success = getattr(result, "success", False)
            if not success:
                self._blackboard["queue_execution_failed"] = True
            logger.info(
                "[BT] Execution complete: %s → %s",
                skill_name,
                "success" if success else "failure",
            )
        except Exception as exc:
            logger.error("[BT] Skill execution error (%s): %s", skill_name, exc)
            self._blackboard["queue_execution_failed"] = True

        task_history = self._blackboard.setdefault("task_history", [])
        task_history.append(
            {
                "cycle": self._blackboard.get("cycle_count", 0),
                "decided_skill": skill_name,
                "decided_params": params,
                "decided_reason": reason,
                "success": success,
            }
        )
        self._blackboard["task_history"] = task_history

        target_key = (
            params.get("target_name")
            or params.get("target_location")
            or f"{params.get('x')},{params.get('y')}"
        )
        repeat_key = f"{skill_name}:{target_key}"
        if success:
            self._blackboard["repeat_failure_key"] = None
            self._blackboard["repeat_failure_count"] = 0
        elif self._blackboard.get("repeat_failure_key") == repeat_key:
            self._blackboard["repeat_failure_count"] = (
                self._blackboard.get("repeat_failure_count", 0) + 1
            )
        else:
            self._blackboard["repeat_failure_key"] = repeat_key
            self._blackboard["repeat_failure_count"] = 1

        return NodeStatus.SUCCESS


class SleepBetweenCycles(BTNode):
    """사이클 간 대기. stop_autonomous 호출 시 즉시 깨어남."""

    def __init__(self, interval_sec: float = 3.0) -> None:
        super().__init__("SleepBetweenCycles")
        self._interval = interval_sec

    def tick(self) -> NodeStatus:
        _SLEEP_WAKE.clear()
        _SLEEP_WAKE.wait(timeout=self._interval)
        return NodeStatus.SUCCESS


class CheckGoalAchieved(BTNode):
    """task_queue가 비어 있으면 자율 행동 루프를 종료한다 (goal 모드 전용)."""

    def __init__(self, skill: BaseSkill) -> None:
        super().__init__("CheckGoalAchieved")
        self._skill = skill

    def tick(self) -> NodeStatus:
        if self._blackboard.get("queue_execution_failed"):
            logger.info("[BT] CheckGoalAchieved: plan execution had failure(s) — stopping loop")
            self._skill.send_user_message(
                "목표 계획 실행 중 실패가 발생하여 자율 행동을 종료합니다."
            )
            _AUTONOMOUS_ACTIVE.clear()
            return NodeStatus.FAILURE
        if self._blackboard.get("goal_achieved"):
            logger.info("[BT] CheckGoalAchieved: goal achieved — stopping autonomous loop")
            self._skill.send_user_message("목표를 달성했습니다. 자율 행동을 종료합니다.")
            _AUTONOMOUS_ACTIVE.clear()
            return NodeStatus.SUCCESS
        if not self._blackboard.get("task_queue"):
            logger.info("[BT] CheckGoalAchieved: task queue empty — plan completed")
            self._blackboard["goal_achieved"] = True
            self._skill.send_user_message("목표 계획을 모두 실행했습니다. 자율 행동을 종료합니다.")
            _AUTONOMOUS_ACTIVE.clear()
            return NodeStatus.SUCCESS
        return NodeStatus.SUCCESS
