from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.robo_pb2 import EmptyRequest, Robot, RobotType
from robo_claw_grpc.utils import LogHelper, handle_grpc_errors


class RobotInfoService:
    def __init__(self, config: ConfigModel) -> None:
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._config = config

    @handle_grpc_errors
    async def GetRobotInfo(self, request: EmptyRequest, context: ServicerContext) -> Robot:
        self._logger.info("GetRobotInfo request")
        return Robot(
            id=self._config.robot_id,
            name=self._config.robot_name,
            description=self._config.robot_description,
            type=getattr(RobotType, self._config.robot_type),
        )
