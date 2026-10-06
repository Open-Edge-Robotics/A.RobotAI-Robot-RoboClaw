from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.limits import check_joint_targets

from .core import _get_runtime, _move_sequence_result

logger = logging.getLogger(__name__)

# Stretch3 헤드 pan/tilt 참고 기본값(rad). Hello Robot 공식 stretch_funmap 패키지
# (mapping.py의 HeadScan.execute_front, manipulation_planning.py의 move_head)에서
# 검증된 값을 그대로 참고했다. 실제 캘리브레이션/카메라 마운트에 따라
# manipulation_named_poses_json으로 덮어쓸 수 있다.
_DEFAULT_HEAD_POSES: dict[str, dict[str, float]] = {
    # 테이블탑/바닥 물체 탐색용: 정면을 약 -46도 숙여 내려다본다.
    "search_head_down": {"joint_head_pan": 0.1, "joint_head_tilt": -0.8},
    # 팔/그리퍼가 있는 오른쪽을 내려다본다(파지 관찰 보조용).
    "gripper_side_head": {"joint_head_pan": -1.8, "joint_head_tilt": -0.8},
    # 주행/대기 안전 자세로 복귀(정면 수평).
    "travel_head": {"joint_head_pan": 0.0, "joint_head_tilt": 0.0},
}

_HEAD_JOINT_NAMES = ("joint_head_pan", "joint_head_tilt")


class HeadPanTiltSkill(BaseSkill):
    """Stretch3 헤드(카메라 마운트) pan/tilt 제어 스킬."""

    name = "head_pan_tilt"
    input_schema = {"type": "object", "properties": {
        "pan": {"type": "number"}, "tilt": {"type": "number"},
        "pose_name": {"type": "string"}, "pan_deg": {"type": "number"}, "tilt_deg": {"type": "number"}
    }, "oneOf": [{"required": ["pose_name"]}, {"required": ["pan", "tilt"]}, {"required": ["pan_deg", "tilt_deg"]}], "additionalProperties": False}
    description = (
        "Stretch3 헤드 카메라의 pan(좌우)/tilt(상하) 각도를 제어합니다. "
        "pose_name(예: search_head_down, gripper_side_head, travel_head)으로 사전 정의된 "
        "자세를 쓰거나, pan_deg/tilt_deg(또는 pan_rad/tilt_rad)로 직접 각도를 지정할 수 "
        "있습니다. Stretch3 전용 기능이며 다른 백엔드에서는 지원하지 않습니다."
    )

    def validate_params(self, params: dict[str, Any]) -> bool:
        if str(params.get("pose_name") or "").strip():
            return True
        keys = ("pan_deg", "tilt_deg", "pan_rad", "tilt_rad")
        return any(key in params for key in keys)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        try:
            runtime = _get_runtime(self.node)
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
        ) as exc:
            logger.warning("Head pan/tilt control failed: %s", exc)
            return {"success": False, "message": f"헤드 pan/tilt 제어 실패: {exc}"}

        if runtime.config.backend != "stretch":
            return {
                "success": False,
                "message": "head_pan_tilt는 Stretch3 전용 기능입니다(현재 백엔드가 stretch가 아님).",
            }

        pose_name = str(params.get("pose_name") or "").strip()
        joints: dict[str, float] = {}

        if pose_name:
            # dict 형태로 설정된 named pose의 실제 joint 값은
            # config.named_poses(alias -> 문자열 이름 맵)가 아니라
            # config.named_pose_joint_values에 저장된다(manipulation_runtime/config.py 참조).
            configured = runtime.config.named_pose_joint_values.get(pose_name)
            pose = configured if configured else _DEFAULT_HEAD_POSES.get(pose_name)
            if not isinstance(pose, Mapping) or not pose:
                return {
                    "success": False,
                    "message": (
                        f"헤드 named pose '{pose_name}'를 찾을 수 없습니다. "
                        f"사용 가능한 기본 pose: {sorted(_DEFAULT_HEAD_POSES)} "
                        "또는 manipulation_named_poses_json에 정의하세요."
                    ),
                }
            joints = {
                name: float(value) for name, value in pose.items() if name in _HEAD_JOINT_NAMES
            }
            if not joints:
                return {
                    "success": False,
                    "message": f"named pose '{pose_name}'에 joint_head_pan/joint_head_tilt 값이 없습니다.",
                }
        else:
            if "pan_rad" in params:
                joints["joint_head_pan"] = float(params["pan_rad"])
            elif "pan_deg" in params:
                joints["joint_head_pan"] = math.radians(float(params["pan_deg"]))
            if "tilt_rad" in params:
                joints["joint_head_tilt"] = float(params["tilt_rad"])
            elif "tilt_deg" in params:
                joints["joint_head_tilt"] = math.radians(float(params["tilt_deg"]))

        if not joints:
            return {
                "success": False,
                "message": "pose_name 또는 pan/tilt 값(pan_deg/tilt_deg/pan_rad/tilt_rad) 중 하나가 필요합니다.",
            }

        robot_limits = getattr(self.node, "_robot_limits_dict", None)
        limit_error = check_joint_targets(joints, robot_limits)
        if limit_error:
            logger.warning("Head pan/tilt limit violation: %s", limit_error)
            return {"success": False, "message": limit_error}

        try:
            result = runtime.move_to_joint_target(joints, group_name="head")
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
            ValueError,
            TypeError,
        ) as exc:
            logger.warning("Head pan/tilt move failed: %s", exc)
            return {"success": False, "message": str(exc)}

        return _move_sequence_result(
            action=f"헤드 pan/tilt 이동 완료{f' ({pose_name})' if pose_name else ''}",
            payload={**result, "pose_name": pose_name or None},
        )
