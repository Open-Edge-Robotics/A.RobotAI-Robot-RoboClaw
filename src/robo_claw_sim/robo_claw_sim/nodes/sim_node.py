"""RoboClaw 전용 시뮬레이터 ROS 2 노드.

Gazebo 없이 RoboClaw가 실제로 소비하는 인터페이스만 제공한다.

발행: /odom, /tf(odom->base_link), /tf_static(map->odom, base_link->sensor),
      /scan, /imu, /map, /battery_state, /joint_states, /amcl_pose, /detections
구독: /cmd_vel, /initialpose

설계 원칙:
- 세계 모델·센서 합성·판정은 ``robo_claw_sim`` 순수 모듈이 담당하고, 이 노드는
  ROS 메시지 변환과 타이머 루프만 맡는다(로직이 여기로 새지 않게 한다).
- 명령이 끊기면 정지한다(robo_claw_core 의 ``command_timeout_sec`` 과 동일 규칙).
"""

from __future__ import annotations

import json
import math
import time

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped, TransformStamped, Twist
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import BatteryState, Imu, JointState, LaserScan
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose

from ..backends import DEFAULT_BACKEND, build_backend
from ..backends.manipulation import ManipulationConfig
from ..motion import MotionArbiter
from ..nav import ControllerConfig
from ..perception import CameraConfig, detect
from ..raycast import ScanConfig, occupancy_values
from ..scenario import Scenario, ScenarioError, build_scenario, load_scenario
from ..viz import world_markers
from ..world import Pose2D, Twist2D

DEFAULT_SCENARIO: dict = {
    "name": "default_room",
    "world": {
        "width": 8.0,
        "height": 6.0,
        "resolution": 0.05,
        "robot": {"x": 1.5, "y": 1.5, "yaw": 0.0},
        "robot_radius": 0.22,
        "obstacles": [
            {"type": "box", "name": "south_wall", "x": 4.0, "y": 0.5, "width": 8.0, "height": 0.2},
            {"type": "box", "name": "block", "x": 4.0, "y": 3.0, "width": 0.6, "height": 1.6},
        ],
    },
    "goals": [],
    "expect": {"min_goals_reached": 0, "max_collisions": 100},
}


