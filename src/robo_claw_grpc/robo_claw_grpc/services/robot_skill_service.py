"""범용 스킬 실행 서비스 — robo_claw_agent의 모든 스킬을 gRPC로 호출"""

import json
import logging
from typing import Any

from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.robo_pb2 import (
    ExecuteSkillRequest,
    ExecuteSkillResponse,
)
from robo_claw_grpc.utils import LogHelper, handle_grpc_errors, log_to_history

logger = logging.getLogger(__name__)


class RobotSkillService:
    """robo_claw_agent의 ExecuteSkill ROS 서비스를 gRPC로 노출하는 서비스."""

    def __init__(self, config: ConfigModel, grpc_node: Any | None = None) -> None:
        self._logger = LogHelper(
            level="DEBUG" if config.debug else "INFO"
        ).get_logger(__name__)
        self._config = config
        self._grpc_node = grpc_node

    @handle_grpc_errors
    @log_to_history(include_request=True, include_response=True)
    async def ExecuteSkill(
        self, request: ExecuteSkillRequest, context: ServicerContext
    ) -> ExecuteSkillResponse:
        """robo_claw_agent의 execute_skill 서비스를 통해 모든 스킬을 실행합니다."""
        skill_name = request.skill_name.strip()
        params_json = request.params_json or "{}"
        timeout_sec = request.timeout_sec if request.timeout_sec > 0 else 300.0

        self._logger.info(
            f"ExecuteSkill request: skill='{skill_name}', params={params_json}, "
            f"timeout={timeout_sec}s"
        )

        if not skill_name:
            return ExecuteSkillResponse(
                success=False,
                message="스킬 이름이 비어있습니다.",
                skill_name="",
            )

        # JSON 파라미터 검증
        try:
            params = json.loads(params_json) if params_json else {}
        except json.JSONDecodeError as e:
            return ExecuteSkillResponse(
                success=False,
                message=f"params_json 파싱 실패: {e}",
                skill_name=skill_name,
            )

        if not isinstance(params, dict):
            return ExecuteSkillResponse(
                success=False,
                message="params_json은 JSON 객체여야 합니다.",
                skill_name=skill_name,
            )

        # grpc_node가 주입되지 않은 경우
        if self._grpc_node is None:
            self._logger.error("gRPC ROS node has not been injected.")
            return ExecuteSkillResponse(
                success=False,
                message="스킬 실행 서비스를 사용할 수 없습니다. (gRPC 노드 미주입)",
                skill_name=skill_name,
            )

        # robo_claw_agent의 execute_skill ROS 서비스 호출
        result = await self._grpc_node.call_execute_skill(
            skill_name, params, timeout_sec=timeout_sec
        )

        if result is None:
            return ExecuteSkillResponse(
                success=False,
                message="스킬 실행 서비스 응답 없음 (서비스 미준비 또는 타임아웃)",
                skill_name=skill_name,
            )

        # ExecuteSkill.Response의 result 필드 (SkillResult msg)에서 결과 추출
        skill_result = result.result
        success = skill_result.code == 0  # SUCCESS=0

        # result_json 필드에 추가 데이터가 있으면 전달
        result_json = ""
        if hasattr(skill_result, "result_json") and skill_result.result_json:
            result_json = skill_result.result_json

        return ExecuteSkillResponse(
            success=success,
            message=skill_result.message or ("완료" if success else "실패"),
            skill_name=skill_result.skill_name or skill_name,
            result_json=result_json,
        )
