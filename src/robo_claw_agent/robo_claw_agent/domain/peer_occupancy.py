"""동료 자세 공유로 목표 지점 점유를 판정하는 순수 규칙 (ROS 의존 없음).

같은 ROS 도메인의 로봇들이 ``/peer_poses`` 로 자기 자세를 공유하면, 이동을
시도하기 **전에** 목표 지점이 동료에게 막혀 있는지 알 수 있다. Nav2 실패를
기다렸다가 원인을 추측하는 대신 선제적으로 자리를 요청하기 위한 기반이다.

판정은 fail-closed 다. 자세가 오래됐거나 프레임이 다르면 점유로 보지 않는다
(없는 로봇을 피해 우회 주행하는 것이 더 위험하다).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

# 이 시간보다 오래된 자세는 쓰지 않는다(초). 자세 공유 주기는 1Hz.
DEFAULT_MAX_AGE_SEC = 5.0
# 점유 판정 기본 반경(m). 로봇 몸체 반경 + 여유.
DEFAULT_OCCUPANCY_RADIUS_M = 0.8
# 점유로 인정하는 좌표 프레임.
SUPPORTED_FRAME = "map"


@dataclass(frozen=True)
class PeerPoseSample:
    """동료 한 대의 자세 스냅샷."""

    agent_id: str
    x: float
    y: float
    yaw: float = 0.0
    observed_at_sec: float = 0.0
    frame_id: str = SUPPORTED_FRAME
    current_skill: str = ""
    yieldable: bool = True

    def is_finite(self) -> bool:
        return all(math.isfinite(v) for v in (self.x, self.y, self.yaw))

    def is_usable(self, *, now_sec: float, max_age_sec: float = DEFAULT_MAX_AGE_SEC) -> bool:
        """점유 판정에 쓸 수 있는 자세인지(프레임·유한값·신선도)."""
        if not self.is_finite():
            return False
        if str(self.frame_id or "") != SUPPORTED_FRAME:
            return False
        age = now_sec - float(self.observed_at_sec)
        return 0.0 <= age <= max_age_sec


def find_blocking_peers(
    samples: Iterable[PeerPoseSample],
    *,
    target_x: float,
    target_y: float,
    now_sec: float,
    radius_m: float = DEFAULT_OCCUPANCY_RADIUS_M,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
    exclude_agent_id: str = "",
) -> list[PeerPoseSample]:
    """목표 지점을 점유한 동료를 가까운 순서로 반환한다.

    오래됐거나 프레임이 다른 자세는 후보에서 제외한다. 반경 경계(정확히
    ``radius_m``)는 점유로 본다 — 자리 양보 판정과 같은 기준을 쓴다.
    """
    if not (math.isfinite(target_x) and math.isfinite(target_y)):
        return []
    radius = float(radius_m)
    if not math.isfinite(radius) or radius <= 0.0:
        return []
    excluded = str(exclude_agent_id or "").strip()

    occupants: list[tuple[float, PeerPoseSample]] = []
    for sample in samples:
        if excluded and sample.agent_id == excluded:
            continue
        if not sample.is_usable(now_sec=now_sec, max_age_sec=max_age_sec):
            continue
        distance = math.dist((sample.x, sample.y), (target_x, target_y))
        if distance > radius:
            continue
        occupants.append((distance, sample))

    occupants.sort(key=lambda item: (item[0], item[1].agent_id))
    return [sample for _, sample in occupants]


def describe_occupants(occupants: Iterable[PeerPoseSample], *, target_x: float, target_y: float) -> str:
    """사용자·로그용 요약. 점유자가 없으면 빈 문자열."""
    lines: list[str] = []
    for sample in occupants:
        distance = math.dist((sample.x, sample.y), (target_x, target_y))
        blocking = "조작 중" if not sample.yieldable else "양보 가능"
        lines.append(f"{sample.agent_id}({distance:.2f}m, {blocking})")
    return ", ".join(lines)
