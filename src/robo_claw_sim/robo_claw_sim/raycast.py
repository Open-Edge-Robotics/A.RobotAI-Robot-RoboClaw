"""LaserScan / 지도 합성(순수 파이썬).

Ray marching 으로 그리드를 샘플링한다. 정확도가 아니라 재현성과 속도가 목적이므로
DDA 대신 ``resolution/2`` 간격 샘플링을 사용한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .world import Pose2D, World


@dataclass(frozen=True)
class ScanConfig:
    """LaserScan 파라미터 기본값(sensor_msgs/LaserScan 규약)."""

    angle_min: float = -math.pi
    angle_max: float = math.pi
    num_beams: int = 360
    range_min: float = 0.12
    range_max: float = 12.0
    frame_id: str = "base_scan"

    @property
    def angle_increment(self) -> float:
        if self.num_beams <= 1:
            return 0.0
        return (self.angle_max - self.angle_min) / float(self.num_beams - 1)

    def bearings(self) -> list[float]:
        return [self.angle_min + i * self.angle_increment for i in range(self.num_beams)]


def cast_ray(world: World, origin: Pose2D, bearing: float, config: ScanConfig) -> float:
    """단일 광선 거리. 아무것도 맞지 않으면 ``inf``."""
    step = max(world.resolution * 0.5, 1e-3)
    distance = config.range_min
    cos_b = math.cos(bearing)
    sin_b = math.sin(bearing)
    while distance <= config.range_max:
        x = origin.x + distance * cos_b
        y = origin.y + distance * sin_b
        if not world.is_free(x, y):
            return distance
        distance += step
    return math.inf


def cast_scan(
    world: World,
    config: ScanConfig | None = None,
    sensor_offset: Pose2D | None = None,
) -> list[float]:
    """전 방위 LaserScan ranges.

    ``sensor_offset``은 base_link 기준 센서 장착 위치/방향이다. yaw는 로봇 yaw에
    더해진다(고정 마운트 가정).
    """
    cfg = config or ScanConfig()
    offset = sensor_offset or Pose2D()
    origin = _sensor_origin(world.robot, offset)
    ranges: list[float] = []
    for bearing in cfg.bearings():
        distance = cast_ray(world, origin, bearing, cfg)
        ranges.append(distance if math.isfinite(distance) else math.inf)
    return ranges


def _sensor_origin(robot: Pose2D, offset: Pose2D) -> Pose2D:
    cos_y = math.cos(robot.yaw)
    sin_y = math.sin(robot.yaw)
    return Pose2D(
        x=robot.x + offset.x * cos_y - offset.y * sin_y,
        y=robot.y + offset.x * sin_y + offset.y * cos_y,
        yaw=robot.yaw + offset.yaw,
    )


def occupancy_values(world: World) -> list[int]:
    """nav_msgs/OccupancyGrid.data (row-major, 0=free / 100=occupied)."""
    occupied = [0] * (world.cols * world.rows)
    for obstacle in world.obstacles:
        if hasattr(obstacle, "radius"):  # 원형
            _rasterize_circle(world, obstacle, occupied)  # type: ignore[arg-type]
        else:  # 사각형
            _rasterize_box(world, obstacle, occupied)  # type: ignore[arg-type]
    return occupied


def _rasterize_box(world: World, obstacle, occupied: list[int]) -> None:  # noqa: ANN001
    min_x = max(0, int((obstacle.x - obstacle.width / 2) / world.resolution))
    max_x = min(world.cols - 1, int((obstacle.x + obstacle.width / 2) / world.resolution))
    min_y = max(0, int((obstacle.y - obstacle.height / 2) / world.resolution))
    max_y = min(world.rows - 1, int((obstacle.y + obstacle.height / 2) / world.resolution))
    for row in range(min_y, max_y + 1):
        base = row * world.cols
        for col in range(min_x, max_x + 1):
            occupied[base + col] = 100


def _rasterize_circle(world: World, obstacle, occupied: list[int]) -> None:  # noqa: ANN001
    min_x = max(0, int((obstacle.x - obstacle.radius) / world.resolution))
    max_x = min(world.cols - 1, int((obstacle.x + obstacle.radius) / world.resolution))
    min_y = max(0, int((obstacle.y - obstacle.radius) / world.resolution))
    max_y = min(world.rows - 1, int((obstacle.y + obstacle.radius) / world.resolution))
    for row in range(min_y, max_y + 1):
        base = row * world.cols
        cy = row * world.resolution
        for col in range(min_x, max_x + 1):
            cx = col * world.resolution
            if math.hypot(cx - obstacle.x, cy - obstacle.y) <= obstacle.radius:
                occupied[base + col] = 100
