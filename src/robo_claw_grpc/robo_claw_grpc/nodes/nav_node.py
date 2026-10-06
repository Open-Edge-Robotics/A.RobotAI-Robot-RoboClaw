"""robo_claw_grpc 내비게이션 노드

위치 조회 전략 (robo_claw_agent의 get_map_pose()와 동일한 다중 경로):
  1순위: TF2 map → base_link 변환 (실시간, 항상 최신)
  2순위: /amcl_pose 토픽 캐시 (AMCL 수렴 시 신뢰 가능)
  3순위: /odom 토픽 (오도메트리, 드리프트 가능)
"""

import threading

from geometry_msgs.msg import (
    Pose,
    PoseArray,
    PoseStamped,
    PoseWithCovarianceStamped,
    Twist,
)
from nav2_msgs.action import FollowWaypoints, NavigateToPose
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.action import ActionClient
from rclpy.node import Node, QoSProfile
from rclpy.qos import QoSDurabilityPolicy, QoSReliabilityPolicy
from std_msgs.msg import Empty


class RoboClawGrpcNavNode(Node):
    """robo_claw_grpc 내비게이션 노드"""

    def __init__(self, pose_topic: str, navigate_to_topic: str, map_topic: str):
        super().__init__("robo_claw_grpc_nav_node")

        try:
            qos_profile = QoSProfile(
                depth=10,
                reliability=QoSReliabilityPolicy.SYSTEM_DEFAULT,
                durability=QoSDurabilityPolicy.SYSTEM_DEFAULT,
            )
            map_qos_profile = QoSProfile(
                depth=1,
                reliability=QoSReliabilityPolicy.RELIABLE,
                durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            )

            self.pose_subscription = self.create_subscription(
                msg_type=PoseWithCovarianceStamped,
                topic=pose_topic,
                callback=self.pose_callback,
                qos_profile=qos_profile,
            )
            self.map_subscription = self.create_subscription(
                msg_type=OccupancyGrid,
                topic=map_topic,
                callback=self.map_callback,
                qos_profile=map_qos_profile,
            )
            self.navigate_to_subscription = self.create_subscription(
                msg_type=PoseStamped,
                topic=navigate_to_topic,
                callback=self.navigate_to_callback,
                qos_profile=qos_profile,
            )
            self.stop_subscription = self.create_subscription(
                msg_type=Empty,
                topic="/robo_claw_grpc/stop",
                callback=self.stop_callback,
                qos_profile=qos_profile,
            )
            self.waypoints_subscription = self.create_subscription(
                msg_type=PoseArray,
                topic="/robo_claw_grpc/follow_waypoints",
                callback=self.waypoints_callback,
                qos_profile=qos_profile,
            )
            self.initial_pose_subscription = self.create_subscription(
                msg_type=PoseWithCovarianceStamped,
                topic="/robo_claw_grpc/set_initial_pose",
                callback=self.initial_pose_callback,
                qos_profile=qos_profile,
            )

            self.cmd_vel_publisher = self.create_publisher(Twist, "/cmd_vel", 10)
            self.initial_pose_publisher = self.create_publisher(
                PoseWithCovarianceStamped, "/initialpose", 10
            )

            self._action_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")
            self._waypoints_action_client = ActionClient(self, FollowWaypoints, "/follow_waypoints")

            self._lock = threading.Lock()
            self._current_map: OccupancyGrid | None = None
            self._is_navigating = False
            self._send_goal_future = None
            self._get_result_future = None
            self._goal_handle = None
            self._current_pose: PoseWithCovarianceStamped | None = None
            self._latest_odom: Odometry | None = None

            # /odom 상시 구독 — 위치 조회 3순위 폴백용 캐시.
            # 조회 시점에 구독을 만들어 동기 대기하면 async 핸들러가 블로킹되므로
            # 초기화 시 한 번만 구독하고 콜백에서 캐시를 갱신한다.
            odom_qos = QoSProfile(depth=1)
            odom_qos.reliability = QoSReliabilityPolicy.BEST_EFFORT
            self.odom_subscription = self.create_subscription(
                msg_type=Odometry,
                topic="/odom",
                callback=self.odom_callback,
                qos_profile=odom_qos,
            )

            # TF2 인프라 — map → base_link 실시간 변환용
            self._map_frame = "map"
            self._base_frame = "base_link"
            self._tf_buffer = None
            self._tf_listener = None
            try:
                import tf2_ros

                self._tf_buffer = tf2_ros.Buffer()
                self._tf_listener = tf2_ros.TransformListener(self._tf_buffer, self)
                self.get_logger().info(
                    f"TF2 listener ready: {self._map_frame} → {self._base_frame}"
                )
            except Exception as tf_err:
                self.get_logger().warning(
                    f"TF2 init failed, position will rely on /amcl_pose or /odom: {tf_err}"
                )

            self.get_logger().info("Navigation node started")

        except Exception as e:
            self.get_logger().error(f"Navigation node initialization error: {e}")
            raise e

    def pose_callback(self, msg: PoseWithCovarianceStamped) -> None:
        with self._lock:
            self._current_pose = msg

    def odom_callback(self, msg: Odometry) -> None:
        with self._lock:
            self._latest_odom = msg

    def map_callback(self, msg: OccupancyGrid) -> None:
        self.get_logger().info(f"Map received: {msg.info.width}x{msg.info.height}")
        with self._lock:
            self._current_map = msg

    def navigate_to_callback(self, msg: PoseStamped) -> None:
        self.get_logger().info(f"Goal position received via topic: {msg.pose.position}")
        self.send_goal(msg)

    def waypoints_callback(self, msg: PoseArray) -> None:
        self.get_logger().info(f"Received {len(msg.poses)} waypoints via topic")
        self.send_waypoints(msg.poses)  # type: ignore

    def initial_pose_callback(self, msg: PoseWithCovarianceStamped) -> None:
        self.get_logger().info(f"Initial pose set: {msg.pose.pose.position}")
        self.initial_pose_publisher.publish(msg)

    def stop_callback(self, msg: Empty) -> None:
        self.stop_robot()

    def stop_robot(self):
        self.get_logger().warn("Emergency stop!")
        with self._lock:
            self._is_navigating = False
            if self._goal_handle:
                try:
                    self._goal_handle.cancel_goal_async()
                except Exception as e:
                    self.get_logger().error(f"Failed to cancel goal: {e}")
                finally:
                    self._goal_handle = None
        self.cmd_vel_publisher.publish(Twist())

    def send_goal(self, pose: PoseStamped) -> bool:
        goal_msg = NavigateToPose.Goal()
        goal_msg.pose = pose

        if not self._action_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("Nav2 NavigateToPose action server unavailable")
            return False

        with self._lock:
            self._is_navigating = True

        self._send_goal_future = self._action_client.send_goal_async(goal_msg)
        self._send_goal_future.add_done_callback(self.goal_response_callback)
        return True

    def send_waypoints(self, poses: list[Pose]) -> bool:
        goal_msg = FollowWaypoints.Goal()
        current_time = self.get_clock().now().to_msg()

        stamped_poses = []
        for pose in poses:
            stamped_pose = PoseStamped()
            stamped_pose.header.frame_id = "map"
            stamped_pose.header.stamp = current_time
            stamped_pose.pose = pose
            stamped_poses.append(stamped_pose)

        goal_msg.poses = stamped_poses

        if not self._waypoints_action_client.wait_for_server(timeout_sec=5.0):
            self.get_logger().error("Nav2 FollowWaypoints action server unavailable")
            return False

        with self._lock:
            self._is_navigating = True

        self._send_goal_future = self._waypoints_action_client.send_goal_async(goal_msg)
        self._send_goal_future.add_done_callback(self.goal_response_callback)
        return True

    def goal_response_callback(self, future) -> None:
        try:
            goal_handle = future.result()
            if not goal_handle.accepted:
                self.get_logger().warn("Nav2 rejected the goal")
                with self._lock:
                    self._is_navigating = False
                return

            with self._lock:
                self._goal_handle = goal_handle

            self._get_result_future = goal_handle.get_result_async()
            self._get_result_future.add_done_callback(self.get_result_callback)

        except Exception as e:
            self.get_logger().error(f"Goal response handling error: {e}")
            with self._lock:
                self._is_navigating = False

    def get_result_callback(self, future) -> None:
        try:
            result = future.result()
            self.get_logger().info(f"Nav2 completed — status: {result.status}")
        except Exception as e:
            self.get_logger().error(f"Result handling error: {e}")
        finally:
            with self._lock:
                self._is_navigating = False
                self._goal_handle = None
                self._get_result_future = None

    def get_current_pose(self) -> PoseWithCovarianceStamped | None:
        """로봇의 현재 위치를 다중 경로로 조회한다.

        1순위: TF2 map → base_link 변환 (실시간, 항상 최신)
        2순위: /amcl_pose 토픽 캐시 (AMCL 수렴 시 신뢰 가능)
        3순위: /odom 토픽 (오도메트리 폴백)

        SLAM 모드 또는 AMCL 미구동 시에도 TF2/odom으로 위치를 획득할 수 있다.
        """
        # 1순위: TF2 map → base_link 실시간 변환
        if self._tf_buffer is not None:
            try:
                import rclpy.time

                tf = self._tf_buffer.lookup_transform(
                    self._map_frame, self._base_frame, rclpy.time.Time()
                )
                t = tf.transform.translation
                q = tf.transform.rotation
                pose_msg = PoseWithCovarianceStamped()
                pose_msg.header.frame_id = self._map_frame
                pose_msg.header.stamp = self.get_clock().now().to_msg()
                pose_msg.pose.pose.position.x = t.x
                pose_msg.pose.pose.position.y = t.y
                pose_msg.pose.pose.position.z = t.z
                pose_msg.pose.pose.orientation = q
                return pose_msg
            except Exception as e:
                self.get_logger().debug(
                    f"TF2 map→base_link lookup failed, falling back to /amcl_pose: {e}"
                )

        # 2순위: /amcl_pose 캐시
        with self._lock:
            if self._current_pose is not None:
                return self._current_pose

        # 3순위: /odom 캐시 (오도메트리 폴백)
        # 주의: odom 프레임 좌표이므로 map 좌표와 다를 수 있다. 호출부가
        # 프레임을 구분할 수 있도록 header.frame_id를 그대로 보존한다.
        odom = self._get_odom_pose()
        if odom is not None:
            self.get_logger().warning(
                "Falling back to /odom pose — coordinates are in frame "
                f"'{odom.header.frame_id}', not '{self._map_frame}'"
            )
            pose_msg = PoseWithCovarianceStamped()
            pose_msg.header = odom.header
            pose_msg.pose = odom.pose
            return pose_msg

        return None

    def _get_odom_pose(self) -> Odometry | None:
        """상시 구독으로 캐시된 최신 오도메트리 메시지를 반환한다.

        호출마다 구독을 만들고 이벤트를 동기 대기하면, async gRPC 핸들러에서
        호출될 때 이벤트 루프가 최대 1초 블로킹된다. 따라서 노드 초기화 시점에
        구독을 걸어두고 콜백에서 갱신한 캐시만 읽는다.
        """
        with self._lock:
            return self._latest_odom

    def get_current_map(self) -> OccupancyGrid | None:
        with self._lock:
            return self._current_map

    def is_navigating(self) -> bool:
        with self._lock:
            return self._is_navigating or (self._goal_handle is not None)
