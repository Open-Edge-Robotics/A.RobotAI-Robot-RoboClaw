"""운동학(kinematic) 백엔드 - 기본값.

현행 ``World.apply_twist`` 와 ``cast_scan`` 을 그대로 사용한다. 외부 의존성이 없어
CI 게이트(``task sim-test``)가 이 백엔드로 돈다.
"""

from __future__ import annotations

from ..raycast import ScanConfig, cast_scan
from ..world import Pose2D, Twist2D, World


class KinematicBackend:
    """그리드 운동학 + ray marching 센서."""

    name = "kinematic"

    def __init__(self, world: World, scan_config: ScanConfig | None = None) -> None:
        self.world = world
        self.scan_config = scan_config or ScanConfig()

    def step(self, twist: Twist2D, dt: float) -> None:
        self.world.apply_twist(twist, dt)

    def scan(self, config: ScanConfig | None = None) -> list[float]:
        return cast_scan(self.world, config or self.scan_config)

    def teleport(self, pose: Pose2D) -> bool:
        return self.world.teleport(pose)

    def close(self) -> None:
        """정리할 자원이 없다."""
