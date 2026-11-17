"""시뮬레이션 백엔드 선택(팩토리).

``backend`` 파라미터 값으로 구현을 고른다. 기본값은 외부 의존성이 없는
``kinematic`` 이며, ``mujoco`` 는 선택 의존성(``sim-mujoco`` extra)이 설치된
경우에만 동작한다. 패키지가 없으면 조용히 대체하지 않고 설치 방법과 함께 실패한다.
"""

from __future__ import annotations

from ..raycast import ScanConfig
from ..world import World
from .base import Backend
from .kinematic import KinematicBackend
from .manipulation import ManipulationConfig

DEFAULT_BACKEND = "kinematic"
SUPPORTED_BACKENDS = ("kinematic", "mujoco")

MUJOCO_INSTALL_HINT = (
    "backend:=mujoco 를 쓰려면 mujoco 패키지가 필요합니다. "
    "개발 환경: `uv sync --extra sim-mujoco`, 이미지/호스트: `pip install mujoco`"
)


def build_backend(
    name: str,
    *,
    world: World,
    scan_config: ScanConfig | None = None,
    manipulation: ManipulationConfig | None = None,
) -> Backend:
    """``backend`` 이름으로 백엔드를 만든다."""
    key = (name or DEFAULT_BACKEND).strip().lower()
    if key == "kinematic":
        if manipulation is not None:
            raise ValueError(
                "kinematic 백엔드는 조작(팔·파지)을 지원하지 않습니다. "
                "조작 시나리오는 backend:=mujoco 를 사용하세요."
            )
        return KinematicBackend(world, scan_config)
    if key == "mujoco":
        try:
            from .mujoco_backend import MujocoBackend
        except ImportError as exc:  # mujoco 미설치
            raise RuntimeError(MUJOCO_INSTALL_HINT) from exc
        return MujocoBackend(world, scan_config, manipulation)
    raise ValueError(
        f"지원하지 않는 시뮬레이션 backend 입니다: {name!r} (가능: {', '.join(SUPPORTED_BACKENDS)})"
    )


__all__ = [
    "DEFAULT_BACKEND",
    "MUJOCO_INSTALL_HINT",
    "SUPPORTED_BACKENDS",
    "Backend",
    "KinematicBackend",
    "build_backend",
]
