"""시뮬레이션 백엔드 인터페이스(순수 파이썬).

시뮬레이터 노드는 "명령을 세계 상태로 반영"과 "센서 값을 합성"만 백엔드에 위임한다.
판정·시나리오·ROS 메시지 변환은 백엔드와 무관하게 유지한다.

- ``kinematic``: 현행 2D 운동학 세계(기본값, 외부 의존성 없음)
- ``mujoco``: MuJoCo 물리 백엔드(선택 의존성, 접촉/카메라 확장 기반)
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..raycast import ScanConfig
from ..world import Pose2D, Twist2D, World


@runtime_checkable
class Backend(Protocol):
    """시뮬레이터 백엔드 계약.

    구현체는 ``World`` 를 단일 상태 저장소로 사용한다. ``step`` 은 명령을 세계에
    반영하고, ``scan`` 은 현재 상태에서 LaserScan ranges 를 만든다.
    """

    name: str
    world: World
    scan_config: ScanConfig

    def step(self, twist: Twist2D, dt: float) -> None:
        """속도 명령을 dt 동안 세계에 반영한다."""

    def scan(self, config: ScanConfig | None = None) -> list[float]:
        """전 방위 LaserScan ranges(m). 무한대는 ``inf``."""

    def teleport(self, pose: Pose2D) -> bool:
        """순간 이동. 점유 위치면 거부하고 False."""

    def close(self) -> None:
        """자원 해제(프로세스 종료 시 정리)."""
