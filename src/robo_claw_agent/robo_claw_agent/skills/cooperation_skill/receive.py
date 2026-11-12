"""동료 협동 메시지 수신 — 전송 ID 보존과 저널 기록.

에이전트 노드의 ``PeerMessage`` 서비스 핸들러가 이 함수 하나를 호출한다. 순수
함수라 ROS2 없이 테스트할 수 있고, 서비스 어댑터는 필드 추출만 담당한다.

수신 규칙:
- 전송 계층이 준 message_id/task_id/session_id/reply_to 가 있으면 그대로 쓴다.
  없으면(구버전 로봇의 평문 메시지) 여기서 새로 만든다.
- ``message_received`` 이벤트와 태스크 ``received`` 상태를 남긴다. 이 시점은
  "인박스에 들어왔다"는 뜻이며 실행 완료/전송 완료와 구분된다.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .envelope import (
    MAX_ID_CHARS,
    MAX_RESULT_CHARS,
    MAX_STATUS_CHARS,
    TASK_STATUS_RECEIVED,
    is_result_report,
    new_id,
)
from .inbox import InboxMessage, PeerMessageInbox, get_peer_message_inbox
from .journal import (
    EVENT_MESSAGE_RECEIVED,
    EVENT_MESSAGE_REJECTED,
    CooperationJournal,
    get_cooperation_journal,
)

logger = logging.getLogger(__name__)

_ACCEPTED_RESULT = "메시지를 협동 인박스에 전달했습니다."
_FULL_ERROR = "협동 인박스가 가득 차 메시지를 수신하지 못했습니다."


@dataclass(frozen=True)
class PeerReceiveOutcome:
    """수신 처리 결과. 서비스 응답 필드로 그대로 옮긴다."""

    accepted: bool
    result_message: str = ""
    error_message: str = ""
    inbox_item: InboxMessage | None = None


def receive_peer_message(
    *,
    peer_name: str,
    message: str,
    message_id: str = "",
    task_id: str = "",
    session_id: str = "",
    reply_to: str = "",
    status: str = "",
    result: str = "",
    kind: str = "",
    inbox: PeerMessageInbox | None = None,
    journal: CooperationJournal | None = None,
) -> PeerReceiveOutcome:
    """동료 메시지를 인박스에 넣고 수신 이벤트를 기록한다."""
    peer = str(peer_name or "").strip()
    text = str(message or "")
    if not peer or not text.strip():
        return PeerReceiveOutcome(accepted=False, error_message="peer_name과 message가 필요합니다.")

    # Reject oversized ROS service metadata before retaining it in the inbox.
    metadata_limits = (
        ("peer_name", peer, 128),
        ("message_id", message_id, MAX_ID_CHARS),
        ("task_id", task_id, MAX_ID_CHARS),
        ("session_id", session_id, MAX_ID_CHARS),
        ("reply_to", reply_to, MAX_ID_CHARS),
        ("status", status, MAX_STATUS_CHARS),
        ("result", result, MAX_RESULT_CHARS),
        ("kind", kind, 16),
    )
    for field, value, limit in metadata_limits:
        if len(str(value or "")) > limit:
            return PeerReceiveOutcome(
                accepted=False, error_message=f"협동 메시지 {field} 길이가 제한을 초과합니다."
            )

    target_inbox = inbox if inbox is not None else get_peer_message_inbox()
    target_journal = journal if journal is not None else get_cooperation_journal()
    item = InboxMessage(
        peer_name=peer,
        message=text,
        message_id=str(message_id or "").strip() or new_id(),
        task_id=str(task_id or "").strip() or new_id(),
        session_id=str(session_id or "").strip(),
        reply_to=str(reply_to or "").strip(),
        status=str(status or "").strip(),
        result=str(result or "").strip(),
        kind=str(kind or "").strip(),
        received_at=time.time(),
    )

    if not target_inbox.push_message(item):
        target_journal.record(
            EVENT_MESSAGE_REJECTED,
            peer_id=peer,
            task_id=item.task_id,
            message_id=item.message_id,
            session_id=item.session_id,
            status="denied",
            content=text,
            details={"reason": "inbox_full"},
        )
        return PeerReceiveOutcome(accepted=False, error_message=_FULL_ERROR)

    result_report = is_result_report(kind=item.kind, status=item.status)
    details = {"result_report": result_report}
    if item.status:
        details["reported_status"] = item.status
    if item.kind:
        details["kind"] = item.kind
    target_journal.record(
        EVENT_MESSAGE_RECEIVED,
        peer_id=peer,
        task_id=item.task_id,
        message_id=item.message_id,
        reply_to=item.reply_to,
        session_id=item.session_id or None,
        status="" if result_report else TASK_STATUS_RECEIVED,
        content=text,
        details=details,
    )
    if not result_report:
        # 결과 보고는 수신 시점에 태스크를 만들지 않는다. 만들면 "우리가 보낸 적 없는
        # 태스크"가 생겨 결과 상관이 자기 자신과 맞아떨어질 수 있다.
        target_journal.upsert_task(
            item.task_id,
            peer_id=peer,
            session_id=item.session_id,
            description=text,
            status=TASK_STATUS_RECEIVED,
        )
    logger.info("[Coop] Received from %s (task=%s)", peer, item.task_id)
    return PeerReceiveOutcome(accepted=True, result_message=_ACCEPTED_RESULT, inbox_item=item)
