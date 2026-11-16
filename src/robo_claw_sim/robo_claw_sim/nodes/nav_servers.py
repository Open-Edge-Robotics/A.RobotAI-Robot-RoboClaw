"""1계층(T1) 내비게이션 액션 서버.

Gazebo/Nav2 없이 ``NavigateToPose``/``Spin``/``FollowWaypoints`` 의미론을 검증하기
위한 액션 서버다. 실제 Nav2와 동일한 액션 이름/타입을 사용하므로 RoboClaw의
내비게이션 스킬 코드는 수정 없이 이 서버를 호출한다.

2계층(T2, 실제 Nav2 + 시뮬레이터 센서)은 이 서버를 끄고 Nav2를 띄워서 사용한다.
"""

from __future__ import annotations

import time

from nav2_msgs.action import FollowWaypoints, NavigateToPose, Spin
from nav2_msgs.msg import MissedWaypoint
from rclpy.action import ActionServer
from rclpy.node import Node

from ..nav import GoalPose

_POLL_SEC = 0.02


class SimNavigation:
    """시뮬레이터 세계를 직접 구동하는 액션 서버 묶음."""

    def __init__(self, node: Node, arbiter, world, injections=None) -> None:  # noqa: ANN001
        self.node = node
        self.arbiter = arbiter
        self.world = world
        self.injections = injections or []
        self.navigate_tolerance = float(node.get_parameter("navigate_tolerance").value)
        self.navigate_timeout_sec = float(node.get_parameter("navigate_timeout_sec").value)
        self.spin_timeout_sec = float(node.get_parameter("spin_timeout_sec").value)

        self.navigate_server = ActionServer(
            node, NavigateToPose, "/navigate_to_pose", self._execute_navigate_to_pose
        )
        self.spin_server = ActionServer(node, Spin, "/spin", self._execute_spin)
        self.waypoints_server = ActionServer(
            node, FollowWaypoints, "/follow_waypoints", self._execute_follow_waypoints
        )

    def destroy(self) -> None:
        for server in (self.navigate_server, self.spin_server, self.waypoints_server):
            server.destroy()

    # --------------------------------------------------------------- helpers
    def _injection(self, kind: str, goal_index: int):  # noqa: ANN202
        for injection in self.injections:
            if injection.kind == kind and injection.goal_index == goal_index:
                return injection
        return None

    def _now(self) -> float:
        return float(self.world.elapsed_sec)

    def _sleep_until_next_poll(self) -> None:
        time.sleep(_POLL_SEC)

    # -------------------------------------------------------- NavigateToPose
    def _execute_navigate_to_pose(self, goal_handle):  # noqa: ANN001, ANN202
        result = NavigateToPose.Result()
        request_pose = goal_handle.request.pose.pose
        goal = GoalPose(
            x=request_pose.position.x,
            y=request_pose.position.y,
            yaw=None,
            tolerance=self.navigate_tolerance,
            label="navigate_to_pose",
        )

        if self._injection("nav_reject", 0) is not None:
            goal_handle.abort()
            result.error_msg = "injected nav_reject"
            return result
        if not self.world.is_free(goal.x, goal.y, self.world.robot_radius):
            goal_handle.abort()
            result.error_msg = "goal is occupied"
            return result

        self.arbiter.start_goal(goal, self._now())
        outcome = self._await_completion(goal_handle, self._pose_done, self.navigate_timeout_sec)
        self.arbiter.clear()

        if outcome == "succeeded":
            goal_handle.succeed()
        elif outcome == "canceled":
            goal_handle.canceled()
        else:
            goal_handle.abort()
            result.error_msg = result.error_msg or "navigation timeout"
        return result

    def _pose_done(self) -> bool:
        return self.arbiter.pose_goal_reached()

    # ------------------------------------------------------------------ Spin
    def _execute_spin(self, goal_handle):  # noqa: ANN001, ANN202
        result = Spin.Result()
        target_yaw = self.world.robot.yaw + float(goal_handle.request.target_yaw)
        self.arbiter.start_spin(target_yaw, self._now())

        allowance = (
            float(goal_handle.request.time_allowance.sec)
            + float(goal_handle.request.time_allowance.nanosec) * 1e-9
        )
        timeout = allowance if allowance > 0.0 else self.spin_timeout_sec
        outcome = self._await_completion(goal_handle, self.arbiter.spin_done, timeout)
        self.arbiter.clear()

        if outcome == "succeeded":
            goal_handle.succeed()
        elif outcome == "canceled":
            goal_handle.canceled()
        else:
            goal_handle.abort()
            result.error_code = Spin.Result.TIMEOUT
            result.error_msg = "spin timeout"
        return result

    # ------------------------------------------------------- FollowWaypoints
    def _execute_follow_waypoints(self, goal_handle):  # noqa: ANN001, ANN202
        result = FollowWaypoints.Result()
        poses = list(goal_handle.request.poses)
        loops = max(1, int(goal_handle.request.number_of_loops) or 1)

        for loop_index in range(loops):
            for index, pose_stamped in enumerate(poses):
                if goal_handle.is_cancel_requested:
                    goal_handle.canceled()
                    return result
                goal = GoalPose(
                    x=pose_stamped.pose.position.x,
                    y=pose_stamped.pose.position.y,
                    tolerance=self.navigate_tolerance,
                    label=f"waypoint_{index}",
                )
                feedback = FollowWaypoints.Feedback()
                feedback.current_waypoint = index
                goal_handle.publish_feedback(feedback)

                self.arbiter.start_goal(goal, self._now())
                outcome = self._await_completion(
                    goal_handle, self._pose_done, self.navigate_timeout_sec
                )
                self.arbiter.clear()
                if outcome != "succeeded":
                    missed = MissedWaypoint()
                    missed.index = index
                    result.missed_waypoints.append(missed)
                    if outcome == "canceled":
                        goal_handle.canceled()
                        return result
                    # 한 지점 실패는 나머지 지점 수행 후 abort 로 보고한다(누락 목록 유지).
                    result.error_code = FollowWaypoints.Result.TASK_EXECUTOR_FAILED
                    result.error_msg = f"waypoint {index} failed"
            if loop_index + 1 < loops and goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return result

        if result.missed_waypoints:
            goal_handle.abort()
        else:
            goal_handle.succeed()
        return result

    # ------------------------------------------------------------ completion
    def _await_completion(self, goal_handle, is_done, timeout_sec: float) -> str:  # noqa: ANN001
        """목표 완료/취소/시간 초과를 폴링한다.

        이동 자체는 노드 타이머가 수행하므로 여기서는 상태만 감시한다
        (MultiThreadedExecutor 필요).
        """
        deadline = time.monotonic() + max(timeout_sec, 0.1)
        abort_injection = self._injection("nav_abort", 0)
        abort_at = (
            None if abort_injection is None else self._now() + max(abort_injection.at_sec, 0.0)
        )
        while True:
            if goal_handle.is_cancel_requested:
                return "canceled"
            if is_done():
                return "succeeded"
            if abort_at is not None and self._now() >= abort_at:
                return "aborted"
            if time.monotonic() >= deadline:
                return "timeout"
            self._sleep_until_next_poll()
