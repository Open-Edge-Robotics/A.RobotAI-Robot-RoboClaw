from __future__ import annotations

import logging
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)
from robo_claw_agent.skill_manager import BaseSkill

from .core import _get_runtime, _move_sequence_result

logger = logging.getLogger(__name__)


class OpenGripperSkill(BaseSkill):
    """gripper open preset 실행"""

    name = "open_gripper"
    side_effects = ("gripper_motion",)
    input_schema = {"type": "object", "properties": {"preset_name": {"type": "string", "default": "open"}}, "additionalProperties": True}
    description = (
        "그리퍼를 open preset 으로 이동합니다. "
        "기본 preset 이름은 'open' 이며 preset 값은 설정에서 제공합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        preset_name = str(params.get("preset_name") or "open").strip()
        group_name = str(params.get("group_name") or "").strip() or None

        try:
            runtime = _get_runtime(self.node)
            result = runtime.execute_gripper_preset(
                preset_name,
                group_name=group_name,
            )
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
        ) as exc:
            logger.warning(f"Gripper open failed ({preset_name}): {exc}")
            return {"success": False, "message": str(exc)}

        return _move_sequence_result(
            action=f"그리퍼 open 완료: {preset_name}",
            payload=result,
        )


class CloseGripperSkill(BaseSkill):
    """gripper close preset 실행"""

    name = "close_gripper"
    side_effects = ("gripper_motion",)
    input_schema = {"type": "object", "properties": {"preset_name": {"type": "string", "default": "close"}}, "additionalProperties": True}
    description = (
        "그리퍼를 close preset 으로 이동합니다. "
        "기본 preset 이름은 'close' 이며 preset 값은 설정에서 제공합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        preset_name = str(params.get("preset_name") or "close").strip()
        group_name = str(params.get("group_name") or "").strip() or None

        try:
            runtime = _get_runtime(self.node)
            result = runtime.execute_gripper_preset(
                preset_name,
                group_name=group_name,
            )
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
        ) as exc:
            logger.warning(f"Gripper close failed ({preset_name}): {exc}")
            return {"success": False, "message": str(exc)}

        return _move_sequence_result(
            action=f"그리퍼 close 완료: {preset_name}",
            payload=result,
        )
