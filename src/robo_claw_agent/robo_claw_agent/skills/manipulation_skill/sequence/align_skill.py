"""Stretch3 오른쪽 팔 작업축을 정면 물체 방향에 맞추는 베이스 회전 스킬."""

from __future__ import annotations

import logging
import math
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)
from robo_claw_agent.manipulation_runtime.stretch_kinematics import compute_centering_rotation_rad
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.limits import check_rotation

from ..core import _get_runtime, _resolve_target_pose
from .params import _has_target_hint

logger = logging.getLogger(__name__)


class AlignRightArmToFrontSkill(BaseSkill):
    """Stretch3 오른쪽 팔 작업축을 현재 정면 물체 방향에 맞추는 베이스 회전."""

    name = "align_right_arm_to_front"
    is_internal = True
    input_schema = {"type": "object", "properties": {
        "angle_deg": {"type": "number", "default": 90.0}, "target_pose": {"type": "object"},
        "object_name": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"}
    }, "additionalProperties": True}
    description = (
        "Stretch3는 팔이 몸통 오른쪽에 있어 정면 물체를 잡기 전 베이스를 회전해야 "
        "합니다. target_pose/x,y,z 또는 object_name(메모리에 3D pose가 있는 경우)을 "
        "주면 그 물체가 오른쪽 팔 작업축에 오도록 필요 회전각을 자동 계산합니다. "
        "목표 정보를 주지 않으면 angle_deg(기본 90도)만큼 고정 회전합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        try:
            runtime = _get_runtime(self.node)
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
        ) as exc:
            logger.warning("Right arm work-axis alignment failed: %s", exc)
            return {"success": False, "message": f"오른쪽 팔 작업축 정렬 실패: {exc}"}

        angle_deg = float(params.get("angle_deg", 90.0))
        angle_source = "fixed"

        if _has_target_hint(params):
            try:
                target_pose = _resolve_target_pose(
                    self.node, params, default_frame=runtime.config.base_frame
                )
            except ManipulationConfigError as exc:
                logger.warning("Failed to resolve target pose for rotation angle computation: %s", exc)
                return {
                    "success": False,
                    "message": f"회전각 계산용 목표 pose 확인 실패: {exc}",
                }

            position = target_pose.position
            theta_rad = compute_centering_rotation_rad(
                (float(position["x"]), float(position["y"]), float(position["z"]))
            )
            if theta_rad is None:
                return {
                    "success": False,
                    "message": (
                        "목표 위치가 회전으로도 도달 불가능한 범위입니다 "
                        "(거리/높이가 팔 가동 범위를 초과했습니다)."
                    ),
                }
            angle_deg = math.degrees(theta_rad)
            angle_source = "computed"

        # ROBOT_LIMITS 하드 검증: 최대 회전 각도 (계산된 각도도 동일하게 검증)
        robot_limits = getattr(self.node, "_robot_limits_dict", None)
        limit_error = check_rotation(angle_deg, robot_limits)
        if limit_error:
            logger.warning("Right arm work-axis alignment rotation limit violation: %s", limit_error)
            return {
                "success": False,
                "message": limit_error,
                "angle_deg": angle_deg,
                "angle_source": angle_source,
            }

        angle_rad = math.radians(angle_deg)
        try:
            runtime.move_to_joint_target(
                {"rotate_mobile_base": angle_rad},
                group_name="base",
            )
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
            ValueError,
            TypeError,
        ) as exc:
            logger.warning("Right arm work-axis alignment failed: %s", exc)
            return {
                "success": False,
                "message": f"오른쪽 팔 작업축 정렬 실패: {exc}",
                "angle_deg": angle_deg,
                "angle_source": angle_source,
            }

        return {
            "success": True,
            "message": f"오른쪽 팔 작업축 정렬 회전 완료 ({angle_deg:.1f}도, Stretch position mode)",
            "angle_deg": angle_deg,
            "angle_source": angle_source,
            "joint": "rotate_mobile_base",
        }
