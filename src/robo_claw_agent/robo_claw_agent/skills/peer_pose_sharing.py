"""동료 자세 공유 — ``/peer_poses`` 발행·수신과 레지스트리.

같은 ROS 도메인의 모든 RoboClaw 에이전트가 자기 자세를 ``/peer_poses`` 로
1Hz 발행하고 서로 구독한다. 이 정보로 이동 **전에** 목표 지점 점유를 판정한다.

레지스트리는 ROS 타입에 의존하지 않는 dict 기반이라 fake 메시지로 테스트한다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from robo_claw_agent.domain.peer_occupancy import (
    DEFAULT_MAX_AGE_SEC,
    DEFAULT_OCCUPANCY_RADIUS_M,
    PeerPoseSample,
    find_blocking_peers,
)

logger = logging.getLogger(__name__)

PEER_POSE_TOPIC = "/peer_poses"
PEER_POSE_PUBLISH_INTERVAL_SEC = 1.0
# 오래된 항목이 무한히 쌓이지 않도록 상한을 둔다.
REGISTRY_MAX_ENTRIES = 32


class PeerPoseRegistry:
    """동료별 최신 자세를 보관하는 스레드 세이프 레지스트리."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples: dict[str, PeerPoseSample] = {}
        self._dropped = 0

    def update(self, sample: PeerPoseSample) -> None:
        """자세를 갱신한다. 오래된 항목은 같은 agent_id 로 덮어써 정리된다."""
        if not sample.agent_id:
            return
        with self._lock:
            self._samples[sample.agent_id] = sample
            if len(self._samples) > REGISTRY_MAX_ENTRIES:
                oldest = min(self._samples.items(), key=lambda kv: kv[1].observed_at_sec)
                self._samples.pop(oldest[0], None)
                self._dropped += 1

    def snapshot(self) -> list[PeerPoseSample]:
        with self._lock:
            return list(self._samples.values())

    def blocking_peers(
        self,
        *,
        target_x: float,
        target_y: float,
        now_sec: float | None = None,
        radius_m: float = DEFAULT_OCCUPANCY_RADIUS_M,
        max_age_sec: float = DEFAULT_MAX_AGE_SEC,
        exclude_agent_id: str = "",
    ) -> list[PeerPoseSample]:
        """목표 지점을 점유한 동료 목록(가까운 순)."""
        now = time.monotonic() if now_sec is None else now_sec
        return find_blocking_peers(
            self.snapshot(),
            target_x=target_x,
            target_y=target_y,
            now_sec=now,
            radius_m=radius_m,
            max_age_sec=max_age_sec,
            exclude_agent_id=exclude_agent_id,
        )

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()

    @property
    def dropped_count(self) -> int:
        with self._lock:
            return self._dropped


# --- ROS 어댑터 --------------------------------------------------------------


def sample_from_message(message: Any, *, now_sec: float | None = None) -> PeerPoseSample | None:
    """``PeerPose`` 메시지를 샘플로 변환한다. 해석 실패 시 None."""
    now = time.monotonic() if now_sec is None else now_sec
    try:
        agent_id = str(getattr(message, "agent_id", "") or "").strip()
        if not agent_id:
            return None
        return PeerPoseSample(
            agent_id=agent_id,
            x=float(message.x),
            y=float(message.y),
            yaw=float(getattr(message, "yaw", 0.0) or 0.0),
            observed_at_sec=now,
            frame_id=str(getattr(message, "frame_id", "") or ""),
            current_skill=str(getattr(message, "current_skill", "") or ""),
            yieldable=bool(getattr(message, "yieldable", True)),
        )
    except (TypeError, ValueError) as exc:
        logger.debug("[PeerPose] 자세 메시지 해석 실패: %s", exc)
        return None


def handle_peer_pose_message(registry: PeerPoseRegistry, message: Any) -> bool:
    """수신 콜백 — 레지스트리에 반영하고 성공 여부를 돌려준다."""
    sample = sample_from_message(message)
    if sample is None:
        return False
    registry.update(sample)
    return True


def build_peer_pose_message(
    *, agent_id: str, pose: Any, frame_id: str, current_skill: str, yieldable: bool
) -> Any:
    """발행용 ``PeerPose`` 메시지를 만든다 (ROS 타입은 호출 시 import)."""
    from robo_claw_msgs.msg import PeerPose  # type: ignore[import]

    message = PeerPose()
    message.agent_id = agent_id
    message.x = float(pose.x)
    message.y = float(pose.y)
    message.yaw = float(pose.yaw)
    message.frame_id = frame_id
    message.current_skill = current_skill
    message.yieldable = bool(yieldable)
    return message


def publish_own_pose(
    node: Any,
    publisher: Any,
    registry: PeerPoseRegistry,
    *,
    pose_provider: Callable[[], Any | None],
    yieldable_provider: Callable[[], bool],
) -> bool:
    """자기 자세를 발행한다. 위치를 모르면 발행하지 않는다(추측 금지)."""
    pose = pose_provider()
    if pose is None or not pose.is_finite():
        return False
    try:
        message = build_peer_pose_message(
            agent_id=str(getattr(node, "_agent_id", "")),
            pose=pose,
            frame_id=str(getattr(node, "_map_frame", "map") or "map"),
            current_skill=str(getattr(node, "_current_skill", "") or ""),
            yieldable=bool(yieldable_provider()),
        )
        message.header.stamp = node.get_clock().now().to_msg()
        publisher.publish(message)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("[PeerPose] 자세 발행 실패: %s", exc)
        return False


def blocking_peer_names(
    registry: PeerPoseRegistry, *, target_x: float, target_y: float, agent_id: str
) -> list[str]:
    """편의 함수 — 점유 동료 이름만 필요할 때."""
    return [
        s.agent_id
        for s in registry.blocking_peers(
            target_x=target_x, target_y=target_y, exclude_agent_id=agent_id
        )
    ]


def registry_of(node: Any) -> PeerPoseRegistry | None:
    """노드에 연결된 레지스트리. 자세 공유가 꺼진 노드는 None."""
    registry = getattr(node, "_peer_pose_registry", None)
    return registry if isinstance(registry, PeerPoseRegistry) else None


def node_blocking_peers(node: Any, *, target_x: float, target_y: float) -> list[PeerPoseSample]:
    """노드 기준 목표 지점 점유 동료 목록. 자세 공유가 꺼져 있으면 빈 리스트."""
    registry = registry_of(node)
    if registry is None:
        return []
    return registry.blocking_peers(
        target_x=target_x,
        target_y=target_y,
        exclude_agent_id=str(getattr(node, "_agent_id", "")),
    )


def merge_samples(registry: PeerPoseRegistry, samples: Iterable[PeerPoseSample]) -> None:
    """여러 샘플을 한 번에 반영한다(테스트·재수화 경로용)."""
    for sample in samples:
        registry.update(sample)
