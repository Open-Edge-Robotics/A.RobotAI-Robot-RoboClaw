"""RViz 마커 스펙 테스트(순수 파이썬)."""

from __future__ import annotations

import pytest
from robo_claw_sim.viz import BOX, CYLINDER, SPHERE, world_markers
from robo_claw_sim.world import BoxObstacle, CircleObstacle, Pose2D, SimObject, World

pytestmark = pytest.mark.unit


def test_markers_cover_robot_obstacles_and_objects() -> None:
    world = World(
        robot=Pose2D(x=1.0, y=2.0),
        obstacles=[
            BoxObstacle(name="wall", x=4.0, y=3.0, width=0.4, height=2.0),
            CircleObstacle(name="pillar", x=6.0, y=1.0, radius=0.35),
        ],
        objects=[SimObject(name="cup_1", class_id="cup", x=2.0, y=1.0)],
    )

    markers = world_markers(world)
    kinds = [marker["type"] for marker in markers]

    assert kinds.count(CYLINDER) == 2  # 로봇 + 원형 장애물
    assert kinds.count(BOX) == 1
    assert kinds.count(SPHERE) == 1
    robot = next(m for m in markers if m["ns"] == "robot")
    assert (round(robot["x"], 3), round(robot["y"], 3)) == (1.0, 2.0)
    assert robot["sx"] == pytest.approx(world.robot_radius * 2.0)
