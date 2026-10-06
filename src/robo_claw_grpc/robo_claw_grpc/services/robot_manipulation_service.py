from typing import Any

from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.repositories import RobotHistoryRepository
from robo_claw_grpc.robo_pb2 import ActionResult, JointControlRequest
from robo_claw_grpc.utils import (
    LogHelper,
    handle_grpc_errors,
    log_to_history,
    skill_result_to_action_result,
)


class RobotManipulationService:
    def __init__(self, repo: RobotHistoryRepository, config: ConfigModel, node: Any | None = None, grpc_node: Any | None = None):
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._repo = repo
        self._robot_id = config.robot_id
        self._manipulation_node = node
        self._grpc_node = grpc_node

    @handle_grpc_errors
    @log_to_history(include_request=True, include_response=True)
    async def ControlJoints(self, request: JointControlRequest, context: ServicerContext) -> ActionResult:
        self._logger.info(f"ControlJoints: {list(request.joints.keys())}")

        if not request.joints:
            return ActionResult(success=False, message="제어할 관절 없음")

        if self._grpc_node is None:
            self._logger.error("gRPC ROS node has not been injected.")
            return ActionResult(
                success=False, message="매니퓰레이션 서비스를 사용할 수 없습니다."
            )

        joints_map = {name: float(pos) for name, pos in request.joints.items()}
        params = {
            "joints": joints_map
        }

        try:
            self._logger.info("Calling robo_claw_agent's move_joints skill...")
            result = await self._grpc_node.call_execute_skill("move_joints", params, timeout_sec=60.0)
            return skill_result_to_action_result(
                result, success_message="관절 제어 성공", fail_prefix="관절 제어 실패"
            )
        except Exception as e:
            self._logger.exception(f"Error calling move_joints skill: {e}")
            return ActionResult(success=False, message=str(e))
