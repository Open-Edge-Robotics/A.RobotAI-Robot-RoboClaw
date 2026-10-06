from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    MoveItManipulationRuntime,
    PoseGoal,
    pose_goal_from_params,
)

logger = logging.getLogger(__name__)


def _get_runtime(node: Any) -> MoveItManipulationRuntime:
    if node is None:
        raise ManipulationConfigError("ROS 노드에 접근할 수 없습니다.")

    runtime = getattr(node, "_manipulation_runtime", None)
    if runtime is None:
        backend = getattr(node, "_manipulation_backend", None)
        runtime = MoveItManipulationRuntime(node, backend=backend)
        node._manipulation_runtime = runtime
    return runtime


def _transform_pose_goal(node: Any, pose: PoseGoal, target_frame: str) -> PoseGoal:
    """PoseGoal을 target_frame으로 '지금 시점' TF를 이용해 변환한다.

    시맨틱 메모리에 저장된 pose는 감지 시점의 값이다. 로봇이 navigate_to 등으로
    이동한 뒤 grasp/place를 실행하면, 감지 당시 base_link 기준으로 저장된 pose는
    더 이상 물체의 실제 위치를 가리키지 않는다. 따라서 소비 시점(지금)의 최신 TF로
    다시 변환해야 하며, 저장된 stamp가 아니라 rclpy.time.Time()(=latest available)을
    사용한다.
    """
    if pose.frame_id == target_frame:
        return pose

    tf_buffer = getattr(node, "_tf_buffer", None)
    if tf_buffer is None:
        raise ManipulationConfigError(
            f"pose frame '{pose.frame_id}' 을(를) '{target_frame}' 으로 변환할 TF 버퍼가 없습니다."
        )

    try:
        import rclpy.time
        import tf2_geometry_msgs  # noqa: F401  (PoseStamped 변환 등록용 side-effect import)
        from geometry_msgs.msg import PoseStamped
        from rclpy.duration import Duration

        ps = PoseStamped()
        ps.header.frame_id = pose.frame_id
        ps.header.stamp = rclpy.time.Time().to_msg()
        ps.pose.position.x = float(pose.position["x"])
        ps.pose.position.y = float(pose.position["y"])
        ps.pose.position.z = float(pose.position["z"])
        ps.pose.orientation.x = float(pose.orientation["x"])
        ps.pose.orientation.y = float(pose.orientation["y"])
        ps.pose.orientation.z = float(pose.orientation["z"])
        ps.pose.orientation.w = float(pose.orientation["w"])

        transformed = tf_buffer.transform(ps, target_frame, timeout=Duration(seconds=0.5))
    except ManipulationConfigError:
        raise
    except Exception as exc:
        raise ManipulationConfigError(
            f"pose frame 변환 실패 ({pose.frame_id} -> {target_frame}): {exc}"
        ) from exc

    return PoseGoal(
        frame_id=target_frame,
        position={
            "x": transformed.pose.position.x,
            "y": transformed.pose.position.y,
            "z": transformed.pose.position.z,
        },
        orientation={
            "x": transformed.pose.orientation.x,
            "y": transformed.pose.orientation.y,
            "z": transformed.pose.orientation.z,
            "w": transformed.pose.orientation.w,
        },
    )


def _resolve_pose_from_object_memory(
    node: Any,
    object_name: str,
    *,
    default_frame: str,
) -> Any | None:
    if (
        node is None
        or not object_name
        or not hasattr(node, "_memory")
        or getattr(node, "_memory", None) is None
    ):
        return None

    memory = node._memory
    if not hasattr(memory, "get_object_location"):
        return None

    object_info = memory.get_object_location(object_name)
    if not object_info:
        return None

    metadata = object_info.get("metadata", {})
    if not isinstance(metadata, Mapping):
        return None

    for key in ("target_pose", "pose", "manipulation_pose"):
        raw_pose = metadata.get(key)
        if isinstance(raw_pose, Mapping):
            pose = pose_goal_from_params(
                {"target_pose": raw_pose},
                default_frame=default_frame,
            )
            return _transform_pose_goal(node, pose, default_frame)

    return None


def _resolve_target_pose(
    node: Any,
    params: dict[str, Any],
    *,
    default_frame: str,
) -> Any:
    if params.get("target_pose") or all(key in params for key in ("x", "y", "z")):
        pose = pose_goal_from_params(params, default_frame=default_frame)
        return _transform_pose_goal(node, pose, default_frame)

    object_name = str(params.get("object_name") or "").strip()
    pose = _resolve_pose_from_object_memory(
        node,
        object_name,
        default_frame=default_frame,
    )
    if pose is not None:
        return pose

    raise ManipulationConfigError(
        "target_pose 또는 x/y/z 좌표가 필요합니다. "
        "object_name 만 사용할 경우 메모리에 3D pose metadata가 저장되어 있어야 합니다."
    )


def _move_sequence_result(
    *,
    action: str,
    payload: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "success": True,
        "message": action,
        **payload,
        **(extra or {}),
    }
