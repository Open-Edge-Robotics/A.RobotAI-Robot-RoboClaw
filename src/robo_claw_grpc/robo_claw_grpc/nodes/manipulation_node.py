"""robo_claw_grpc 조작(Manipulation) 노드"""

import threading

from builtin_interfaces.msg import Duration
from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rclpy.task import Future
from trajectory_msgs.msg import JointTrajectoryPoint


class RoboClawGrpcManipulationNode(Node):
    """robo_claw_grpc 조작 노드"""

    def __init__(self):
        super().__init__("robo_claw_grpc_manipulation_node")

        try:
            self._callback_group = ReentrantCallbackGroup()
            self._action_client = ActionClient(
                self,
                FollowJointTrajectory,
                "/stretch_controller/follow_joint_trajectory",
                callback_group=self._callback_group,
            )
            self._lock = threading.Lock()
            self.get_logger().info("Manipulation node started")

        except Exception as e:
            self.get_logger().error(f"Manipulation node initialization error: {e}")
            raise e

    def execute_joint_trajectory(self, joint_names: list[str], positions: list[float]) -> bool:
        self.get_logger().info(f"Joint control: {joint_names}, positions: {positions}")

        try:
            if not self._action_client.wait_for_server(timeout_sec=2.0):
                self.get_logger().error("FollowJointTrajectory action server unavailable")
                return False

            goal_msg = FollowJointTrajectory.Goal()
            point = JointTrajectoryPoint()
            point.time_from_start = Duration(sec=2, nanosec=0)
            point.positions = [float(p) for p in positions]

            goal_msg.trajectory.joint_names = joint_names
            goal_msg.trajectory.points.append(point)  # type: ignore

            send_goal_future = self._action_client.send_goal_async(goal_msg)
            send_goal_future.add_done_callback(self._goal_response_callback)
            return True

        except Exception as e:
            self.get_logger().error(f"Joint control error: {e}")
            return False

    def _goal_response_callback(self, future: Future) -> None:
        try:
            goal_handle = future.result()
            if not goal_handle.accepted:  # type: ignore
                self.get_logger().error("Action goal rejected")
                return
            get_result_future = goal_handle.get_result_async()  # type: ignore
            get_result_future.add_done_callback(self._get_result_callback)
        except Exception as e:
            self.get_logger().error(f"Goal response handling error: {e}")

    def _get_result_callback(self, future: Future) -> None:
        try:
            result = future.result().result  # type: ignore
            if result.error_code == FollowJointTrajectory.Result.SUCCESSFUL:
                self.get_logger().info("Joint control succeeded")
            else:
                self.get_logger().warn(f"Joint control failed. Error code: {result.error_code}")
        except Exception as e:
            self.get_logger().error(f"Result handling error: {e}")
