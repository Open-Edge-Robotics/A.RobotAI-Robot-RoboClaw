"""카메라 검출 합성(순수 파이썬).

RoboClaw는 vision 노드의 ``Detection2DArray`` 를 소비한다. Gazebo에서는 실제
YOLO 추론이 필요하지만, 검증 목적에서는 "세계 정답을 픽셀 좌표로 투영"하는 것으로
충분하고 오히려 정답 라벨을 알 수 있다는 장점이 있다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from .world import Pose2D, World


@dataclass(frozen=True)
class CameraConfig:
    width: int = 640
    height: int = 480
    hfov: float = 1.2  # rad
    min_range: float = 0.2
    max_range: float = 6.0
    frame_id: str = "camera_link"


@dataclass(frozen=True)
class Detection:
    """Detection2DArray 항목에 대응하는 값 객체(정답 이름 포함)."""

    object_name: str
    class_id: str
    center_u: float
    center_v: float
    size_u: float
    size_v: float
    range_m: float


def detect(
    world: World,
    config: CameraConfig | None = None,
    camera_offset: Pose2D | None = None,
    seed: int = 0,
    false_negative_rate: float = 0.0,
) -> list[Detection]:
    """전방 카메라 시야에 들어온 객체를 거리순으로 반환한다.

    ``false_negative_rate`` 는 검출 누락을 결정론적으로 재현하기 위한 값이다
    (seed 고정). 정리/파지 시나리오에서 "객체를 못 봄" 상태를 만들 때 사용한다.
    """
    cfg = config or CameraConfig()
    offset = camera_offset or Pose2D()
    origin = _camera_origin(world.robot, offset)
    rng = random.Random(seed)
    half_fov = cfg.hfov / 2.0

    found: list[Detection] = []
    for obj in world.objects:
        dx = obj.x - origin.x
        dy = obj.y - origin.y
        distance = math.hypot(dx, dy)
        if distance < cfg.min_range or distance > cfg.max_range:
            continue
        bearing = _normalize(math.atan2(dy, dx) - origin.yaw)
        if abs(bearing) > half_fov:
            continue
        if false_negative_rate > 0.0 and rng.random() < false_negative_rate:
            continue

        # 각크기 -> 픽셀 크기 (원거리 소실 방지 위해 최소 4px)
        angular_size = 2.0 * math.atan2(obj.radius, max(distance, 1e-3))
        size_u = max(4.0, angular_size / cfg.hfov * cfg.width)
        center_u = cfg.width / 2.0 + (bearing / half_fov) * (cfg.width / 2.0)
        found.append(
            Detection(
                object_name=obj.name,
                class_id=obj.class_id,
                center_u=center_u,
                center_v=cfg.height * 0.55,
                size_u=size_u,
                size_v=size_u,
                range_m=distance,
            )
        )

    found.sort(key=lambda det: det.range_m)
    return found


def _camera_origin(robot: Pose2D, offset: Pose2D) -> Pose2D:
    cos_y = math.cos(robot.yaw)
    sin_y = math.sin(robot.yaw)
    return Pose2D(
        x=robot.x + offset.x * cos_y - offset.y * sin_y,
        y=robot.y + offset.x * sin_y + offset.y * cos_y,
        yaw=robot.yaw + offset.yaw,
    )


def _normalize(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
