import math
from collections.abc import Mapping
from typing import Any

from .exceptions import ManipulationConfigError
from .types import PoseGoal


def quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> dict[str, float]:
    """RPY를 quaternion으로 변환"""

    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)

    return {
        "x": sr * cp * cy - cr * sp * sy,
        "y": cr * sp * cy + sr * cp * sy,
        "z": cr * cp * sy - sr * sp * cy,
        "w": cr * cp * cy + sr * sp * sy,
    }


def rpy_from_quaternion(quaternion: Mapping[str, Any]) -> tuple[float, float, float]:
    """quaternion(ROS Rz*Ry*Rx 컨벤션)을 RPY로 역변환. quaternion_from_rpy 의 역변환.

    Returns:
        (roll, pitch, yaw) in radians.
    """
    x = float(quaternion.get("x", 0.0))
    y = float(quaternion.get("y", 0.0))
    z = float(quaternion.get("z", 0.0))
    w = float(quaternion.get("w", 1.0))

    # roll (x-axis)
    sinr_cosp = 2.0 * (w * x + y * z)
    cosr_cosp = 1.0 - 2.0 * (x * x + y * y)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    # pitch (y-axis)
    sinp = 2.0 * (w * y - z * x)
    if abs(sinp) >= 1.0:
        pitch = math.copysign(math.pi / 2.0, sinp)
    else:
        pitch = math.asin(sinp)

    # yaw (z-axis)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return (roll, pitch, yaw)


def normalize_vector(raw_vector: Mapping[str, Any] | None) -> dict[str, float]:
    """접근/후퇴 벡터 정규화"""

    vector = raw_vector or {"x": 0.0, "y": 0.0, "z": 1.0}
    x = float(vector.get("x", 0.0))
    y = float(vector.get("y", 0.0))
    z = float(vector.get("z", 1.0))
    norm = math.sqrt(x * x + y * y + z * z)
    if norm <= 1e-9:
        raise ManipulationConfigError("접근/후퇴 벡터 길이는 0일 수 없습니다.")
    return {"x": x / norm, "y": y / norm, "z": z / norm}


def offset_pose(
    pose: PoseGoal,
    distance_m: float,
    direction: Mapping[str, float],
) -> PoseGoal:
    """지정 방향으로 pose를 오프셋"""

    return PoseGoal(
        frame_id=pose.frame_id,
        position={
            "x": pose.position["x"] + float(direction["x"]) * distance_m,
            "y": pose.position["y"] + float(direction["y"]) * distance_m,
            "z": pose.position["z"] + float(direction["z"]) * distance_m,
        },
        orientation=dict(pose.orientation),
    )


def pose_goal_from_params(
    params: Mapping[str, Any],
    *,
    pose_key: str = "target_pose",
    default_frame: str = "base_link",
) -> PoseGoal:
    """실행 파라미터에서 PoseGoal 파싱"""

    source = params.get(pose_key)
    if source is None:
        source = params

    if not isinstance(source, Mapping):
        raise ManipulationConfigError(f"'{pose_key}' 는 객체 형태여야 합니다.")

    position_source = source.get("position")
    if isinstance(position_source, Mapping):
        x = position_source.get("x")
        y = position_source.get("y")
        z = position_source.get("z")
    else:
        x = source.get("x")
        y = source.get("y")
        z = source.get("z")

    try:
        position = {"x": float(x), "y": float(y), "z": float(z)}
    except (TypeError, ValueError) as exc:
        raise ManipulationConfigError(
            "pose target에는 x, y, z 숫자값이 필요합니다."
        ) from exc

    orientation_source = source.get("orientation")
    if isinstance(orientation_source, Mapping):
        orientation = {
            "x": float(orientation_source.get("x", 0.0)),
            "y": float(orientation_source.get("y", 0.0)),
            "z": float(orientation_source.get("z", 0.0)),
            "w": float(orientation_source.get("w", 1.0)),
        }
    elif any(key in source for key in ("qx", "qy", "qz", "qw")):
        orientation = {
            "x": float(source.get("qx", 0.0)),
            "y": float(source.get("qy", 0.0)),
            "z": float(source.get("qz", 0.0)),
            "w": float(source.get("qw", 1.0)),
        }
    elif any(key in source for key in ("roll", "pitch", "yaw")):
        orientation = quaternion_from_rpy(
            float(source.get("roll", 0.0)),
            float(source.get("pitch", 0.0)),
            float(source.get("yaw", 0.0)),
        )
    else:
        orientation = {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}

    frame_id = str(source.get("frame_id") or params.get("frame_id") or default_frame)

    return PoseGoal(frame_id=frame_id, position=position, orientation=orientation)
