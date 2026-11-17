"""시나리오 → MJCF 변환 테스트(순수 파이썬, mujoco 미설치 환경에서도 실행)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest
from robo_claw_sim.backends.scene import (
    OBSTACLE_PREFIX,
    ROBOT_BODY_NAME,
    ROBOT_GEOM_NAME,
    WALL_PREFIX,
    build_scene_xml,
)
from robo_claw_sim.world import BoxObstacle, CircleObstacle, Pose2D, SimObject, World

pytestmark = pytest.mark.unit


def _geoms(xml: str) -> dict[str, ET.Element]:
    root = ET.fromstring(xml)
    found: dict[str, ET.Element] = {}
    for geom in root.iter("geom"):
        name = geom.get("name")
        if name:
            found[name] = geom
    return found


def _vec(text: str) -> list[float]:
    return [float(value) for value in text.split()]


def test_scene_is_parseable_mjcf() -> None:
    xml = build_scene_xml(World())
    root = ET.fromstring(xml)

    assert root.tag == "mujoco"
    assert root.find("worldbody") is not None
    option = root.find("option")
    assert option is not None
    assert option.get("timestep")


def test_scene_has_four_boundary_walls() -> None:
    geoms = _geoms(build_scene_xml(World(width=8.0, height=6.0)))
    walls = {name: geom for name, geom in geoms.items() if name.startswith(WALL_PREFIX)}

    assert set(walls) == {
        f"{WALL_PREFIX}west",
        f"{WALL_PREFIX}east",
        f"{WALL_PREFIX}south",
        f"{WALL_PREFIX}north",
    }
    # 서쪽/동쪽 벽은 경계 바로 바깥에 서 있어야 한다.
    assert _vec(walls[f"{WALL_PREFIX}west"].get("pos"))[0] < 0.0
    assert _vec(walls[f"{WALL_PREFIX}east"].get("pos"))[0] > 8.0
    assert _vec(walls[f"{WALL_PREFIX}south"].get("pos"))[1] < 0.0
    assert _vec(walls[f"{WALL_PREFIX}north"].get("pos"))[1] > 6.0


def test_scene_places_robot_at_scenario_pose() -> None:
    world = World(robot=Pose2D(x=1.25, y=2.5, yaw=0.0), robot_radius=0.28)
    xml = build_scene_xml(world)
    root = ET.fromstring(xml)

    body = root.find(f".//body[@name='{ROBOT_BODY_NAME}']")
    assert body is not None, "로봇 body 가 없습니다"
    x, y, z = _vec(body.get("pos"))
    assert (round(x, 3), round(y, 3)) == (1.25, 2.5)
    assert z > 0.0, "로봇은 바닥과 접촉하지 않도록 띄워야 합니다"
    assert body.find("freejoint") is not None, "베이스는 자유 관절이어야 합니다"

    geom = body.find(f"./geom[@name='{ROBOT_GEOM_NAME}']")
    assert geom is not None
    assert round(_vec(geom.get("size"))[0], 3) == 0.28


def test_scene_maps_obstacles_by_type() -> None:
    world = World(
        obstacles=[
            BoxObstacle(name="wall unit", x=4.0, y=3.0, width=0.6, height=1.6),
            CircleObstacle(name="pillar", x=6.2, y=1.6, radius=0.35),
        ]
    )
    geoms = _geoms(build_scene_xml(world))

    box = geoms[f"{OBSTACLE_PREFIX}wall_unit"]
    assert box.get("type") == "box"
    assert [round(v, 3) for v in _vec(box.get("size"))[:2]] == [0.3, 0.8]

    pillar = geoms[f"{OBSTACLE_PREFIX}pillar"]
    assert pillar.get("type") == "cylinder"
    assert round(_vec(pillar.get("size"))[0], 3) == 0.35


def test_scene_sanitizes_obstacle_names() -> None:
    world = World(obstacles=[BoxObstacle(name="a<b>&c", x=1.0, y=1.0, width=1, height=1)])
    geoms = _geoms(build_scene_xml(world))

    assert f"{OBSTACLE_PREFIX}a_b__c" in geoms


def test_scene_skips_graspable_objects() -> None:
    """T1 객체는 파지 판정용 정답이므로 Phase 0 장면에는 넣지 않는다(광선/판정 parity 유지)."""
    xml = build_scene_xml(World(objects=[SimObject(name="cup_1", class_id="cup", x=2.2, y=1.2)]))
    assert "cup_1" not in xml
