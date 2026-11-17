"""MuJoCo 백엔드 계약 테스트.

``mujoco`` 패키지가 없으면 skip 한다(선택 의존성). CI 기본 게이트는 kinematic
백엔드로 돌고, 이 테스트는 ``uv sync --extra sim-mujoco`` 환경에서 실행한다.
"""

from __future__ import annotations

import math

import pytest
from robo_claw_sim.backends import build_backend
from robo_claw_sim.raycast import ScanConfig
from robo_claw_sim.world import BoxObstacle, Pose2D, Twist2D, World

pytestmark = [pytest.mark.unit, pytest.mark.mujoco]

pytest.importorskip("mujoco", reason="mujoco 미설치(sim-mujoco extra)")


def _open_world(**kwargs) -> World:
    params = {"width": 8.0, "height": 6.0, "robot": Pose2D(x=2.0, y=3.0)}
    params.update(kwargs)
    return World(**params)


def _backend(world: World, beams: int = 9):
    return build_backend("mujoco", world=world, scan_config=ScanConfig(num_beams=beams))


def _drive(backend, twist: Twist2D, seconds: float, dt: float = 0.05) -> None:  # noqa: ANN001
    for _ in range(int(round(seconds / dt))):
        backend.step(twist, dt)


def test_backend_reports_name() -> None:
    backend = _backend(_open_world())
    assert backend.name == "mujoco"


def test_forward_command_moves_robot() -> None:
    backend = _backend(_open_world())
    _drive(backend, Twist2D(vx=0.4), seconds=1.0)

    assert backend.world.robot.x == pytest.approx(2.4, abs=0.03)
    assert backend.world.robot.y == pytest.approx(3.0, abs=0.01)
    assert backend.world.traveled_m == pytest.approx(0.4, abs=0.03)
    assert backend.world.collisions == 0


def test_angular_command_rotates_yaw() -> None:
    backend = _backend(_open_world())
    _drive(backend, Twist2D(vyaw=0.5), seconds=1.0)

    assert backend.world.robot.yaw == pytest.approx(0.5, abs=0.05)
    assert backend.world.robot.x == pytest.approx(2.0, abs=0.01)


def test_speed_is_clamped_to_world_limits() -> None:
    world = _open_world()
    backend = _backend(world)
    _drive(backend, Twist2D(vx=5.0), seconds=1.0)

    # max_linear_speed(0.5) 이상 이동하면 안 된다.
    assert backend.world.robot.x <= 2.0 + 0.5 + 0.03
    assert backend.world.last_command.vx == pytest.approx(0.5)


def test_wall_contact_blocks_motion_and_counts_collision() -> None:
    world = _open_world(obstacles=[BoxObstacle(name="block", x=2.6, y=3.0, width=0.2, height=2.0)])
    backend = _backend(world)

    _drive(backend, Twist2D(vx=0.4), seconds=2.0)

    # 장애물 표면(x=2.5)에서 로봇 반지름만큼 떨어진 곳에서 멈춘다.
    assert backend.world.robot.x < 2.5
    assert backend.world.robot.x == pytest.approx(2.28, abs=0.06)
    assert backend.world.collisions >= 1


def test_scan_hits_walls_at_expected_distance() -> None:
    backend = _backend(_open_world(), beams=9)
    ranges = backend.scan()

    # 9 beams: index 4 = 정면(+x), index 0 = 후방(-x)
    assert ranges[4] == pytest.approx(6.0, abs=0.1)  # 동쪽 벽까지 8.0 - 2.0
    assert ranges[0] == pytest.approx(2.0, abs=0.1)  # 서쪽 벽까지 2.0 - 0.0
    # 측면(y=3.0, 월드 높이 6.0)도 벽에 부딪힌다.
    assert ranges[2] == pytest.approx(3.0, abs=0.1)


def test_scan_returns_inf_beyond_range_max() -> None:
    world = _open_world(width=40.0, height=40.0, robot=Pose2D(x=20.0, y=20.0))
    backend = _backend(world, beams=9)

    assert all(math.isinf(value) for value in backend.scan())


def test_scan_ignores_robot_body() -> None:
    """자기 몸체(반지름 0.22)를 장애물로 보고 최소 거리로 잘리면 안 된다."""
    world = _open_world(width=40.0, height=40.0, robot=Pose2D(x=20.0, y=20.0))
    backend = _backend(world, beams=9)

    assert min(backend.scan()) > world.robot_radius


def test_scan_respects_range_min() -> None:
    world = _open_world(obstacles=[BoxObstacle(name="near", x=2.25, y=3.0, width=0.1, height=1.0)])
    backend = _backend(world, beams=9)

    assert backend.scan()[4] >= 0.12


def test_motion_is_deterministic() -> None:
    first = _backend(_open_world())
    second = _backend(_open_world())

    _drive(first, Twist2D(vx=0.35, vyaw=0.2), seconds=1.5)
    _drive(second, Twist2D(vx=0.35, vyaw=0.2), seconds=1.5)

    assert first.world.robot.x == pytest.approx(second.world.robot.x, abs=1e-9)
    assert first.world.robot.y == pytest.approx(second.world.robot.y, abs=1e-9)
    assert first.world.robot.yaw == pytest.approx(second.world.robot.yaw, abs=1e-9)


def test_teleport_updates_world_and_scene() -> None:
    backend = _backend(_open_world())

    assert backend.teleport(Pose2D(x=5.0, y=4.0, yaw=1.0)) is True
    assert backend.world.robot == Pose2D(x=5.0, y=4.0, yaw=1.0)

    _drive(backend, Twist2D(), seconds=0.2)
    assert backend.world.robot.x == pytest.approx(5.0, abs=1e-6)
    assert backend.world.robot.y == pytest.approx(4.0, abs=1e-6)
    assert backend.world.robot.yaw == pytest.approx(1.0, abs=1e-6)


def test_teleport_rejects_occupied_pose() -> None:
    world = _open_world(obstacles=[BoxObstacle(name="block", x=4.0, y=3.0, width=1.0, height=1.0)])
    backend = _backend(world)

    assert backend.teleport(Pose2D(x=4.0, y=3.0)) is False
