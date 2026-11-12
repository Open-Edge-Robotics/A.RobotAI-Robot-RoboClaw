"""동료 로봇으로의 협동 메시지 전송 — envelope 생성과 전송 결과 기록.

자율협동 스킬이 만드는 모든 협동 메시지는 이 함수를 지난다. message_id/task_id/
session_id/reply_to 를 envelope 에 실어 보내고, 전송 대기(queued)·전송 실패를
저널에 남긴다. 수신측이 "받았다"고 보기 전까지 queued 는 완료가 아니다.

ROS2 의존은 lazy import(CallPeerRobot, ros_future)로 감싸 단위 테스트가 가능하다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from robo_claw_agent import ros_future

from .envelope import (
    KIND_REPLY,
    KIND_REQUEST,
    TASK_STATUS_FAILED,
    TASK_STATUS_QUEUED,
    PeerEnvelope,
    build_outbound_envelope,
    new_id,
)
from .globals import is_cooperation_active
from .journal import (
    EVENT_MESSAGE_QUEUED,
    EVENT_MESSAGE_SEND_FAILED,
    EVENT_REPLY_QUEUED,
    CooperationJournal,
    get_cooperation_journal,
)

logger = logging.getLogger(__name__)


def send_cooperation_message(
    *,
    node: Any,
    peer_name: str,
    message: str,
    task_id: str = "",
    reply_to: str = "",
    session_id: str = "",
    status: str = "",
    result: str = "",
    kind: str = "",
    journal: CooperationJournal | None = None,
) -> dict[str, Any]:
    """동료 로봇에게 협동 메시지를 보내고 전송 결과를 돌려준다."""
    if not is_cooperation_active():
        return {"success": False, "message": "자율협동이 중단되었습니다."}
    if node is None or not hasattr(node, "_call_peer_client"):
        return {"success": False, "message": "피어 호출 클라이언트가 설정되지 않았습니다."}

    target_journal = journal if journal is not None else get_cooperation_journal()
    envelope = build_outbound_envelope(
        content=message,
        message_id=new_id(),
        task_id=str(task_id or "").strip() or new_id(),
        session_id=str(session_id or "").strip() or target_journal.session_id,
        reply_to=reply_to,
        kind=kind or (KIND_REPLY if reply_to else KIND_REQUEST),
        status=status,
        result=result,
    )
    is_reply = bool(envelope.reply_to)
    target_journal.record(
        EVENT_REPLY_QUEUED if is_reply else EVENT_MESSAGE_QUEUED,
        peer_id=peer_name,
        task_id=envelope.task_id,
        message_id=envelope.message_id,
        reply_to=envelope.reply_to,
        session_id=envelope.session_id,
        status=envelope.status or ("" if is_reply else TASK_STATUS_QUEUED),
        content=message,
    )
    if not is_reply:
        target_journal.upsert_task(
            envelope.task_id,
            peer_id=peer_name,
            session_id=envelope.session_id,
            description=message,
            status=TASK_STATUS_QUEUED,
        )
    # 이 message_id 로 온 회신/결과만 상관된 것으로 인정한다.
    target_journal.note_outbound_message(envelope.task_id, envelope.message_id)

    from robo_claw_msgs.srv import CallPeerRobot  # type: ignore[import]

    request = CallPeerRobot.Request()
    request.peer_name = peer_name
    request.instruction = json.dumps(envelope.to_payload(), ensure_ascii=False)
    request.file_path = ""
    request.wait_for_result = False  # 대화는 비동기로 전달
    request.timeout_sec = 0.0

    ok, response = ros_future.call_service(
        node._call_peer_client,
        request,
        call_timeout_sec=ros_future.local_wait_sec(0.0),
        not_found_message="CallPeerRobot 서비스를 찾을 수 없습니다.",
        timeout_message="동료 로봇 호출 시간 초과",
    )
    if not ok:
        logger.warning("[Coop] Failed to send to %s: %s", peer_name, response)
        _record_send_failure(target_journal, envelope, peer_name, str(response))
        return {"success": False, "message": str(response)}

    success = bool(response.success)
    if not success:
        _record_send_failure(
            target_journal,
            envelope,
            peer_name,
            str(response.error_message or response.result_message or "전송 실패"),
        )
    return {
        "success": success,
        "message": response.result_message or response.error_message or "응답 없음",
    }


def _record_send_failure(
    journal: CooperationJournal, envelope: PeerEnvelope, peer_name: str, error: str
) -> None:
    """전송 실패를 기록한다. 새 작업이면 태스크를 실패로 확정한다."""
    journal.record(
        EVENT_MESSAGE_SEND_FAILED,
        peer_id=peer_name,
        task_id=envelope.task_id,
        message_id=envelope.message_id,
        reply_to=envelope.reply_to,
        session_id=envelope.session_id,
        status=TASK_STATUS_FAILED,
        details={"error": error},
    )
    if not envelope.reply_to:
        journal.update_task(envelope.task_id, status=TASK_STATUS_FAILED, result=error)
