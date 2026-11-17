"""RViz 시각화용 마커 스펙 생성(순수 파이썬, ROS 비의존).

시뮬레이터는 URDF/메시 없이 시나리오 세계만 가지므로, 장애물·객체·로봇
footprint 를 MarkerArray 로 그려 RViz 에서 바로 확인할 수 있게 한다.
"""

from __future__ import annotations

from .world import BoxObstacle, CircleObstacle, World

BOX = "box"
CYLINDER = "cylinder"
SPHERE = "sphere"

_HEIGHT = 0.6
_OBJECT_HEIGHT = 0.1


def _marker(
    namespace: str,
    marker_id: int,
    kind: str,
    x: float,
    y: float,
    z: float,
    sx: float,
    sy: float,
    sz: float,
    rgba: tuple[float, float, float, float],
) -> dict:
    return {
        "ns": namespace,
        "id": marker_id,
        "type": kind,
        "x": float(x),
        "y": float(y),
        "z": float(z),
        "sx": float(sx),
        "sy": float(sy),
        "sz": float(sz),
        "rgba": rgba,
    }


def world_markers(world: World) -> list[dict]:
    """시나리오 세계를 RViz 마커 스펙 목록으로 변환한다."""
    markers: list[dict] = [
        _marker(
            "robot",
            0,
            CYLINDER,
            world.robot.x,
            world.robot.y,
            0.1,
            world.robot_radius * 2.0,
            world.robot_radius * 2.0,
            0.2,
            (0.2, 0.45, 0.9, 0.8),
        )
    ]
    for index, obstacle in enumerate(world.obstacles):
        if isinstance(obstacle, BoxObstacle):
            markers.append(
                _marker(
                    "obstacles",
                    index,
                    BOX,
                    obstacle.x,
                    obstacle.y,
                    _HEIGHT / 2.0,
                    obstacle.width,
                    obstacle.height,
                    _HEIGHT,
                    (0.75, 0.72, 0.68, 0.9),
                )
            )
        elif isinstance(obstacle, CircleObstacle):
            markers.append(
                _marker(
                    "obstacles",
                    index,
                    CYLINDER,
                    obstacle.x,
                    obstacle.y,
                    _HEIGHT / 2.0,
                    obstacle.radius * 2.0,
                    obstacle.radius * 2.0,
                    _HEIGHT,
                    (0.75, 0.72, 0.68, 0.9),
                )
            )
    for index, obj in enumerate(world.objects):
        markers.append(
            _marker(
                "objects",
                index,
                SPHERE,
                obj.x,
                obj.y,
                _OBJECT_HEIGHT / 2.0,
                obj.radius * 2.0,
                obj.radius * 2.0,
                obj.radius * 2.0,
                (0.2, 0.7, 0.4, 0.9),
            )
        )
    return markers
