"""명령 중재기 테스트(ROS 비의존)."""

from __future__ import annotations

import math

import pytest
from robo_claw_sim.motion import IDLE, MotionArbiter
from robo_claw_sim.nav import ControllerConfig, GoalPose
from robo_claw_sim.world import Pose2D, Twist2D, World

pytestmark = pytest.mark.unit


def _arbiter() -> MotionArbiter:
    world = World(width=10.0, height=10.0, robot_radius=0.2)
    world.robot = Pose2D(x=1.0, y=1.0, yaw=0.0)
    return MotionArbiter(world=world)


def test_external_command_is_used_until_timeout() -> None:
    arbiter = _arbiter()
    arbiter.set_external(Twist2D(vx=0.3), now=1.0)

    assert arbiter.command(1.2) == Twist2D(vx=0.3)
    assert arbiter.command(1.6) == Twist2D()


def test_pose_goal_overrides_external_command() -> None:
    arbiter = _arbiter()
    arbiter.set_external(Twist2D(vx=-0.3), now=1.0)
    arbiter.start_goal(GoalPose(x=4.0, y=1.0), now=1.0)

    command = arbiter.command(1.1)

    assert command.vx > 0.0


def test_pose_goal_reaches_and_clears() -> None:
    arbiter = _arbiter()
    arbiter.start_goal(GoalPose(x=3.0, y=1.0, tolerance=0.2), now=0.0)

    now = 0.0
    for _ in range(4000):
        now += 0.05
        twist = arbiter.command(now)
        if twist == Twist2D() and arbiter.pose_goal_reached():
            break
        arbiter.world.apply_twist(twist, dt=0.05)

    assert arbiter.pose_goal_reached() is True
    arbiter.clear()
    assert arbiter.mode == IDLE


def test_spin_goal_completes() -> None:
    arbiter = _arbiter()
    arbiter.start_spin(math.pi / 2, now=0.0)

    now = 0.0
    for _ in range(2000):
        now += 0.05
        twist = arbiter.command(now)
        if arbiter.spin_done():
            break
        arbiter.world.apply_twist(twist, dt=0.05)

    assert arbiter.spin_done() is True


def test_controller_config_limits_are_applied_to_goal_commands() -> None:
    arbiter = _arbiter()
    arbiter.config = ControllerConfig(max_linear=0.1, max_angular=0.2)
    arbiter.start_goal(GoalPose(x=9.0, y=1.0), now=0.0)

    command = arbiter.command(0.1)

    assert abs(command.vx) <= 0.1
    assert abs(command.vyaw) <= 0.2
