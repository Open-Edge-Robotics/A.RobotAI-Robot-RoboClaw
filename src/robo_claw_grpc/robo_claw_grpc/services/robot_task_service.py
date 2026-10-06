"""태스크 실행 서비스 — robo_claw_agent의 ExecuteTask 액션을 gRPC로 노출

채널 노드(Telegram/Slack/HTTP/peer)가 메시지를 수신해 실행하는 경로와
동일하게 자연어 명령을 LLM 에이전트로 전달합니다. robo_mcp 등 외부 클라이언트가
``send_message`` 스킬(로컬 메신저 송신 전용)을 거치지 않고 에이전트 추론을
거치도록 합니다.
"""

import logging
from typing import Any

from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.robo_pb2 import (
    ExecuteTaskRequest,
    ExecuteTaskResponse,
)
from robo_claw_grpc.utils import LogHelper, handle_grpc_errors, log_to_history

logger = logging.getLogger(__name__)


class RobotTaskService:
    """robo_claw_agent의 ExecuteTask ROS 액션을 gRPC로 노출하는 서비스."""

    def __init__(self, config: ConfigModel, grpc_node: Any | None = None) -> None:
        self._logger = LogHelper(
            level="DEBUG" if config.debug else "INFO"
        ).get_logger(__name__)
        self._config = config
        self._grpc_node = grpc_node

    @handle_grpc_errors
    @log_to_history(include_request=True, include_response=True)
    async def ExecuteTask(
        self, request: ExecuteTaskRequest, context: ServicerContext
    ) -> ExecuteTaskResponse:
        """robo_claw_agent의 execute_task 액션을 통해 자연어 명령을 실행합니다."""
        instruction = request.instruction.strip()
        context_json = request.context_json or ""
        timeout_sec = request.timeout_sec if request.timeout_sec > 0 else 300.0

        self._logger.info(
            f"ExecuteTask request: instruction='{instruction}', "
            f"context='{context_json}', timeout={timeout_sec}s"
        )

        if not instruction:
            return ExecuteTaskResponse(
                success=False,
                result_message="명령(instruction)이 비어있습니다.",
            )

        if self._grpc_node is None:
            self._logger.error("gRPC ROS node has not been injected.")
            return ExecuteTaskResponse(
                success=False,
                result_message="태스크 실행 서비스를 사용할 수 없습니다. (gRPC 노드 미주입)",
            )

        result = await self._grpc_node.call_execute_task(
            instruction, context_json=context_json, timeout_sec=timeout_sec
        )

        if result is None:
            return ExecuteTaskResponse(
                success=False,
                result_message="태스크 실행 응답 없음 (액션 서버 미준비 또는 타임아웃)",
            )

        # ExecuteTask.Result에서 결과 추출
        ros_result = result.result if hasattr(result, "result") else result
        success = bool(getattr(ros_result, "success", False))
        result_message = getattr(ros_result, "result_message", "") or (
            "완료" if success else "실패"
        )

        return ExecuteTaskResponse(
            success=success,
            result_message=result_message,
        )
