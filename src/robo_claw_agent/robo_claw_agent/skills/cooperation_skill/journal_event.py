"""협동 관측 이벤트의 표현과 서버 로그 출력.

저널이 만든 이벤트 한 건의 모양(관측 API 계약)과 그 이벤트를 sanitized JSON 한
줄로 로그에 남기는 책임만 갖는다. 로그 handler/formatter 가 깨져도 협동 실행
경로를 막지 않는다.

ROS2/네트워크 의존이 없다.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: 서버 로그에서 이벤트 라인을 찾는 표식.
LOG_PREFIX = "[CooperationEvent]"


@dataclass(frozen=True)
class CooperationEvent:
    """관측 이벤트 한 건. ``session_id`` 는 이 이벤트가 속한 세션(보낸 로봇의 세션 포함)."""

    sequence: int
    event_type: str
    timestamp: float
    robot_id: str
    peer_id: str = ""
    session_id: str = ""
    task_id: str = ""
    message_id: str = ""
    reply_to: str = ""
    status: str = ""
    content: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """관측 API/저장소로 내보낼 사본(중첩 details 포함)."""
        return {
            "sequence": self.sequence,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "robot_id": self.robot_id,
            "peer_id": self.peer_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "message_id": self.message_id,
            "reply_to": self.reply_to,
            "status": self.status,
            "content": self.content,
            "details": copy.deepcopy(self.details),
        }


def log_event(event: CooperationEvent) -> None:
    """이벤트 한 건을 sanitized JSON 한 줄로 남긴다.

    로그 handler/formatter 가 예외를 던져도 협동 실행 경로를 막지 않는다.
    """
    try:
        payload = json.dumps(event.as_dict(), ensure_ascii=False, default=str)
        logger.info("%s %s", LOG_PREFIX, payload)
    except Exception:  # noqa: BLE001 - a broken logging sink must not affect execution
        pass
