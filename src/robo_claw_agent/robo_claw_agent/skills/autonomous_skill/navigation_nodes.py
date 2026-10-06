import logging
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .core import BTNode, NodeStatus

logger = logging.getLogger(__name__)


class NavigateNode(BTNode):
    """지정된 좌표로 이동을 수행하는 BT 노드. halt() 시 진행 중인 Nav2 액션을 취소함."""

    def __init__(
        self,
        skill: BaseSkill,
        target_location: str = "",
        x: float | None = None,
        y: float | None = None,
        frame_id: str = "map",
    ) -> None:
        super().__init__("NavigateNode")
        self._skill = skill
        self._target_location = target_location
        self._x = x
        self._y = y
        self._frame_id = frame_id
        self._goal_handle: Any = None
        self._done = False
        self._success = False

    def tick(self) -> NodeStatus:
        if self._done:
            return NodeStatus.SUCCESS if self._success else NodeStatus.FAILURE

        if self._goal_handle is None:
            from robo_claw_agent.skills.navigation_skill.navigate import (
                _resolve_target_coordinates,
                _send_navigation_goal,
            )

            # 목적지 좌표 해석
            target_x, target_y = self._x, self._y
            if self._target_location and not (target_x is not None and target_y is not None):
                memory = getattr(self._skill.node, "_memory", None)
                if memory:
                    obj_info = _resolve_target_coordinates(memory, self._target_location)
                    if obj_info:
                        target_x = obj_info["position"]["x"]
                        target_y = obj_info["position"]["y"]

            if target_x is None or target_y is None:
                logger.error(
                    "[BT] NavigateNode: could not find target coordinates (%s)",
                    self._target_location,
                )
                return NodeStatus.FAILURE

            # Nav2 목표 전송
            logger.info("[BT] NavigateNode: starting movement -> (%.2f, %.2f)", target_x, target_y)
            accepted, message, goal_handle = _send_navigation_goal(
                self._skill.node, target_x, target_y, self._frame_id
            )
            if not accepted:
                logger.error("[BT] NavigateNode: goal rejected - %s", message)
                return NodeStatus.FAILURE

            self._goal_handle = goal_handle

            # 결과 콜백 등록 (비동기 처리)
            def _on_result(future: Any) -> None:
                from action_msgs.msg import GoalStatus

                result = future.result()
                self._done = True
                self._success = result.status == GoalStatus.STATUS_SUCCEEDED
                logger.info("[BT] NavigateNode: movement complete (success=%s)", self._success)

            self._goal_handle.get_result_async().add_done_callback(_on_result)

        return NodeStatus.RUNNING

    def halt(self) -> None:
        if self._goal_handle and not self._done:
            logger.info("[BT] NavigateNode: movement cancellation requested (halt)")
            try:
                self._goal_handle.cancel_goal_async()
            except Exception as e:
                logger.warning("[BT] NavigateNode: error during cancellation request - %s", e)
        self._goal_handle = None
        self._done = False


class ApproachObjectNode(BTNode):
    """Blackboard에 저장된 객체 좌표로 접근하는 BT 노드."""

    def __init__(self, skill: BaseSkill, stop_distance_m: float = 0.8) -> None:
        super().__init__("ApproachObjectNode")
        self._skill = skill
        self._stop_distance_m = stop_distance_m
        self._nav_node: NavigateNode | None = None

    def tick(self) -> NodeStatus:
        target_pos = self._blackboard.get("detected_target_pos")
        if not target_pos:
            logger.warning("[BT] ApproachObjectNode: target coordinates not found in Blackboard")
            return NodeStatus.FAILURE

        if self._nav_node is None:
            self._nav_node = NavigateNode(
                self._skill,
                x=target_pos["x"],
                y=target_pos["y"],
                frame_id=target_pos.get("frame_id", "map"),
            )

        return self._nav_node.tick()

    def halt(self) -> None:
        if self._nav_node:
            self._nav_node.halt()
        self._nav_node = None


