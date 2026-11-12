"""자율협동 수신 메시지 인박스.

동료 로봇으로부터 들어온 메시지를 스레드 세이프한 큐에 보관하고,
자율협동 루프가 drain() 으로 꺼내 처리한다.

ROS2 의존성이 없어 단위 테스트가 가능하다.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)
_DEFAULT_MAX_SIZE = 256
_MAX_MESSAGE_LENGTH = 4096


@dataclass(frozen=True)
class InboxMessage:
    """수신 메시지 한 건 — 전송 계층에서 받은 상관 ID를 함께 보관한다."""

    peer_name: str
    message: str
    message_id: str = ""
    task_id: str = ""
    session_id: str = ""
    reply_to: str = ""
    status: str = ""
    result: str = ""
    kind: str = ""
    received_at: float = 0.0


class PeerMessageInbox:
    """동료 로봇이 보낸 메시지를 담는 스레드 세이프 인박스(단일 큐).

    구조화 API(``push_message``/``drain_messages``)가 ID를 보존하고, legacy
    tuple API(``push``/``drain``)는 같은 큐를 tuple 로 보여준다. 두 API 를 섞어도
    한 메시지는 한 번만 소비된다.
    """

    def __init__(self, maxsize: int = 0) -> None:
        self._queue: queue.Queue[InboxMessage] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._total_pushed = 0
        self._consumer: str | None = None

    def claim_consumer(self, consumer: str) -> bool:
        """인박스 소비권을 원자적으로 확보한다."""
        if not consumer:
            return False
        with self._lock:
            if self._consumer is None or self._consumer == consumer:
                self._consumer = consumer
                return True
            return False

    def release_consumer(self, consumer: str) -> None:
        """소비자가 종료될 때 소비권을 반납한다."""
        with self._lock:
            if self._consumer == consumer:
                self._consumer = None

    def push(self, peer_name: str, message: str) -> bool:
        """메시지를 인박스에 넣는다. 큐가 가득 차면 False 를 반환한다."""
        return self.push_message(
            InboxMessage(
                peer_name=str(peer_name or ""),
                message=str(message or ""),
                received_at=time.time(),
            )
        )

    def push_message(self, item: InboxMessage) -> bool:
        """상관 ID를 보존한 메시지를 인박스에 넣는다."""
        peer_name = str(item.peer_name or "")
        message = str(item.message or "")
        if not peer_name or not message:
            return False
        if len(message) > _MAX_MESSAGE_LENGTH:
            logger.warning("[Coop] Dropping oversized message from %s", peer_name)
            return False
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            logger.warning("[Coop] Inbox full — dropping message from %s", peer_name)
            return False
        with self._lock:
            self._total_pushed += 1
        return True

    def drain(
        self, limit: int | None = None, *, consumer: str | None = None
    ) -> list[tuple[str, str]]:
        """현재 쌓인 메시지를 ``(peer_name, message)`` tuple 로 꺼낸다(legacy view)."""
        return [
            (item.peer_name, item.message) for item in self.drain_messages(limit, consumer=consumer)
        ]

    def drain_messages(
        self, limit: int | None = None, *, consumer: str | None = None
    ) -> list[InboxMessage]:
        """현재 쌓인 메시지를 상관 ID와 함께 꺼낸다. (비어 있으면 빈 리스트)"""
        if consumer is not None:
            with self._lock:
                if self._consumer != consumer:
                    return []
        items: list[InboxMessage] = []
        while limit is None or len(items) < limit:
            try:
                items.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return items

    def size(self) -> int:
        """현재 대기 중인 메시지 수."""
        return self._queue.qsize()

    @property
    def total_pushed(self) -> int:
        with self._lock:
            return self._total_pushed


# 전역 싱글턴 — 에이전트 노드의 PeerMessage 서비스 핸들러와 자율협동 루프가
# 같은 인박스를 공유하기 위해 사용한다.
_global_inbox: PeerMessageInbox | None = None
_global_inbox_lock = threading.Lock()


def get_peer_message_inbox() -> PeerMessageInbox:
    """전역 공유 인박스 싱글턴을 반환한다."""
    global _global_inbox
    with _global_inbox_lock:
        if _global_inbox is None:
            _global_inbox = PeerMessageInbox(maxsize=_DEFAULT_MAX_SIZE)
        return _global_inbox


def reset_peer_message_inbox() -> None:
    """전역 인박스를 초기화한다 (테스트용)."""
    global _global_inbox
    with _global_inbox_lock:
        _global_inbox = None
