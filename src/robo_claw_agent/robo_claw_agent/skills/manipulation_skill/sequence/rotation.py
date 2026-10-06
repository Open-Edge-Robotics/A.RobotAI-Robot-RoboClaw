"""베이스 회전을 이용한 도달성 확보/방위 계산 헬퍼.

Stretch3는 팔이 몸통 오른쪽에 고정되어 있어, IK로 도달 불가능한 목표는
베이스를 회전시켜 팔 작업축에 맞추는 방식으로 해결한다. 이 모듈은 그 회전각
계산과, 회전이 실제로 필요한지 판단하는 로직을 담당한다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
    pose_goal_from_params,
)
from robo_claw_agent.manipulation_runtime.stretch_kinematics import (
    compute_align_rotation_from_bearing_rad,
    compute_align_rotation_rad,
    compute_centering_rotation_rad,
)
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.limits import check_rotation

from ..core import _get_runtime, _resolve_target_pose, _transform_pose_goal
from .joint_state import _joint_positions

# Stretch3 팔 회전 중 안전 임계치. right_side_pick_ready(0.10m) 정도는 허용하되
# reach(0.32m)처럼 실제로 뻗은 상태에서는 회전을 거부한다.
_ROTATE_SAFE_WRIST_EXTENSION_M = 0.15


def _localize_rotation_with_base_camera(
    node: Any, params: dict[str, Any], target_object: str
) -> tuple[float | None, dict[str, Any]]:
    """베이스(정면) 카메라로 target_object를 탐지해 필요 회전각(rad)을 계산한다.

    3D pose(depth)가 함께 있으면 IK 기반 ``compute_align_rotation_rad``로 정밀 계산하고,
    없으면 ``bearing_offset_deg``만으로 ``compute_align_rotation_from_bearing_rad``를 쓴다.
    탐지 자체에 실패하면 (None, find_result)를 반환한다.
    """
    from robo_claw_agent.skills.perception_skill import FindObjectSkill

    finder = FindObjectSkill()
    finder.set_node(node)
    find_params = dict(params)
    find_params["target_object"] = target_object
    find_params.pop("camera", None)  # 베이스(기본) 카메라 사용을 보장
    find_result = finder.execute(find_params)
    if not find_result.get("success", False):
        return None, find_result

    target_pose_meta = find_result.get("target_pose")
    if isinstance(target_pose_meta, Mapping):
        try:
            runtime = _get_runtime(node)
            pose = pose_goal_from_params(
                {"target_pose": target_pose_meta}, default_frame=runtime.config.base_frame
            )
            pose = _transform_pose_goal(node, pose, runtime.config.base_frame)
            xyz = (
                float(pose.position["x"]),
                float(pose.position["y"]),
                float(pose.position["z"]),
            )
            theta = compute_centering_rotation_rad(xyz)
            if theta is not None:
                return theta, find_result
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
        ):
            pass  # depth 기반 계산 실패 시 방위각 기반으로 폴백

    bearing_deg = find_result.get("bearing_offset_deg")
    if bearing_deg is None:
        return None, find_result
    theta = compute_align_rotation_from_bearing_rad(math.radians(float(bearing_deg)))
    return theta, find_result


def _align_base_if_unreachable(
    skill: BaseSkill,
    runtime: Any,
    params: dict[str, Any],
    target_pose: Any,
    *,
    default_frame: str,
) -> tuple[Any, dict[str, Any]]:
    """Stretch 백엔드에서 target_pose가 회전 없이 도달 불가능하면 필요 각도만큼
    베이스를 회전한 뒤 pose를 재조회/재변환해 돌려준다.

    object_name 기반 pose만 재조회가 안전하다(메모리+최신 TF로 다시 계산 가능).
    x/y/z 직접 지정은 정적 값이라 회전 후에도 그대로 남아 대상에서 제외한다.
    MoveIt 백엔드에서는 아무 일도 하지 않고 그대로 반환한다(stretch_backend 지연 import
    자체를 하지 않아 control_msgs 의존이 추가되지 않는다).
    """
    info: dict[str, Any] = {"rotated": False, "rotation_deg": None}
    if runtime.config.backend != "stretch":
        return target_pose, info

    object_name = str(params.get("object_name") or "").strip()
    if not object_name:
        return target_pose, info

    from robo_claw_agent.manipulation_runtime import stretch_backend as sb

    position = target_pose.position
    xyz = (float(position["x"]), float(position["y"]), float(position["z"]))
    if sb.check_reachable(xyz):
        return target_pose, info

    joints = _joint_positions(skill, ["wrist_extension"])
    current_ext = None if joints is None else joints.get("wrist_extension")
    if current_ext is None or current_ext > _ROTATE_SAFE_WRIST_EXTENSION_M:
        raise ManipulationError(
            "팔 상태를 확인할 수 없거나 이미 뻗어 있어 회전 정렬을 시도하지 않습니다. "
            "stow_for_navigation 또는 prepare_right_side_pick으로 먼저 접으세요."
        )

    theta_rad = compute_align_rotation_rad(xyz)
    if theta_rad is None:
        raise ManipulationError(
            "목표 위치가 회전으로도 도달 불가능한 범위입니다(거리/높이가 팔 가동 범위를 초과)."
        )
    angle_deg = math.degrees(theta_rad)

    robot_limits = getattr(skill.node, "_robot_limits_dict", None)
    limit_error = check_rotation(angle_deg, robot_limits)
    if limit_error:
        raise ManipulationError(f"회전 정렬 실패: {limit_error}")

    runtime.move_to_joint_target({"rotate_mobile_base": theta_rad}, group_name="base")

    rotated_pose = _resolve_target_pose(skill.node, params, default_frame=default_frame)
    rotated_position = rotated_pose.position
    rotated_xyz = (
        float(rotated_position["x"]),
        float(rotated_position["y"]),
        float(rotated_position["z"]),
    )
    if not sb.check_reachable(rotated_xyz):
        raise ManipulationError(
            f"베이스를 {angle_deg:.1f}도 회전했지만 여전히 목표 위치에 도달할 수 없습니다."
        )

    info.update({"rotated": True, "rotation_deg": angle_deg})
    return rotated_pose, info