class ExploreNode(BTNode):
    """프론티어 기반 자율 탐험을 수행하는 BT 노드.

    프론티어를 찾아 순차적으로 이동하며 탐험을 지속한다.
    프론티어가 소진되면 SUCCESS, 이동 중이면 RUNNING을 반환한다.
    halt() 호출 시 진행 중인 Nav2 목표를 취소한다.
    """

    def __init__(
        self,
        skill: BaseSkill,
        exploration_radius: float = 5.0,
    ) -> None:
        super().__init__("ExploreNode")
        self._skill = skill
        self._radius = exploration_radius
        self._goal_handle: Any = None
        self._goal_done = False
        self._goal_success = False

    def tick(self) -> NodeStatus:
        # 이전 목표가 완료될 때까지 대기
        if self._goal_handle is not None and not self._goal_done:
            return NodeStatus.RUNNING

        # 이전 목표가 실패했으면 계속 진행 (다음 프론티어 시도)
        self._goal_handle = None
        self._goal_done = False

        try:
            import numpy as np
            from nav_msgs.msg import OccupancyGrid

            from robo_claw_agent.agent_node.nav_safety import prepare_stretch_navigation
            from robo_claw_agent.skills.explore_skill import (
                _cluster_frontiers,
                _extract_frontiers,
                _filter_frontiers_by_radius,
                _get_robot_pose,
                _select_nearest_frontier,
            )
            from robo_claw_agent.skills.navigation_skill import _send_navigation_goal
        except ImportError as e:
            logger.error("[BT] ExploreNode: import failed - %s", e)
            return NodeStatus.FAILURE

        map_msg = self._skill.wait_for_message(
            OccupancyGrid, "/map", timeout_sec=5.0, use_transient_local=True
        )
        if not map_msg:
            logger.warning("[BT] ExploreNode: failed to receive map")
            return NodeStatus.FAILURE

        info = map_msg.info
        data = np.array(map_msg.data).reshape((info.height, info.width))

        pose = _get_robot_pose(self._skill)
        if not pose:
            logger.warning("[BT] ExploreNode: failed to receive robot position")
            return NodeStatus.FAILURE
        robot_x, robot_y = pose

        frontiers = _extract_frontiers(
            data, info.resolution, info.origin.position.x, info.origin.position.y
        )
        clusters = _cluster_frontiers(frontiers, cluster_dist=0.5)
        in_radius = _filter_frontiers_by_radius(clusters, robot_x, robot_y, self._radius)
        target = _select_nearest_frontier(in_radius, robot_x, robot_y)

        if target is None:
            logger.info("[BT] ExploreNode: no frontier to explore — exploration complete")
            return NodeStatus.SUCCESS

        tx, ty = target
        logger.info("[BT] ExploreNode: moving to next frontier -> (%.2f, %.2f)", tx, ty)

        ready, reason = prepare_stretch_navigation(self._skill.node)
        if not ready:
            logger.warning("[BT] ExploreNode: navigation safety rejected - %s", reason)
            return NodeStatus.FAILURE

        accepted, message, goal_handle = _send_navigation_goal(self._skill.node, tx, ty, "map")
        if not accepted or goal_handle is None:
            logger.warning("[BT] ExploreNode: goal rejected - %s", message)
            return NodeStatus.RUNNING  # 거부 시 다음 tick에서 재시도

        self._goal_handle = goal_handle

        def _on_result(future: Any) -> None:
            from action_msgs.msg import GoalStatus

            result = future.result()
            self._goal_success = result.status == GoalStatus.STATUS_SUCCEEDED
            self._goal_done = True
            logger.info(
                "[BT] ExploreNode: frontier movement complete (success=%s)", self._goal_success
            )

        self._goal_handle.get_result_async().add_done_callback(_on_result)
        return NodeStatus.RUNNING

    def halt(self) -> None:
        if self._goal_handle and not self._goal_done:
            logger.info("[BT] ExploreNode: exploration cancellation requested (halt)")
            try:
                self._goal_handle.cancel_goal_async()
            except Exception as e:
                logger.warning("[BT] ExploreNode: error during cancellation request - %s", e)
        self._goal_handle = None
        self._goal_done = False
