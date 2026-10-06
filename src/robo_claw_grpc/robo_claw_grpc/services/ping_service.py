from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.robo_pb2 import PingRequest, Pong
from robo_claw_grpc.utils import LogHelper, handle_grpc_errors


class PingService:
    def __init__(self, config: ConfigModel) -> None:
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)

    @handle_grpc_errors
    async def Ping(self, request: PingRequest, context: ServicerContext) -> Pong:
        self._logger.info(f"Ping: {request.message}")
        return Pong(message="Pong")
