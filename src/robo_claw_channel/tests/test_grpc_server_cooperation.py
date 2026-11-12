"""협동 메시지 상관 ID / 중복 차단 gRPC 통합 테스트.

생성 protobuf 가 필요한 테스트는 scratch protobuf 를 PYTHONPATH 로 주입해 실행한다::

    ROBOCLAW_TEST_PROTO_PATH=/tmp/roboclaw-test-protos PYTHONPATH=/tmp:src/robo_claw_channel \\
        pytest -p roboclaw_proto_test_plugin src/robo_claw_channel/tests/test_grpc_server_cooperation.py

검증 범위:
- 협동 콜백이 상관 정보를 받고, legacy 2-인자 콜백도 계속 동작한다.
- 같은 task 의 서로 다른 message_id 결과 메시지가 중복으로 차단되지 않는다.
- 같은 요청/결과의 재전송은 계속 차단된다(legacy task 차단 약화 없음).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from robo_claw_channel.grpc_server import MessengerServicer
from robo_claw_channel.peer_envelope import (
    KIND_REPLY,
    KIND_REQUEST,
    KIND_RESULT,
    PeerCorrelation,
)

from robo_claw_channel import messenger_pb2 as pb2


def _coop_message(
    *,
    message_id: str,
    task_id: str,
    kind: str = "",
    session_id: str = "",
    reply_to: str = "",
    status: str = "",
    result: str = "",
    content: str = "거실로 이동해줘",
    sender_id: str = "robot-a",
) -> pb2.ChatMessage:
    metadata = {
        "type": "peer_cooperation",
        "message_id": message_id,
        "task_id": task_id,
        "created_at": str(time.time()),
        "expires_at": str(time.time() + 30),
    }
    if kind:
        metadata["kind"] = kind
    if session_id:
        metadata["session_id"] = session_id
    if reply_to:
        metadata["reply_to"] = reply_to
    if status:
        metadata["status"] = status
    if result:
        metadata["result"] = result
    return pb2.ChatMessage(sender_id=sender_id, content=content, metadata=metadata)


def _send(servicer: MessengerServicer, message: pb2.ChatMessage):
    return servicer.SendCommand(message, None)


def test_cooperation_callback_receives_correlation(tmp_path: Path):
    received: list[tuple[str, str, PeerCorrelation]] = []

    def on_peer_message(sender_id: str, content: str, correlation: PeerCorrelation):
        received.append((sender_id, content, correlation))
        return True, "ok", ""

    servicer = MessengerServicer(
        lambda text: text, on_peer_message, dedup_path=str(tmp_path / "dedup.sqlite3")
    )
    try:
        response = _send(
            servicer,
            _coop_message(
                message_id="msg-1",
                task_id="task-1",
                kind=KIND_REQUEST,
                session_id="session-1",
            ),
        )
    finally:
        servicer._dedup_db.close()

    assert response.success is True
    sender_id, content, correlation = received[0]
    assert sender_id == "robot-a"
    assert content == "거실로 이동해줘"
    assert correlation.message_id == "msg-1"
    assert correlation.task_id == "task-1"
    assert correlation.session_id == "session-1"
    assert correlation.kind == KIND_REQUEST


def test_legacy_two_argument_cooperation_callback_still_routes(tmp_path: Path):
    received: list[tuple[str, str]] = []

    def on_peer_message(sender_id: str, content: str):
        received.append((sender_id, content))
        return True, "ok", ""

    servicer = MessengerServicer(
        lambda text: text, on_peer_message, dedup_path=str(tmp_path / "dedup.sqlite3")
    )
    try:
        response = _send(servicer, _coop_message(message_id="msg-1", task_id="task-1"))
    finally:
        servicer._dedup_db.close()

    assert response.success is True
    assert received == [("robot-a", "거실로 이동해줘")]


def test_same_task_with_distinct_message_ids_is_not_deduped_for_results(tmp_path: Path):
    """한 작업의 서로 다른 결과 메시지가 중복으로 차단되면 안 된다."""
    received: list[PeerCorrelation] = []

    def on_peer_message(sender_id: str, content: str, correlation: PeerCorrelation):
        received.append(correlation)
        return True, "ok", ""

    servicer = MessengerServicer(
        lambda text: text, on_peer_message, dedup_path=str(tmp_path / "dedup.sqlite3")
    )
    try:
        first = _send(
            servicer,
            _coop_message(
                message_id="msg-result-1",
                task_id="task-1",
                kind=KIND_RESULT,
                status="running",
                result="1단계 완료",
            ),
        )
        second = _send(
            servicer,
            _coop_message(
                message_id="msg-result-2",
                task_id="task-1",
                kind=KIND_RESULT,
                status="completed",
                result="전체 완료",
            ),
        )
        reply = _send(
            servicer,
            _coop_message(
                message_id="msg-reply-1",
                task_id="task-1",
                kind=KIND_REPLY,
                reply_to="msg-1",
            ),
        )
    finally:
        servicer._dedup_db.close()

    assert first.success is True
    assert second.success is True
    assert reply.success is True
    assert [correlation.message_id for correlation in received] == [
        "msg-result-1",
        "msg-result-2",
        "msg-reply-1",
    ]


def test_resent_result_message_with_same_message_id_is_still_deduped(tmp_path: Path):
    received: list[str] = []

    def on_peer_message(sender_id: str, content: str, correlation: PeerCorrelation):
        received.append(correlation.message_id)
        return True, "ok", ""

    servicer = MessengerServicer(
        lambda text: text, on_peer_message, dedup_path=str(tmp_path / "dedup.sqlite3")
    )
    try:
        first = _send(
            servicer,
            _coop_message(
                message_id="msg-result-1", task_id="task-1", kind=KIND_RESULT, status="completed"
            ),
        )
        resent = _send(
            servicer,
            _coop_message(
                message_id="msg-result-1", task_id="task-1", kind=KIND_RESULT, status="completed"
            ),
        )
    finally:
        servicer._dedup_db.close()

    assert first.success is True
    assert resent.success is True
    assert "중복" in resent.message
    assert received == ["msg-result-1"]


def test_duplicate_request_task_is_still_blocked_with_new_message_id(tmp_path: Path):
    """같은 task 의 새 message_id 요청은 중복 작업 실행으로 차단한다."""
    received: list[str] = []

    def on_peer_message(sender_id: str, content: str, correlation: PeerCorrelation):
        received.append(correlation.message_id)
        return True, "ok", ""

    servicer = MessengerServicer(
        lambda text: text, on_peer_message, dedup_path=str(tmp_path / "dedup.sqlite3")
    )
    try:
        first = _send(
            servicer,
            _coop_message(message_id="msg-1", task_id="task-1", kind=KIND_REQUEST),
        )
        duplicate = _send(
            servicer,
            _coop_message(message_id="msg-2", task_id="task-1", kind=KIND_REQUEST),
        )
    finally:
        servicer._dedup_db.close()

    assert first.success is True
    assert duplicate.success is True
    assert "중복" in duplicate.message
    assert received == ["msg-1"]


def test_legacy_message_without_kind_still_blocks_task_duplicates(tmp_path: Path):
    """kind 가 없는 구버전 메시지의 task 중복 차단은 그대로 유지한다."""
    received: list[str] = []

    def on_peer_message(sender_id: str, content: str, correlation: PeerCorrelation):
        received.append(correlation.message_id)
        return True, "ok", ""

    servicer = MessengerServicer(
        lambda text: text, on_peer_message, dedup_path=str(tmp_path / "dedup.sqlite3")
    )
    try:
        first = _send(servicer, _coop_message(message_id="msg-1", task_id="task-legacy"))
        duplicate = _send(servicer, _coop_message(message_id="msg-2", task_id="task-legacy"))
    finally:
        servicer._dedup_db.close()

    assert first.success is True
    assert duplicate.success is True
    assert "중복" in duplicate.message
    assert received == ["msg-1"]


@pytest.mark.parametrize("kind", [KIND_RESULT, KIND_REPLY])
def test_distinct_task_messages_are_unaffected_by_kind(tmp_path: Path, kind: str):
    received: list[str] = []

    def on_peer_message(sender_id: str, content: str, correlation: PeerCorrelation):
        received.append(correlation.task_id)
        return True, "ok", ""

    servicer = MessengerServicer(
        lambda text: text, on_peer_message, dedup_path=str(tmp_path / "dedup.sqlite3")
    )
    try:
        _send(servicer, _coop_message(message_id="msg-1", task_id="task-a", kind=kind))
        _send(servicer, _coop_message(message_id="msg-2", task_id="task-b", kind=kind))
    finally:
        servicer._dedup_db.close()

    assert received == ["task-a", "task-b"]


def test_cooperation_callback_exception_returns_failure_instead_of_grpc_unknown(tmp_path: Path):
    def on_peer_message(sender_id: str, content: str, correlation: PeerCorrelation):
        raise RuntimeError("callback exploded")

    servicer = MessengerServicer(
        lambda text: text, on_peer_message, dedup_path=str(tmp_path / "dedup.sqlite3")
    )
    try:
        response = _send(
            servicer, _coop_message(message_id="msg-1", task_id="task-1", kind=KIND_REQUEST)
        )
    finally:
        servicer._dedup_db.close()

    assert response.success is False
    assert "협동 메시지 처리 실패" in response.message
