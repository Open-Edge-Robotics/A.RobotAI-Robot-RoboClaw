from typing import Any

from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.models.robot_history import RobotHistory as RobotHistoryModel
from robo_claw_grpc.repositories import RobotHistoryRepository
from robo_claw_grpc.robo_pb2 import (
    EmptyRequest,
    RobotBattery,
    RobotHistoryList,
)
from robo_claw_grpc.robo_pb2 import (
    RobotHistory as RobotHistoryProto,
)
from robo_claw_grpc.utils import LogHelper, handle_grpc_errors


class RobotHistoryService:
    def __init__(self, repo: RobotHistoryRepository, config: ConfigModel, battery_node: Any | None = None):
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._repo = repo
        self._robot_id = config.robot_id
        self._battery_node = battery_node

    def _convert_to_proto(self, model: RobotHistoryModel) -> RobotHistoryProto:
        return RobotHistoryProto(
            id=model.id or 0,
            robot_id=model.robot_id,
            is_connected=model.is_connected,
            battery_percentage=model.battery_percentage,
            cpu_usage=model.cpu_usage,
            ram_usage=model.ram_usage,
            disk_usage=model.disk_usage,
            network_usage=model.network_usage,
            uptime=model.uptime,
            last_seen=model.last_seen,
            request_type=model.request_type or "",
            request_payload=model.request_payload or "",
            response_payload=model.response_payload or "",
            image_snapshot=model.image_snapshot or "",
        )

    @handle_grpc_errors
    async def GetRobotHistoryList(self, request: EmptyRequest, context: ServicerContext) -> RobotHistoryList:
        self._logger.info("GetRobotHistoryList request")
        history_models = await self._repo.get_history_list()
        return RobotHistoryList(history=[self._convert_to_proto(h) for h in history_models])

    @handle_grpc_errors
    async def GetCurrentRobotBattery(self, request: EmptyRequest, context: ServicerContext) -> RobotBattery:
        self._logger.info("GetCurrentRobotBattery request")
        if self._battery_node is None:
            return RobotBattery(percentage=0.0)
        return RobotBattery(percentage=self._battery_node.get_battery_status())

    @handle_grpc_errors
    async def GetLatestRobotHistory(self, request: EmptyRequest, context: ServicerContext) -> RobotHistoryProto:
        self._logger.info("GetLatestRobotHistory request")
        status = await self._repo.get_latest_history(self._robot_id)

        if status is None:
            return RobotHistoryProto()

        if self._battery_node:
            status.battery_percentage = self._battery_node.get_battery_status()

        return self._convert_to_proto(status)
