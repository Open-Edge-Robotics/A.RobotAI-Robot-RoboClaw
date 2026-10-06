import math
from typing import Any

_DIRECTION_YAW_DEG = {
    "east": 0.0,
    "e": 0.0,
    "동": 0.0,
    "동쪽": 0.0,
    "north": 90.0,
    "n": 90.0,
    "북": 90.0,
    "북쪽": 90.0,
    "west": 180.0,
    "w": 180.0,
    "서": 180.0,
    "서쪽": 180.0,
    "south": -90.0,
    "s": -90.0,
    "남": -90.0,
    "남쪽": -90.0,
}


def normalize_angle_rad(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))


def shortest_angular_delta_rad(current_yaw: float, target_yaw: float) -> float:
    return normalize_angle_rad(target_yaw - current_yaw)


def direction_to_yaw_rad(direction: Any) -> float | None:
    key = str(direction or "").strip().lower().replace(" ", "")
    if not key:
        return None
    yaw_deg = _DIRECTION_YAW_DEG.get(key)
    if yaw_deg is None:
        return None
    return math.radians(yaw_deg)


def yaw_to_point_rad(current_x: float, current_y: float, target_x: float, target_y: float) -> float | None:
    dx = float(target_x) - float(current_x)
    dy = float(target_y) - float(current_y)
    if math.hypot(dx, dy) < 0.05:
        return None
    return math.atan2(dy, dx)


def yaw_targets_from_offsets_rad(start_yaw: float, offsets_rad: list[float]) -> list[float]:
    return [normalize_angle_rad(start_yaw + offset) for offset in offsets_rad]
