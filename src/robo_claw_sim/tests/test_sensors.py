"""센서 합성(LaserScan/지도/검출) 테스트."""

from __future__ import annotations

import math

import pytest
from robo_claw_sim.perception import CameraConfig, detect
from robo_claw_sim.raycast import ScanConfig, cast_scan, occupancy_values
from robo_claw_sim.world import BoxObstacle, Pose2D, SimObject, World

pytestmark = pytest.mark.unit


def _world() -> World:
    world = World(width=8.0, height=6.0, resolution=0.1, robot_radius=0.2)
    world.robot = Pose2D(x=2.0, y=2.0, yaw=0.0)
    world.add_obstacle(BoxObstacle(name="wall", x=4.0, y=2.0, width=0.2, height=4.0))
    return world


def test_scan_hits_wall_at_expected_distance() -> None:
    world = _world()
    config = ScanConfig(num_beams=361, angle_min=-math.pi, angle_max=math.pi)

    ranges = cast_scan(world, config)

    assert len(ranges) == 361
    forward = ranges[180]  # bearing 0 rad
    assert forward == pytest.approx(1.9, abs=0.15)


def test_scan_reports_inf_when_nothing_ahead() -> None:
    world = World(width=20.0, height=20.0, resolution=0.2, robot_radius=0.2)
    world.robot = Pose2D(x=10.0, y=10.0, yaw=0.0)
    config = ScanConfig(num_beams=9, range_max=3.0)

    ranges = cast_scan(world, config)

    assert all(math.isinf(value) for value in ranges)


def test_scan_respects_max_range() -> None:
    world = _world()
    config = ScanConfig(num_beams=181, range_max=0.5)

    ranges = cast_scan(world, config)

    assert all(value == math.inf or value <= 0.5 for value in ranges)


def test_scan_increment_matches_sensor_msgs_convention() -> None:
    config = ScanConfig(num_beams=360)
    assert config.angle_increment == pytest.approx(2 * math.pi / 359)


def test_occupancy_grid_shape_and_obstacles() -> None:
    world = _world()

    data = occupancy_values(world)

    assert len(data) == world.cols * world.rows
    wall_col = int(4.0 / world.resolution)
    wall_row = int(2.0 / world.resolution)
    assert data[wall_row * world.cols + wall_col] == 100
    assert data[0] == 0


def test_detections_include_ground_truth_class_and_range() -> None:
    world = _world()
    world.add_object(SimObject(name="cup1", class_id="cup", x=3.0, y=2.0, radius=0.06))

    detections = detect(world, CameraConfig())

    assert len(detections) == 1
    assert detections[0].class_id == "cup"
    assert detections[0].range_m == pytest.approx(1.0, abs=1e-3)
    assert detections[0].center_u == pytest.approx(320.0, abs=5.0)


def test_detections_ignore_objects_outside_fov_or_range() -> None:
    world = _world()
    world.add_object(SimObject(name="behind", class_id="cup", x=1.0, y=2.0))
    world.add_object(SimObject(name="far", class_id="cup", x=2.0 + 20.0, y=2.0))

    detections = detect(world, CameraConfig(max_range=6.0))

    assert detections == []


def test_detection_dropout_is_deterministic_with_seed() -> None:
    world = _world()
    for index in range(20):
        world.add_object(SimObject(name=f"obj{index}", class_id="box", x=3.0 + index * 0.1, y=2.0))

    first = detect(world, CameraConfig(), seed=7, false_negative_rate=0.5)
    second = detect(world, CameraConfig(), seed=7, false_negative_rate=0.5)
    none_dropped = detect(world, CameraConfig(), seed=7, false_negative_rate=0.0)

    assert [d.object_name for d in first] == [d.object_name for d in second]
    assert len(none_dropped) == 20
    assert len(first) < len(none_dropped), "누락 주입이 동작하지 않습니다"
