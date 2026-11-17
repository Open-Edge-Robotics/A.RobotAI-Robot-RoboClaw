"""MuJoCo 헤드 카메라 RGB-D 렌더 테스트.

오프스크린 렌더링은 GL 백엔드(EGL/OSMesa)가 필요하다. 없는 환경에서는 skip 하고,
``render_camera`` 계약(형상/타입/깊이 유효성)만 고정한다.
"""

from __future__ import annotations

import numpy as np
import pytest
from robo_claw_sim.backends import build_backend
from robo_claw_sim.raycast import ScanConfig
from robo_claw_sim.world import BoxObstacle, Pose2D, World

pytestmark = [pytest.mark.unit, pytest.mark.mujoco]

pytest.importorskip("mujoco", reason="mujoco 미설치(sim-mujoco extra)")

pytestmark.append(pytest.mark.slow)


def _backend() -> object:
    world = World(
        width=6.0,
        height=4.0,
        robot=Pose2D(x=1.0, y=1.0),
        obstacles=[BoxObstacle(name="wall", x=3.0, y=1.0, width=0.2, height=2.0)],
    )
    return build_backend("mujoco", world=world, scan_config=ScanConfig(num_beams=9))


def _render(backend):  # noqa: ANN001, ANN202
    try:
        return backend.render_camera(width=64, height=48)
    except RuntimeError as exc:  # GL 백엔드 없음
        pytest.skip(str(exc))


def test_render_camera_returns_rgb_and_depth() -> None:
    backend = _backend()
    rgb, depth = _render(backend)

    assert rgb.shape == (48, 64, 3)
    assert rgb.dtype == np.uint8
    assert depth.shape == (48, 64)
    assert depth.dtype == np.float32


def test_render_shows_scene_geometry_and_valid_depth() -> None:
    backend = _backend()
    rgb, depth = _render(backend)

    # 완전 단색이면 장면이 그려지지 않은 것이다.
    assert rgb.reshape(-1, 3).std(axis=0).max() > 0.0
    # 바닥/벽이 보이므로 유효한 깊이 값이 존재한다.
    finite = depth[np.isfinite(depth)]
    assert finite.size > 0
    assert finite.min() > 0.0


def test_render_camera_reuses_renderer() -> None:
    backend = _backend()
    first_rgb, _ = _render(backend)
    second_rgb, _ = _render(backend)

    assert first_rgb.shape == second_rgb.shape


def test_kinematic_backend_has_no_camera() -> None:
    from robo_claw_sim.backends import build_backend as factory

    backend = factory("kinematic", world=World())

    assert not hasattr(backend, "render_camera")
