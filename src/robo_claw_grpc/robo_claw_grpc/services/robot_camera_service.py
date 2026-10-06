import asyncio
from collections.abc import AsyncGenerator

from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.nodes import RoboClawGrpcCameraNode, RoboClawGrpcNavNode
from robo_claw_grpc.repositories import RobotHistoryRepository
from robo_claw_grpc.robo_pb2 import (
    CameraRequest,
    RobotCameraImage,
)
from robo_claw_grpc.utils import (
    LogHelper,
    generate_index,
    handle_grpc_errors,
    log_to_history,
    ros_pose_to_proto,
)


class RobotCameraService:
    def __init__(
        self,
        repo: RobotHistoryRepository,
        config: ConfigModel,
        camera_node: RoboClawGrpcCameraNode | None = None,
        nav_node: RoboClawGrpcNavNode | None = None,
    ):
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._repo = repo
        self._robot_id = config.robot_id
        self._camera_node = camera_node
        self._nav_node = nav_node

    @handle_grpc_errors
    @log_to_history(include_request=True, include_response=False, capture_image=True)
    async def GetRobotCameraImage(self, request: CameraRequest, context: ServicerContext) -> RobotCameraImage:
        self._logger.info("GetRobotCameraImage request")

        if self._camera_node is None:
            return RobotCameraImage()

        # 클라이언트가 타입을 명시하지 않은 경우(기본값 0) 기본으로 COMPRESSED(1)를 요청하여 가져옴
        requested_type = request.image_type if request.image_type != 0 else 1
        image_data, image_format, _ = self._camera_node.get_latest_image(requested_type)
        ros_pose = self._nav_node.get_current_pose() if self._nav_node else None
        robot_pose = None

        if ros_pose:
            robot_pose = ros_pose_to_proto(ros_pose.pose.pose, self._robot_id)

        if image_data:
            return RobotCameraImage(
                id=generate_index(),
                robot_id=self._robot_id,
                image=image_data,
                format=image_format or "UNKNOWN",
                pose=robot_pose,
            )
        return RobotCameraImage()

    async def GetRobotCameraImageStream(
        self, request: CameraRequest, context: ServicerContext
    ) -> AsyncGenerator[RobotCameraImage, None]:
        self._logger.info("GetRobotCameraImageStream request")

        if self._camera_node is None:
            return

        last_timestamp = 0.0
        try:
            while not context.done():
                # 클라이언트가 타입을 명시하지 않은 경우(기본값 0) 기본으로 COMPRESSED(1)를 요청하여 가져옴
                requested_type = request.image_type if request.image_type != 0 else 1
                image_data, image_format, timestamp = self._camera_node.get_latest_image(requested_type)

                if image_data and timestamp > last_timestamp:
                    last_timestamp = timestamp
                    ros_pose = self._nav_node.get_current_pose() if self._nav_node else None
                    robot_pose = None

                    if ros_pose:
                        robot_pose = ros_pose_to_proto(ros_pose.pose.pose, self._robot_id)

                    yield RobotCameraImage(
                        id=generate_index(),
                        robot_id=self._robot_id,
                        image=image_data,
                        format=image_format or "UNKNOWN",
                        pose=robot_pose,
                        timestamp=int(timestamp * 1000),
                    )

                await asyncio.sleep(0.01)

        except asyncio.CancelledError:
            self._logger.info("Camera streaming cancelled")
        except Exception as e:
            self._logger.exception(f"Camera streaming error: {e}")
        finally:
            self._logger.info("Camera streaming ended")
