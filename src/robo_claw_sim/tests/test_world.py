"""세계 모델 계약 테스트(ROS 비의존)."""

from __future__ import annotations

import math

import pytest
from robo_claw_sim.nav import GoalPose, compute_twist, is_goal_reached, spin_twist
from robo_claw_sim.world import (
    BoxObstacle,
    CircleObstacle,
    Pose2D,
    SimObject,
    Twist2D,
    World,
)

pytestmark = pytest.mark.unit


def _open_world(**kwargs) -> World:
    return World(width=8.0, height=6.0, robot_radius=0.2, **kwargs)


def test_robot_moves_forward_along_yaw() -> None:
    world = _open_world()
    world.robot = Pose2D(x=1.0, y=1.0, yaw=0.0)

    world.apply_twist(Twist2D(vx=0.5), dt=1.0)

    assert world.robot.x == pytest.approx(1.5, abs=1e-6)
    assert world.robot.y == pytest.approx(1.0, abs=1e-6)
    assert world.traveled_m == pytest.approx(0.5, abs=1e-6)


def test_twist_is_clamped_to_world_limits() -> None:
    world = _open_world()
    world.apply_twist(Twist2D(vx=99.0, vyaw=99.0), dt=0.1)

    assert world.last_command.vx == pytest.approx(world.max_linear_speed)
    assert world.last_command.vyaw == pytest.approx(world.max_angular_speed)


def test_out_of_bounds_is_not_free() -> None:
    world = _open_world()
    assert world.is_free(4.0, 3.0)
    assert not world.is_free(-0.1, 3.0)
    assert not world.is_free(8.5, 3.0)


def test_box_and_circle_obstacles_block_motion() -> None:
    world = _open_world()
    world.add_obstacle(BoxObstacle(name="wall", x=2.0, y=1.0, width=0.2, height=4.0))
    world.add_obstacle(CircleObstacle(name="pillar", x=5.0, y=4.0, radius=0.4))
    world.robot = Pose2D(x=1.0, y=1.0, yaw=0.0)

    assert not world.is_free(2.0, 1.0)
    assert not world.is_free(5.0, 4.0)
    assert world.is_free(1.5, 1.0)


def test_robot_stops_at_obstacle_without_tunneling() -> None:
    """얇은 벽을 통과하지 않고, 충돌이 계측되어야 한다."""
    world = _open_world()
    world.add_obstacle(BoxObstacle(name="thin_wall", x=2.0, y=1.0, width=0.1, height=4.0))
    world.robot = Pose2D(x=1.0, y=1.0, yaw=0.0)

    for _ in range(50):
        world.apply_twist(Twist2D(vx=0.5), dt=0.1)

    assert world.robot.x < 2.0 - 0.1, "벽을 통과했습니다"
    assert world.collisions > 0


def test_zero_or_negative_dt_is_ignored() -> None:
    world = _open_world()
    world.robot = Pose2D(x=1.0, y=1.0)
    world.apply_twist(Twist2D(vx=0.5), dt=0.0)
    world.apply_twist(Twist2D(vx=0.5), dt=-1.0)

    assert world.robot.x == pytest.approx(1.0)
    assert world.elapsed_sec == 0.0


def test_teleport_rejects_occupied_pose() -> None:
    world = _open_world()
    world.add_obstacle(BoxObstacle(name="wall", x=2.0, y=2.0, width=1.0, height=1.0))

    assert world.teleport(Pose2D(x=2.0, y=2.0)) is False
    assert world.teleport(Pose2D(x=1.0, y=1.0)) is True
    assert world.robot.x == pytest.approx(1.0)


def test_object_removal_supports_grasp_assertions() -> None:
    world = _open_world()
    world.add_object(SimObject(name="cup1", class_id="cup", x=2.0, y=2.0))

    assert world.object_by_name("cup1") is not None
    assert world.remove_object("cup1") is True
    assert world.object_by_name("cup1") is None
    assert world.remove_object("cup1") is False


# --- 제어기 ---


def test_controller_rotates_in_place_when_goal_behind() -> None:
    pose = Pose2D(x=1.0, y=1.0, yaw=0.0)
    goal = GoalPose(x=0.0, y=1.0)  # 뒤쪽

    twist = compute_twist(pose, goal)

    assert twist.vx == pytest.approx(0.0)
    assert twist.vyaw != 0.0


def test_controller_drives_forward_when_aligned() -> None:
    pose = Pose2D(x=1.0, y=1.0, yaw=0.0)
    goal = GoalPose(x=3.0, y=1.0)

    twist = compute_twist(pose, goal)

    assert twist.vx > 0.0


def test_controller_stops_inside_tolerance() -> None:
    pose = Pose2D(x=1.0, y=1.0, yaw=0.0)
    goal = GoalPose(x=1.1, y=1.0, tolerance=0.25)

    assert compute_twist(pose, goal) == Twist2D()
    assert is_goal_reached(pose, goal) is True


def test_goal_with_yaw_requires_alignment() -> None:
    pose = Pose2D(x=1.0, y=1.0, yaw=0.0)
    goal = GoalPose(x=1.0, y=1.0, yaw=math.pi, tolerance=0.25, yaw_tolerance=0.1)

    assert is_goal_reached(pose, goal) is False
    twist = compute_twist(pose, goal)
    assert twist.vx == pytest.approx(0.0)
    assert twist.vyaw != 0.0


def test_closed_loop_reaches_goal() -> None:
    """시뮬레이터 폐루프: cmd_vel 적분으로 실제로 목표에 도달해야 한다."""
    world = _open_world()
    world.robot = Pose2D(x=1.0, y=1.0, yaw=0.0)
    goal = GoalPose(x=4.0, y=3.0, tolerance=0.2)

    for _ in range(2000):
        twist = compute_twist(world.robot, goal)
        if twist == Twist2D():
            break
        world.apply_twist(twist, dt=0.05)

    assert is_goal_reached(world.robot, goal) is True
    assert world.collisions == 0


def test_spin_reaches_target_yaw() -> None:
    target = math.pi / 2
    yaw = 0.0
    for _ in range(200):
        twist = spin_twist(target, yaw, yaw_tolerance=0.05)
        if twist == Twist2D():
            break
        yaw = math.atan2(math.sin(yaw + twist.vyaw * 0.05), math.cos(yaw + twist.vyaw * 0.05))

    assert abs(math.atan2(math.sin(target - yaw), math.cos(target - yaw))) <= 0.06
