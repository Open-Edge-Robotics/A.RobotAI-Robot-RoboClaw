"""백엔드 선택/운동학 백엔드 계약 테스트(외부 의존성 없음)."""

from __future__ import annotations

import sys

import pytest
from robo_claw_sim.backends import (
    DEFAULT_BACKEND,
    MUJOCO_INSTALL_HINT,
    SUPPORTED_BACKENDS,
    Backend,
    KinematicBackend,
    build_backend,
)
from robo_claw_sim.raycast import ScanConfig, cast_scan
from robo_claw_sim.world import BoxObstacle, Pose2D, Twist2D, World

pytestmark = pytest.mark.unit


def _world() -> World:
    return World(
        width=8.0,
        height=6.0,
        robot=Pose2D(x=2.0, y=2.0),
        obstacles=[BoxObstacle(name="wall", x=4.0, y=3.0, width=0.4, height=2.0)],
    )


def test_default_backend_is_dependency_free() -> None:
    assert DEFAULT_BACKEND == "kinematic"
    assert SUPPORTED_BACKENDS == ("kinematic", "mujoco")


def test_build_backend_returns_kinematic_by_default() -> None:
    world = _world()
    backend = build_backend("", world=world)

    assert isinstance(backend, KinematicBackend)
    assert isinstance(backend, Backend)  # Protocol 준수
    assert backend.name == "kinematic"
    assert backend.world is world


def test_unknown_backend_is_rejected_with_supported_list() -> None:
    with pytest.raises(ValueError) as excinfo:
        build_backend("isaac", world=_world())

    message = str(excinfo.value)
    assert "isaac" in message
    for name in SUPPORTED_BACKENDS:
        assert name in message


def test_kinematic_backend_matches_world_physics() -> None:
    """kinematic 백엔드는 기존 World 동작과 완전히 같아야 한다(회귀 방지)."""
    reference = _world()
    backend = KinematicBackend(_world())

    for _ in range(10):
        backend.step(Twist2D(vx=0.3, vyaw=0.1), 0.05)
        reference.apply_twist(Twist2D(vx=0.3, vyaw=0.1), 0.05)

    assert backend.world.robot == reference.robot
    assert backend.world.traveled_m == pytest.approx(reference.traveled_m)
    assert backend.world.collisions == reference.collisions


def test_kinematic_backend_scan_matches_raycast() -> None:
    world = _world()
    backend = KinematicBackend(world, ScanConfig(num_beams=36))

    assert backend.scan() == cast_scan(world, ScanConfig(num_beams=36))


def test_kinematic_backend_teleport_rejects_occupied_pose() -> None:
    backend = KinematicBackend(_world())

    assert backend.teleport(Pose2D(x=1.0, y=1.0)) is True
    assert backend.world.robot.x == pytest.approx(1.0)
    assert backend.teleport(Pose2D(x=4.0, y=3.0)) is False


def test_missing_mujoco_package_fails_with_install_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    """mujoco 미설치 환경에서는 조용히 kinematic 으로 대체하지 않고 안내와 함께 실패한다."""
    monkeypatch.setitem(sys.modules, "robo_claw_sim.backends.mujoco_backend", None)

    with pytest.raises(RuntimeError) as excinfo:
        build_backend("mujoco", world=_world())

    assert "mujoco" in str(excinfo.value)
    assert MUJOCO_INSTALL_HINT in str(excinfo.value)
