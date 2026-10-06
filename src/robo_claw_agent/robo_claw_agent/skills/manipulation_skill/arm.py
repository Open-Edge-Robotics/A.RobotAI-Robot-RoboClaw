from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
    pose_goal_from_params,
)
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.limits import check_joint_targets

from .core import _get_runtime, _move_sequence_result, _transform_pose_goal

logger = logging.getLogger(__name__)


class ArmPoseSkill(BaseSkill):
    """팔 named pose 이동 스킬"""

    name = "arm_pose"
    side_effects = ("arm_motion",)
    input_schema = {"type": "object", "properties": {"pose_name": {"type": "string"}}, "required": ["pose_name"], "additionalProperties": True}
    description = (
        "팔을 named pose로 이동합니다. "
        "pose_name 파라미터가 필요하며, 실제 MoveIt target 이름은 설정으로 매핑됩니다."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        pose_name = str(params.get("pose_name") or "").strip()
        return bool(pose_name)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        pose_name = str(params.get("pose_name") or "").strip()
        group_name = str(params.get("group_name") or "").strip() or None

        logger.info("Arm pose move: pose=%s group=%s", pose_name, group_name or "default")

        try:
            runtime = _get_runtime(self.node)
            result = runtime.move_to_named_pose(pose_name, group_name=group_name)
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
        ) as exc:
            logger.warning(f"Arm pose move failed ({pose_name}): {exc}")
            return {"success": False, "message": str(exc)}

        return _move_sequence_result(
            action=f"포즈 이동 완료: {pose_name}",
            payload=result,
        )


class MoveJointsSkill(BaseSkill):
    """조인트 직접 이동 스킬"""

    name = "move_joints"
    side_effects = ("arm_motion",)
    input_schema = {"type": "object", "properties": {"joints": {"type": "object"}}, "required": ["joints"], "additionalProperties": True}
    description = (
        "지정한 조인트 값으로 팔 또는 그리퍼를 이동합니다. "
        "joints 파라미터에 {'joint_name': 값} 형태의 맵이 필요합니다."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        joints = params.get("joints")
        return isinstance(joints, Mapping) and bool(joints)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        joints = params.get("joints", {})
        group_name = str(params.get("group_name") or "").strip() or None

        logger.info(
            "Joint move: group=%s joints=%s",
            group_name or "default",
            list(joints.keys()) if isinstance(joints, Mapping) else joints,
        )

        # ROBOT_LIMITS 하드 검증: 조인트 허용 범위
        robot_limits = getattr(self.node, "_robot_limits_dict", None)
        if isinstance(joints, Mapping):
            limit_error = check_joint_targets(dict(joints), robot_limits)
            if limit_error:
                logger.warning("move_joints limit violation: %s", limit_error)
                return {"success": False, "message": limit_error}

        try:
            runtime = _get_runtime(self.node)
            result = runtime.move_to_joint_target(joints, group_name=group_name)
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
        ) as exc:
            logger.warning(f"Joint move failed: {exc}")
            return {"success": False, "message": str(exc)}

        return _move_sequence_result(
            action="조인트 이동 완료",
            payload=result,
        )


class MovePoseSkill(BaseSkill):
    """end-effector pose 이동 스킬"""

    name = "move_pose"
    side_effects = ("arm_motion",)
    input_schema = {"type": "object", "properties": {"target_pose": {"type": "object"}, "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}, "frame_id": {"type": "string"}}, "oneOf": [{"required": ["target_pose"]}, {"required": ["x", "y", "z"]}], "additionalProperties": True}
    description = (
        "end-effector 를 목표 pose로 이동합니다. "
        "target_pose 또는 x/y/z 좌표(미터, 기본 프레임 base_link: x=앞/y=왼쪽/z=위)가 필요하며 "
        "orientation 또는 roll/pitch/yaw를 함께 줄 수 있습니다. "
        "map 등 다른 프레임의 좌표는 frame_id로 지정하면 실행 시점 TF로 자동 변환됩니다."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        try:
            pose_goal_from_params(params, default_frame="base_link")
        except ManipulationConfigError:
            return False
        return True

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        group_name = str(params.get("group_name") or "").strip() or None
        end_effector_link = str(params.get("end_effector_link") or "").strip()
        cartesian = bool(params.get("cartesian", False))

        try:
            runtime = _get_runtime(self.node)
            pose = pose_goal_from_params(
                params,
                default_frame=runtime.config.base_frame,
            )
            pose = _transform_pose_goal(self.node, pose, runtime.config.base_frame)
            result = runtime.move_to_pose_target(
                pose,
                group_name=group_name,
                end_effector_link=end_effector_link,
                cartesian=cartesian,
            )
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
        ) as exc:
            logger.warning(f"Pose move failed: {exc}")
            return {"success": False, "message": str(exc)}

        return _move_sequence_result(
            action="Pose 이동 완료",
            payload=result,
        )