class SimulationNode(Node):
    """시나리오 세계 + 센서 합성 노드(백엔드 교체 가능)."""

    def __init__(self, scenario: Scenario | None = None, cli_args: list[str] | None = None) -> None:
        super().__init__("robo_claw_sim_node", cli_args=cli_args)
        self._declare_parameters()
        self.scenario = scenario or self._load_scenario()
        self.world = self.scenario.world
        self.arbiter = MotionArbiter(
            world=self.world,
            config=ControllerConfig(
                max_linear=float(self.get_parameter("controller_max_linear").value),
                max_angular=float(self.get_parameter("controller_max_angular").value),
            ),
            command_timeout_sec=float(self.get_parameter("command_timeout_sec").value),
        )
        self.scan_config = ScanConfig(
            num_beams=int(self.get_parameter("scan_num_beams").value),
            range_max=float(self.get_parameter("scan_range_max").value),
        )
        self.camera_config = CameraConfig(
            width=int(self.get_parameter("camera_width").value),
            height=int(self.get_parameter("camera_height").value),
            hfov=float(self.get_parameter("camera_hfov").value),
        )
        # 물리/센서 백엔드. 기본값은 외부 의존성이 없는 kinematic 이다.
        self.serve_manipulation = bool(self._p("serve_manipulation"))
        manipulation = ManipulationConfig() if self.serve_manipulation else None
        self.backend = build_backend(
            str(self.get_parameter("backend").value),
            world=self.world,
            scan_config=self.scan_config,
            manipulation=manipulation,
            viewer=bool(self._p("viewer")),
        )
        self._frames = {
            "map": self.get_parameter("map_frame").value,
            "odom": self.get_parameter("odom_frame").value,
            "base": self.get_parameter("base_frame").value,
            "scan": self.get_parameter("scan_frame").value,
            "camera": self.get_parameter("camera_frame").value,
        }
        self._joint_names = list(
            json.loads(self.get_parameter("joint_names_json").value or "[]")
        ) or ["left_wheel_joint", "right_wheel_joint"]
        self._wheel_angles = [0.0] * len(self._joint_names)
        self._battery_percent = float(self.get_parameter("battery_start_percent").value)
        self._last_tick = time.monotonic()
        self._camera_tick = 0
        self.last_detected_objects: list[str] = []
        self._setup_io()
        self.navigation = None
        self.manipulation = None
        if self.serve_manipulation:
            from .manipulation_servers import SimManipulation

            self.manipulation = SimManipulation(
                self, self.backend, str(self._p("manipulation_action_name"))
            )
        if bool(self._p("serve_navigation")):
            # 순환 import 를 피하기 위해 노드 준비 후 지역 import 한다.
            from .nav_servers import SimNavigation

            self.navigation = SimNavigation(
                self, self.arbiter, self.world, self.scenario.injections
            )
        rate = float(self.get_parameter("update_rate_hz").value)
        self._timer = self.create_timer(1.0 / max(rate, 1.0), self._tick)
        self.get_logger().info(
            f"robo_claw_sim 시작: scenario={self.scenario.name} backend={self.backend.name} "
            f"world={self.world.width}x{self.world.height}m "
            f"objects={len(self.world.objects)} rate={rate}Hz"
        )

    # ------------------------------------------------------------------ 설정
    def _declare_parameters(self) -> None:
        defaults = {
            "scenario_file": "",
            "backend": DEFAULT_BACKEND,
            "update_rate_hz": 20.0,
            "command_timeout_sec": 0.5,
            "controller_max_linear": 0.45,
            "controller_max_angular": 1.2,
            "scan_num_beams": 360,
            "scan_range_max": 12.0,
            "camera_width": 640,
            "camera_height": 480,
            "camera_hfov": 1.2,
            "map_frame": "map",
            "odom_frame": "odom",
            "base_frame": "base_link",
            "scan_frame": "base_scan",
            "camera_frame": "camera_link",
            "cmd_vel_topic": "/cmd_vel",
            "odom_topic": "/odom",
            "scan_topic": "/scan",
            "imu_topic": "/imu",
            "map_topic": "/map",
            "battery_topic": "/battery_state",
            "joint_states_topic": "/joint_states",
            "amcl_pose_topic": "/amcl_pose",
            "detections_topic": "/object_detector_node/detections",
            "initial_pose_topic": "/initialpose",
            "publish_scan": True,
            "publish_imu": True,
            "publish_map": True,
            "publish_battery": True,
            "publish_joint_states": True,
            "publish_detections": True,
            "viewer": False,
            "sim_viz": False,
            "world_markers_topic": "/sim/world_markers",
            "publish_camera": False,
            "camera_topic": "/camera/color/image_raw",
            "camera_depth_topic": "/camera/depth/image_raw",
            "camera_publish_every_n_ticks": 10,
            "publish_clock": False,
            "battery_start_percent": 100.0,
            "battery_drain_percent_per_sec": 0.0,
            "detect_seed": 0,
            "detect_false_negative_rate": 0.0,
            "joint_names_json": "",
            "serve_navigation": True,
            "serve_manipulation": False,
            "manipulation_action_name": "/sim_arm_controller/follow_joint_trajectory",
            "manipulation_timeout_sec": 30.0,
            "navigate_tolerance": 0.25,
            "navigate_timeout_sec": 300.0,
            "spin_timeout_sec": 60.0,
        }
        for name, value in defaults.items():
            self.declare_parameter(name, value)

    def _load_scenario(self) -> Scenario:
        path = str(self.get_parameter("scenario_file").value or "")
        if not path:
            return build_scenario(DEFAULT_SCENARIO)
        try:
            return load_scenario(path)
        except ScenarioError as exc:
            self.get_logger().error(f"시나리오 파일을 사용할 수 없습니다: {exc}")
            raise

    def _p(self, name: str):
        return self.get_parameter(name).value

    # ---------------------------------------------------------------- ROS IO
    def _setup_io(self) -> None:
        self.tf_broadcaster = TransformBroadcaster(self)
        self.static_tf_broadcaster = StaticTransformBroadcaster(self)
        self._publish_static_transforms()

        self.create_subscription(Twist, str(self._p("cmd_vel_topic")), self._on_cmd_vel, 10)
        self.create_subscription(
            PoseWithCovarianceStamped, str(self._p("initial_pose_topic")), self._on_initial_pose, 10
        )

        self.odom_pub = self.create_publisher(Odometry, str(self._p("odom_topic")), 10)
        self.scan_pub = self.create_publisher(LaserScan, str(self._p("scan_topic")), 10)
        self.imu_pub = self.create_publisher(Imu, str(self._p("imu_topic")), 10)
        self.joint_pub = self.create_publisher(JointState, str(self._p("joint_states_topic")), 10)
        self.battery_pub = self.create_publisher(BatteryState, str(self._p("battery_topic")), 10)
        self.amcl_pub = self.create_publisher(
            PoseWithCovarianceStamped, str(self._p("amcl_pose_topic")), 10
        )
        self.detections_pub = self.create_publisher(
            Detection2DArray, str(self._p("detections_topic")), 10
        )
        # 지도는 늦게 접속한 구독자도 받아야 하므로 TRANSIENT_LOCAL 로 발행한다.
        map_qos = QoSProfile(depth=1)
        map_qos.durability = DurabilityPolicy.TRANSIENT_LOCAL
        map_qos.reliability = ReliabilityPolicy.RELIABLE
        self.map_pub = self.create_publisher(OccupancyGrid, str(self._p("map_topic")), map_qos)
        self._publish_map()
        self.markers_pub = None
        if bool(self._p("sim_viz")):
            from visualization_msgs.msg import MarkerArray

            self.markers_pub = self.create_publisher(
                MarkerArray, str(self._p("world_markers_topic")), 1
            )
        self.camera_pub = None
        self.camera_depth_pub = None
        if bool(self._p("publish_camera")):
            if not hasattr(self.backend, "render_camera"):
                self.get_logger().warning(
                    f"backend={self.backend.name} 는 카메라 렌더를 지원하지 않습니다. "
                    "publish_camera 를 무시합니다(backend:=mujoco 필요)."
                )
            else:
                from sensor_msgs.msg import Image

                self.camera_pub = self.create_publisher(Image, str(self._p("camera_topic")), 5)
                self.camera_depth_pub = self.create_publisher(
                    Image, str(self._p("camera_depth_topic")), 5
                )
        if bool(self._p("publish_clock")):
            from rosgraph_msgs.msg import Clock

            self.clock_pub = self.create_publisher(Clock, "/clock", 10)

    def _on_cmd_vel(self, msg: Twist) -> None:
        self.arbiter.set_external(Twist2D(vx=msg.linear.x, vyaw=msg.angular.z), self._now())

    def _on_initial_pose(self, msg: PoseWithCovarianceStamped) -> None:
        pose = msg.pose.pose
        yaw = _yaw_from_quaternion(
            pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w
        )
        if self.backend.teleport(Pose2D(x=pose.position.x, y=pose.position.y, yaw=yaw)):
            self.get_logger().info(
                f"초기 위치 설정: ({pose.position.x:.2f}, {pose.position.y:.2f}, {yaw:.2f})"
            )
        else:
            self.get_logger().warning("초기 위치 설정 거부: 점유된 위치입니다")

    # ------------------------------------------------------------------ 루프
    def _now(self) -> float:
        return self.world.elapsed_sec

    def _tick(self) -> None:
        current = time.monotonic()
        dt = min(max(current - self._last_tick, 0.0), 0.5)
        self._last_tick = current
        if dt <= 0.0:
            return

        now = self._now()
        command = self.arbiter.command(now)
        self.backend.step(command, dt)
        self._update_battery(dt)
        self.publish_sensors()

    def grasped_objects(self) -> list[str]:
        """시나리오 실행 중 파지에 성공한 물체 이름."""
        if self.manipulation is None:
            return []
        return self.manipulation.grasped_objects()

    def destroy_backend(self) -> None:
        """백엔드 자원 정리. 노드 destroy 전에 호출한다."""
        if getattr(self, "manipulation", None) is not None:
            self.manipulation.destroy()
            self.manipulation = None
        if getattr(self, "backend", None) is not None:
            self.backend.close()

    def destroy_navigation(self) -> None:
        """액션 서버 정리. 노드 destroy 전에 호출한다."""
        if getattr(self, "navigation", None) is not None:
            self.navigation.destroy()
            self.navigation = None

    def _update_battery(self, dt: float) -> None:
        drain = float(self._p("battery_drain_percent_per_sec"))
        if drain > 0.0:
            self._battery_percent = max(0.0, self._battery_percent - drain * dt)

    def injection_active(self, kind: str, now: float | None = None) -> bool:
        """시나리오에 선언된 실패 주입이 현재 활성인지."""
        moment = self._now() if now is None else now
        return any(
            injection.kind == kind and injection.active(moment)
            for injection in self.scenario.injections
        )

    def injection_for_goal(self, kind: str, goal_index: int):
        for injection in self.scenario.injections:
            if injection.kind == kind and injection.goal_index == goal_index:
                return injection
        return None

    def set_battery_percent(self, percent: float) -> None:
        self._battery_percent = max(0.0, min(100.0, percent))

    # -------------------------------------------------------------- 발행부
    def publish_sensors(self) -> None:
        stamp = self.get_clock().now().to_msg()
        self._publish_odom(stamp)
        self._publish_transforms(stamp)
        self._publish_joint_states(stamp)
        self._publish_amcl(stamp)
        if bool(self._p("publish_scan")) and not self.injection_active("scan_drop"):
            self._publish_scan(stamp)
        if bool(self._p("publish_imu")) and not self.injection_active("imu_drop"):
            self._publish_imu(stamp)
        if bool(self._p("publish_battery")):
            self._publish_battery(stamp)
        if bool(self._p("publish_detections")):
            self._publish_detections(stamp)
        if self.markers_pub is not None:
            self._publish_world_markers(stamp)
        if self.camera_pub is not None:
            self._camera_tick += 1
            every = max(1, int(self._p("camera_publish_every_n_ticks")))
            if self._camera_tick % every == 0:
                self._publish_camera(stamp)
        if bool(self._p("publish_clock")):
            self._publish_clock(stamp)

    def _publish_odom(self, stamp) -> None:  # noqa: ANN001
        robot = self.world.robot
        msg = Odometry()
        msg.header.stamp = stamp
        msg.header.frame_id = self._frames["odom"]
        msg.child_frame_id = self._frames["base"]
        msg.pose.pose.position.x = robot.x
        msg.pose.pose.position.y = robot.y
        msg.pose.pose.orientation.z = math.sin(robot.yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(robot.yaw / 2.0)
        msg.twist.twist.linear.x = self.world.last_command.vx * math.cos(0.0)
        msg.twist.twist.angular.z = self.world.last_command.vyaw
        self.odom_pub.publish(msg)

    def _publish_transforms(self, stamp) -> None:  # noqa: ANN001
        robot = self.world.robot
        msg = TransformStamped()
        msg.header.stamp = stamp
        msg.header.frame_id = self._frames["odom"]
        msg.child_frame_id = self._frames["base"]
        msg.transform.translation.x = robot.x
        msg.transform.translation.y = robot.y
        msg.transform.rotation.z = math.sin(robot.yaw / 2.0)
        msg.transform.rotation.w = math.cos(robot.yaw / 2.0)
        self.tf_broadcaster.sendTransform(msg)

    def _publish_static_transforms(self) -> None:
        transforms = []
        # map -> odom: 위치추정(AMCL)을 대체하는 항등 변환.
        transforms.append(_static_transform(self._frames["map"], self._frames["odom"]))
        transforms.append(_static_transform(self._frames["base"], self._frames["scan"], x=0.1))
        transforms.append(
            _static_transform(
                self._frames["base"], self._frames["camera"], x=0.15, z=0.35, pitch=-0.26
            )
        )
        self.static_tf_broadcaster.sendTransform(transforms)

    def _publish_scan(self, stamp) -> None:  # noqa: ANN001
        ranges = self.backend.scan()
        msg = LaserScan()
        msg.header.stamp = stamp
        msg.header.frame_id = self._frames["scan"]
        msg.angle_min = self.scan_config.angle_min
        msg.angle_max = self.scan_config.angle_max
        msg.angle_increment = self.scan_config.angle_increment
        msg.time_increment = 0.0
        msg.scan_time = 1.0 / max(float(self._p("update_rate_hz")), 1.0)
        msg.range_min = self.scan_config.range_min
        msg.range_max = self.scan_config.range_max
        msg.ranges = [float(value) for value in ranges]
        self.scan_pub.publish(msg)

    def _publish_imu(self, stamp) -> None:  # noqa: ANN001
        msg = Imu()
        msg.header.stamp = stamp
        msg.header.frame_id = self._frames["base"]
        msg.orientation.z = math.sin(self.world.robot.yaw / 2.0)
        msg.orientation.w = math.cos(self.world.robot.yaw / 2.0)
        msg.angular_velocity.z = self.world.last_command.vyaw
        msg.linear_acceleration.x = 0.0
        for index in range(9):
            msg.orientation_covariance[index] = 0.0
        msg.orientation_covariance[0] = 0.01
        msg.orientation_covariance[4] = 0.01
        msg.orientation_covariance[8] = 0.01
        self.imu_pub.publish(msg)

    def _publish_joint_states(self, stamp) -> None:  # noqa: ANN001
        if not bool(self._p("publish_joint_states")):
            return
        # 바퀴 관절 각도는 주행 거리로부터 적분한다(차동 구동 근사).
        travel = self.world.last_command.vx * self._timer_period
        wheel_radius = 0.035
        delta = travel / max(wheel_radius, 1e-3)
        self._wheel_angles = [angle + delta for angle in self._wheel_angles]
        msg = JointState()
        msg.header.stamp = stamp
        msg.name = list(self._joint_names)
        msg.position = list(self._wheel_angles)
        msg.velocity = [self.world.last_command.vx / max(wheel_radius, 1e-3)] * len(
            self._joint_names
        )
        msg.effort = [0.0] * len(self._joint_names)
        self.joint_pub.publish(msg)

    @property
    def _timer_period(self) -> float:
        return 1.0 / max(float(self._p("update_rate_hz")), 1.0)

    def _publish_battery(self, stamp) -> None:  # noqa: ANN001
        msg = BatteryState()
        msg.header.stamp = stamp
        msg.percentage = self._battery_percent / 100.0
        msg.voltage = 24.0
        msg.present = True
        msg.power_supply_status = BatteryState.POWER_SUPPLY_STATUS_DISCHARGING
        self.battery_pub.publish(msg)

    def _publish_amcl(self, stamp) -> None:  # noqa: ANN001
        robot = self.world.robot
        msg = PoseWithCovarianceStamped()
        msg.header.stamp = stamp
        msg.header.frame_id = self._frames["map"]
        msg.pose.pose.position.x = robot.x
        msg.pose.pose.position.y = robot.y
        msg.pose.pose.orientation.z = math.sin(robot.yaw / 2.0)
        msg.pose.pose.orientation.w = math.cos(robot.yaw / 2.0)
        msg.pose.covariance[0] = 0.05
        msg.pose.covariance[7] = 0.05
        msg.pose.covariance[35] = 0.02
        self.amcl_pub.publish(msg)

    def _publish_detections(self, stamp) -> None:  # noqa: ANN001
        detections = detect(
            self.world,
            self.camera_config,
            seed=int(self._p("detect_seed")),
            false_negative_rate=float(self._p("detect_false_negative_rate")),
        )
        self.last_detected_objects = [detection.class_id for detection in detections]
        msg = Detection2DArray()
        msg.header.stamp = stamp
        msg.header.frame_id = self._frames["camera"]
        for detection in detections:
            item = Detection2D()
            item.header = msg.header
            item.id = detection.object_name
            item.bbox.center.position.x = detection.center_u
            item.bbox.center.position.y = detection.center_v
            item.bbox.size_x = detection.size_u
            item.bbox.size_y = detection.size_v
            hypothesis = ObjectHypothesisWithPose()
            hypothesis.hypothesis.class_id = detection.class_id
            hypothesis.hypothesis.score = 1.0
            item.results.append(hypothesis)
            msg.detections.append(item)
        self.detections_pub.publish(msg)

    def _publish_map(self) -> None:
        if not bool(self._p("publish_map")):
            return
        msg = OccupancyGrid()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self._frames["map"]
        msg.info.resolution = self.world.resolution
        msg.info.width = self.world.cols
        msg.info.height = self.world.rows
        msg.info.origin.position.x = 0.0
        msg.info.origin.position.y = 0.0
        msg.info.origin.orientation.w = 1.0
        msg.data = occupancy_values(self.world)
        self.map_pub.publish(msg)

    def _publish_world_markers(self, stamp) -> None:  # noqa: ANN001
        """시나리오 세계를 MarkerArray 로 발행한다(RViz 시각화)."""
        from visualization_msgs.msg import Marker, MarkerArray

        kinds = {"box": Marker.CUBE, "cylinder": Marker.CYLINDER, "sphere": Marker.SPHERE}
        msg = MarkerArray()
        for spec in world_markers(self.world):
            marker = Marker()
            marker.header.stamp = stamp
            marker.header.frame_id = self._frames["map"]
            marker.ns = spec["ns"]
            marker.id = spec["id"]
            marker.type = kinds[spec["type"]]
            marker.action = Marker.ADD
            marker.pose.position.x = spec["x"]
            marker.pose.position.y = spec["y"]
            marker.pose.position.z = spec["z"]
            marker.pose.orientation.w = 1.0
            marker.scale.x = spec["sx"]
            marker.scale.y = spec["sy"]
            marker.scale.z = spec["sz"]
            marker.color.r, marker.color.g, marker.color.b, marker.color.a = spec["rgba"]
            msg.markers.append(marker)
        self.markers_pub.publish(msg)

    def _publish_camera(self, stamp) -> None:  # noqa: ANN001
        """MuJoCo 헤드 카메라 RGB-D를 Image 로 발행한다(오프스크린 렌더)."""
        from sensor_msgs.msg import Image

        try:
            rgb, depth = self.backend.render_camera(
                width=self.camera_config.width, height=self.camera_config.height
            )
        except RuntimeError as exc:
            # GL 백엔드가 없으면 매 tick 경고를 반복하지 않고 한 번만 알린다.
            if self.camera_pub is not None:
                self.get_logger().error(f"카메라 렌더 실패: {exc}")
                self.camera_pub = None
                self.camera_depth_pub = None
            return

        rgb_msg = Image()
        rgb_msg.header.stamp = stamp
        rgb_msg.header.frame_id = self._frames["camera"]
        rgb_msg.height, rgb_msg.width = int(rgb.shape[0]), int(rgb.shape[1])
        rgb_msg.encoding = "rgb8"
        rgb_msg.is_bigendian = 0
        rgb_msg.step = int(rgb.shape[1]) * 3
        rgb_msg.data = rgb.tobytes()
        self.camera_pub.publish(rgb_msg)

        if self.camera_depth_pub is not None:
            depth_msg = Image()
            depth_msg.header.stamp = stamp
            depth_msg.header.frame_id = self._frames["camera"]
            depth_msg.height, depth_msg.width = int(depth.shape[0]), int(depth.shape[1])
            depth_msg.encoding = "32FC1"
            depth_msg.is_bigendian = 0
            depth_msg.step = int(depth.shape[1]) * 4
            depth_msg.data = depth.astype("float32").tobytes()
            self.camera_depth_pub.publish(depth_msg)

    def _publish_clock(self, stamp) -> None:  # noqa: ANN001
        from rosgraph_msgs.msg import Clock

        clock = Clock()
        clock.clock = stamp
        self.clock_pub.publish(clock)


def _static_transform(
    parent: str,
    child: str,
    x: float = 0.0,
    y: float = 0.0,
    z: float = 0.0,
    pitch: float = 0.0,
) -> TransformStamped:
    msg = TransformStamped()
    msg.header.frame_id = parent
    msg.child_frame_id = child
    msg.transform.translation.x = x
    msg.transform.translation.y = y
    msg.transform.translation.z = z
    msg.transform.rotation.y = math.sin(pitch / 2.0)
    msg.transform.rotation.w = math.cos(pitch / 2.0)
    return msg


def _yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def main(args: list[str] | None = None) -> None:
    rclpy.init(args=args)
    node = SimulationNode()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_navigation()
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
