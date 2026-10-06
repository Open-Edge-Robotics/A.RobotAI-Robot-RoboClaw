import asyncio
import time
from collections.abc import AsyncGenerator
from typing import Any

from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.robo_pb2 import (
    EmptyRequest,
    Point,
    Quaternion,
    RobotPose,
    RobotStatus,
)
from robo_claw_grpc.utils import (
    LogHelper,
    get_system_stats,
    handle_grpc_errors,
    ros_pose_to_proto,
)


class RobotStatusService:
    def __init__(
        self,
        config: ConfigModel,
        battery_node: Any | None = None,
        camera_node: Any | None = None,
        nav_node: Any | None = None,
    ):
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._robot_id = config.robot_id
        self._battery_node = battery_node
        self._camera_node = camera_node
        self._nav_node = nav_node

    def _collect_robot_status(self) -> RobotStatus:
        stats = get_system_stats()

        battery_percentage = 0.0
        if self._battery_node:
            try:
                battery_percentage = self._battery_node.get_battery_status()
            except Exception as e:
                self._logger.warning(f"Battery query failed: {e}")

        current_pose = RobotPose(
            position=Point(x=0.0, y=0.0, z=0.0),
            orientation=Quaternion(x=0.0, y=0.0, z=0.0, w=1.0),
        )
        if self._nav_node:
            try:
                pose_msg = self._nav_node.get_current_pose()
                if pose_msg:
                    current_pose = ros_pose_to_proto(pose_msg.pose.pose, self._robot_id)
            except Exception as e:
                self._logger.warning(f"Position query failed: {e}")

        image_snapshot = ""
        if self._camera_node:
            try:
                latest_image, _, _ = self._camera_node.get_latest_image()
                if latest_image:
                    image_snapshot = latest_image
            except Exception as e:
                self._logger.warning(f"Camera snapshot query failed: {e}")

        current_task = "IDLE"
        if self._nav_node and hasattr(self._nav_node, "is_navigating"):
            if self._nav_node.is_navigating():
                current_task = "NAVIGATING"

        return RobotStatus(
            robot_id=self._robot_id,
            battery_percentage=battery_percentage,
            cpu_usage=stats["cpu_usage"],
            ram_usage=stats["ram_usage"],
            disk_usage=stats["disk_usage"],
            network_usage=0.0,
            uptime=int(stats["uptime"]),
            current_pose=current_pose,
            current_task=current_task,
            image_snapshot=image_snapshot,
            timestamp=int(time.time()),
        )

    @handle_grpc_errors
    async def GetRobotStatus(self, request: EmptyRequest, context: ServicerContext) -> RobotStatus:
        self._logger.info("GetRobotStatus request")
        return self._collect_robot_status()

    async def GetRobotStatusStream(
        self, request: EmptyRequest, context: ServicerContext
    ) -> AsyncGenerator[RobotStatus, None]:
        self._logger.info("GetRobotStatusStream request")
        try:
            while not context.done():
                yield self._collect_robot_status()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            self._logger.info("Status streaming cancelled")
        except Exception as e:
            self._logger.exception(f"Status streaming error: {e}")
        finally:
            self._logger.info("Status streaming ended")
