from grpc.aio import ServicerContext

from robo_claw_grpc.config.robo_claw_grpc_database import RoboClawGrpcDatabase
from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.repositories import RobotHistoryRepository
from robo_claw_grpc.robo_pb2 import (
    ActionResult,
    CommandRequest,
    CommandResponse,
    EmptyRequest,
    ExecuteSkillRequest,
    ExecuteSkillResponse,
    ExecuteTaskRequest,
    ExecuteTaskResponse,
    JointControlRequest,
    OccupancyGrid,
    PingRequest,
    Pong,
    Robot,
    RobotHistoryList,
    RobotStatus,
)
from robo_claw_grpc.robo_pb2_grpc import RosGrpcServicer
from robo_claw_grpc.services import (
    FileTransferService,
    PingService,
    RobotCameraService,
    RobotCommandService,
    RobotHistoryService,
    RobotInfoService,
    RobotManipulationService,
    RobotNavigationService,
    RobotSkillService,
    RobotStatusService,
    RobotTaskService,
)
from robo_claw_grpc.utils import LogHelper


class RoboClawGrpcServicer(RosGrpcServicer):
    """robo_claw_grpc gRPC Servicer"""

    def __init__(self, config: ConfigModel, manipulation_node=None, camera_node=None, nav_node=None, battery_node=None, grpc_node=None):
        super().__init__()
        self._logger = LogHelper().get_logger(__name__)
        self._db = RoboClawGrpcDatabase(config=config)
        self._repo = RobotHistoryRepository(db=self._db, config=config)

        self._ping_svc = PingService(config=config)
        self._robot_info_svc = RobotInfoService(config=config)
        self._robot_history_svc = RobotHistoryService(repo=self._repo, config=config, battery_node=battery_node)
        self._robot_camera_svc = RobotCameraService(repo=self._repo, config=config, camera_node=camera_node, nav_node=nav_node)
        self._robot_navigation_svc = RobotNavigationService(repo=self._repo, config=config, nav_node=nav_node, grpc_node=grpc_node)
        self._robot_manipulation_svc = RobotManipulationService(repo=self._repo, config=config, node=manipulation_node, grpc_node=grpc_node)
        self._robot_status_svc = RobotStatusService(config=config, battery_node=battery_node, camera_node=camera_node, nav_node=nav_node)
        self._robot_command_svc = RobotCommandService(config=config)
        self._file_transfer_svc = FileTransferService(config=config)
        self._robot_skill_svc = RobotSkillService(config=config, grpc_node=grpc_node)
        self._robot_task_svc = RobotTaskService(config=config, grpc_node=grpc_node)

    async def initialize(self):
        await self._db.init_database()

    async def Ping(self, request: PingRequest, context: ServicerContext) -> Pong:
        self._logger.info("Ping: %s", request.message)
        return Pong(message="Pong")

    async def GetRobotInfo(self, request: EmptyRequest, context: ServicerContext) -> Robot:
        return await self._robot_info_svc.GetRobotInfo(request, context)

    async def GetRobotCameraImage(self, request, context):
        return await self._robot_camera_svc.GetRobotCameraImage(request, context)

    async def GetRobotCameraImageStream(self, request, context):
        async for image in self._robot_camera_svc.GetRobotCameraImageStream(request, context):
            yield image

    async def GetCurrentRobotPose(self, request, context):
        return await self._robot_navigation_svc.GetCurrentRobotPose(request, context)

    async def GetCurrentRobotBattery(self, request: EmptyRequest, context: ServicerContext):
        return await self._robot_history_svc.GetCurrentRobotBattery(request, context)

    async def GetLatestRobotHistory(self, request: EmptyRequest, context: ServicerContext):
        return await self._robot_history_svc.GetLatestRobotHistory(request, context)

    async def GetRobotHistoryList(self, request: EmptyRequest, context: ServicerContext) -> RobotHistoryList:
        return await self._robot_history_svc.GetRobotHistoryList(request, context)

    async def NavigateToPose(self, request, context):
        return await self._robot_navigation_svc.NavigateToPose(request, context)

    async def EmergencyStop(self, request, context):
        return await self._robot_navigation_svc.EmergencyStop(request, context)

    async def FollowWaypoints(self, request, context):
        return await self._robot_navigation_svc.FollowWaypoints(request, context)

    async def SetInitialPose(self, request, context):
        return await self._robot_navigation_svc.SetInitialPose(request, context)

    async def GetRobotMap(self, request: EmptyRequest, context: ServicerContext) -> OccupancyGrid:
        return await self._robot_navigation_svc.GetRobotMap(request, context)

    async def ControlJoints(self, request: JointControlRequest, context: ServicerContext) -> ActionResult:
        return await self._robot_manipulation_svc.ControlJoints(request, context)

    async def GetRobotStatus(self, request: EmptyRequest, context: ServicerContext) -> RobotStatus:
        return await self._robot_status_svc.GetRobotStatus(request, context)

    async def GetRobotStatusStream(self, request: EmptyRequest, context: ServicerContext):
        async for status in self._robot_status_svc.GetRobotStatusStream(request, context):
            yield status

    async def ExecuteROSCommand(self, request: CommandRequest, context: ServicerContext) -> CommandResponse:
        return await self._robot_command_svc.ExecuteROSCommand(request, context)

    async def ExecuteROSCommandStream(self, request: CommandRequest, context: ServicerContext):
        async for response in self._robot_command_svc.ExecuteROSCommandStream(request, context):
            yield response

    async def UploadFile(self, request_iterator, context: ServicerContext):
        return await self._file_transfer_svc.UploadFile(request_iterator, context)

    async def DownloadFile(self, request, context: ServicerContext):
        async for chunk in self._file_transfer_svc.DownloadFile(request, context):
            yield chunk

    async def ExecuteSkill(self, request: ExecuteSkillRequest, context: ServicerContext) -> ExecuteSkillResponse:
        return await self._robot_skill_svc.ExecuteSkill(request, context)

    async def ExecuteTask(self, request: ExecuteTaskRequest, context: ServicerContext) -> ExecuteTaskResponse:
        return await self._robot_task_svc.ExecuteTask(request, context)
