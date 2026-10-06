import array
from typing import Any

from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.repositories import RobotHistoryRepository
from robo_claw_grpc.robo_pb2 import (
    ActionResult,
    EmptyRequest,
    MapMetaData,
    NavigationGoal,
    OccupancyGrid,
    RobotPose,
    WaypointList,
)
from robo_claw_grpc.utils import (
    LogHelper,
    handle_grpc_errors,
    log_to_history,
    ros_pose_to_proto,
    skill_result_to_action_result,
)


class RobotNavigationService:
    def __init__(self, repo: RobotHistoryRepository, config: ConfigModel, nav_node: Any | None = None, grpc_node: Any | None = None):
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._repo = repo
        self._robot_id = config.robot_id
        self._nav_node = nav_node
        self._grpc_node = grpc_node

    @handle_grpc_errors
    async def GetCurrentRobotPose(self, request: EmptyRequest, context: ServicerContext) -> RobotPose:
        self._logger.info("GetCurrentRobotPose request")
        return await self._get_pose_from_node()

    @handle_grpc_errors
    async def GetRobotMap(self, request: EmptyRequest, context: ServicerContext) -> OccupancyGrid:
        self._logger.info("GetRobotMap request")
        return await self._get_map_from_node()

    @handle_grpc_errors
    @log_to_history(include_request=True, include_response=True)
    async def NavigateToPose(self, request: NavigationGoal, context: ServicerContext) -> ActionResult:
        self._logger.info(f"NavigateToPose request (x:{request.target_pose.position.x}, y:{request.target_pose.position.y})")

        if self._grpc_node is None:
            self._logger.error("gRPC ROS node has not been injected.")
            return ActionResult(success=False, message="내비게이션 서비스를 사용할 수 없습니다.")

        params = {
            "x": float(request.target_pose.position.x),
            "y": float(request.target_pose.position.y),
            "frame_id": "map"
        }

        # robo_claw_agent의 navigate_to 스킬 비동기 호출
        result = await self._grpc_node.call_execute_skill("navigate_to", params, timeout_sec=300.0)
        return skill_result_to_action_result(
            result, success_message="내비게이션 성공", fail_prefix="내비게이션 실패"
        )

    @handle_grpc_errors
    @log_to_history(include_request=True, include_response=True)
    async def FollowWaypoints(self, request: WaypointList, context: ServicerContext) -> ActionResult:
        self._logger.info(f"FollowWaypoints request ({len(request.waypoints)} waypoints)")

        if self._grpc_node is None:
            self._logger.error("gRPC ROS node has not been injected.")
            return ActionResult(success=False, message="내비게이션 서비스를 사용할 수 없습니다.")

        waypoints_list = []
        for wp in request.waypoints:
            waypoints_list.append({
                "x": float(wp.position.x),
                "y": float(wp.position.y)
            })

        params = {
            "waypoints": waypoints_list,
            "frame_id": "map"
        }

        # robo_claw_agent의 follow_waypoints 스킬 비동기 호출
        result = await self._grpc_node.call_execute_skill("follow_waypoints", params, timeout_sec=600.0)
        return skill_result_to_action_result(
            result, success_message="웨이포인트 이동 성공", fail_prefix="웨이포인트 이동 실패"
        )

    @handle_grpc_errors
    @log_to_history(include_request=False, include_response=True)
    async def EmergencyStop(self, request: EmptyRequest, context: ServicerContext) -> ActionResult:
        if self._grpc_node is None:
            self._logger.error("gRPC ROS node has not been injected.")
            return ActionResult(success=False, message="내비게이션 서비스를 사용할 수 없습니다.")

        # robo_claw_agent의 stop 스킬 비동기 호출
        result = await self._grpc_node.call_execute_skill("stop", {}, timeout_sec=10.0)
        return skill_result_to_action_result(
            result, success_message="비상 정지 완료", fail_prefix="비상 정지 실패"
        )

    @handle_grpc_errors
    @log_to_history(include_request=True, include_response=True)
    async def SetInitialPose(self, request: RobotPose, context: ServicerContext) -> ActionResult:
        self._logger.info(
            f"SetInitialPose request (x:{request.position.x}, y:{request.position.y}, "
            f"qx:{request.orientation.x}, qy:{request.orientation.y}, "
            f"qz:{request.orientation.z}, qw:{request.orientation.w})"
        )

        if self._grpc_node is None:
            self._logger.error("gRPC ROS node has not been injected.")
            return ActionResult(success=False, message="내비게이션 서비스를 사용할 수 없습니다.")

        import math

        x = float(request.position.x)
        y = float(request.position.y)

        # 쿼터니언 → yaw 변환
        qx = float(request.orientation.x)
        qy = float(request.orientation.y)
        qz = float(request.orientation.z)
        qw = float(request.orientation.w)
        yaw = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))

        # robo_claw_agent의 set_initial_pose 스킬이 있으면 우선 사용
        if self._grpc_node.exec_skill_client.service_is_ready():
            params = {"x": x, "y": y, "yaw": yaw}
            result = await self._grpc_node.call_execute_skill(
                "set_initial_pose", params, timeout_sec=30.0
            )
            return skill_result_to_action_result(
                result,
                success_message="초기 위치 설정 성공",
                fail_prefix="초기 위치 설정 실패",
            )

        # 폴백: /initialpose 토픽에 직접 발행 (AMCL 초기 추정치 설정)
        if self._nav_node is not None:
            try:
                from geometry_msgs.msg import PoseWithCovarianceStamped

                init_pose = PoseWithCovarianceStamped()
                init_pose.header.frame_id = "map"
                init_pose.header.stamp = self._nav_node.get_clock().now().to_msg()
                init_pose.pose.pose.position.x = x
                init_pose.pose.pose.position.y = y
                init_pose.pose.pose.orientation.z = math.sin(yaw / 2.0)
                init_pose.pose.pose.orientation.w = math.cos(yaw / 2.0)

                self._nav_node.initial_pose_publisher.publish(init_pose)
                self._logger.info(f"Initial pose published to /initialpose: ({x}, {y}, yaw={yaw:.2f})")
                return ActionResult(success=True, message=f"초기 위치 설정 성공: ({x}, {y}, yaw={yaw:.2f})")
            except Exception as e:
                self._logger.error(f"Failed to publish initial pose: {e}")
                return ActionResult(success=False, message=f"초기 위치 설정 실패: {e}")

        return ActionResult(success=False, message="내비게이션 노드를 사용할 수 없습니다.")

    async def _get_pose_from_node(self) -> RobotPose:
        if self._nav_node is None:
            return RobotPose(robot_id=self._robot_id)

        pose = self._nav_node.get_current_pose()
        if pose:
            return ros_pose_to_proto(pose.pose.pose, self._robot_id)
        return RobotPose(robot_id=self._robot_id)

    async def _get_map_from_node(self) -> OccupancyGrid:
        if self._nav_node is None:
            return OccupancyGrid()

        ros_map = self._nav_node.get_current_map()
        if ros_map:
            meta = MapMetaData(
                resolution=ros_map.info.resolution,
                width=ros_map.info.width,
                height=ros_map.info.height,
                origin=ros_pose_to_proto(ros_map.info.origin, self._robot_id),
            )

            if isinstance(ros_map.data, array.array):
                data_array = ros_map.data
            else:
                data_array = array.array("b", ros_map.data)

            map_bytes = memoryview(data_array).cast("B").tobytes()
            return OccupancyGrid(info=meta, data=map_bytes)

        return OccupancyGrid()
