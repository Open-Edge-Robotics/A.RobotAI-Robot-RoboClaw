"""협동 메시지 상관 ID 전달 규약 테스트 (ROS2/생성코드 불필요)."""

from __future__ import annotations

import json

import pytest
from robo_claw_channel.peer_envelope import (
    COOPERATION_MESSAGE_TYPE,
    KIND_REQUEST,
    KIND_RESULT,
    MAX_RESULT_CHARS,
    PeerCorrelation,
    call_peer_message_callback,
    correlation_from_envelope,
    correlation_from_metadata,
    parse_cooperation_envelope,
    peer_callback_accepts_correlation,
)

pytestmark = pytest.mark.unit


def _envelope(**fields) -> str:
    payload = {
        "_robo_claw_message_type": COOPERATION_MESSAGE_TYPE,
        "content": "거실로 이동해줘",
        "created_at": 1.0,
        "expires_at": 300.0,
    }
    payload.update(fields)
    return json.dumps(payload, ensure_ascii=False)


def test_parse_envelope_returns_type_and_payload():
    parsed = parse_cooperation_envelope(_envelope(message_id="msg-1"))

    assert parsed is not None
    message_type, payload = parsed
    assert message_type == COOPERATION_MESSAGE_TYPE
    assert payload["message_id"] == "msg-1"


def test_parse_envelope_rejects_plain_and_malformed_payloads():
    assert parse_cooperation_envelope("그냥 명령") is None
    assert parse_cooperation_envelope('{"content": "표식 없음"}') is None
    assert parse_cooperation_envelope('{"_robo_claw_message_type": "peer_cooperation"') is None
    assert parse_cooperation_envelope("[1, 2, 3]") is None


def test_correlation_from_envelope_keeps_ids_and_kind():
    parsed = parse_cooperation_envelope(
        _envelope(
            message_id="msg-2",
            task_id="task-1",
            session_id="session-1",
            reply_to="msg-1",
            status="Completed",
            result="정리 끝",
            kind=KIND_RESULT,
        )
    )
    assert parsed is not None

    correlation = correlation_from_envelope(parsed[1])

    assert correlation.message_id == "msg-2"
    assert correlation.task_id == "task-1"
    assert correlation.session_id == "session-1"
    assert correlation.reply_to == "msg-1"
    assert correlation.status == "completed"
    assert correlation.result == "정리 끝"
    assert correlation.kind == KIND_RESULT


def test_legacy_envelope_without_correlation_fields_is_empty():
    parsed = parse_cooperation_envelope(_envelope())
    assert parsed is not None

    correlation = correlation_from_envelope(parsed[1])

    assert correlation == PeerCorrelation()


def test_correlation_from_metadata_ignores_auth_material():
    correlation = correlation_from_metadata(
        {
            "type": COOPERATION_MESSAGE_TYPE,
            "auth_token": "secret",
            "message_id": "msg-1",
            "task_id": "task-1",
            "kind": KIND_REQUEST,
        }
    )

    metadata = correlation.as_chat_metadata()

    assert metadata == {"message_id": "msg-1", "task_id": "task-1", "kind": KIND_REQUEST}
    assert "secret" not in json.dumps(metadata)


def test_chat_metadata_generates_message_id_for_legacy_senders():
    metadata = PeerCorrelation(task_id="task-1").as_chat_metadata(
        generate_message_id=lambda: "generated-1"
    )

    assert metadata["message_id"] == "generated-1"
    assert metadata["task_id"] == "task-1"


def test_peer_message_fields_drop_empty_and_bound_result():
    correlation = PeerCorrelation(
        message_id="msg-1",
        session_id="session-1",
        status="completed",
        result="가" * 5000,
    )

    fields = correlation.as_peer_message_fields()

    assert fields["message_id"] == "msg-1"
    assert fields["session_id"] == "session-1"
    assert fields["status"] == "completed"
    assert "task_id" not in fields
    assert len(fields["result"]) <= MAX_RESULT_CHARS


def test_ids_are_normalized_for_transport():
    correlation = correlation_from_metadata({"message_id": " msg 1 / 2 ", "kind": "REQUEST"})

    assert correlation.message_id == "msg_1_2"
    assert correlation.kind == KIND_REQUEST


def test_legacy_two_argument_callback_still_works():
    received: list[tuple[str, str]] = []

    def legacy(sender_id: str, content: str) -> bool:
        received.append((sender_id, content))
        return True

    assert peer_callback_accepts_correlation(legacy) is False
    assert (
        call_peer_message_callback(legacy, "robot-a", "도와줘", PeerCorrelation(message_id="msg-1"))
        is True
    )
    assert received == [("robot-a", "도와줘")]


def test_three_argument_callback_receives_correlation():
    captured: list[PeerCorrelation] = []

    def modern(sender_id: str, content: str, correlation: PeerCorrelation) -> bool:
        captured.append(correlation)
        return True

    assert peer_callback_accepts_correlation(modern) is True
    call_peer_message_callback(
        modern, "robot-a", "도와줘", PeerCorrelation(message_id="msg-1", task_id="task-1")
    )

    assert captured[0].message_id == "msg-1"
    assert captured[0].task_id == "task-1"


def test_var_positional_callback_receives_correlation():
    captured: list[tuple] = []

    def flexible(*args) -> bool:
        captured.append(args)
        return True

    call_peer_message_callback(flexible, "robot-a", "도와줘", PeerCorrelation(task_id="task-1"))

    assert len(captured[0]) == 3


# ---------------------------------------------------------------------------
# PeerMessage.srv 필드 채우기 / 미재빌드 감지
# ---------------------------------------------------------------------------


class _OldPeerMessageRequest:
    """재빌드 전 생성코드 — 상관 필드가 없는 slotted 요청."""

    __slots__ = ("peer_name", "message")

    def __init__(self) -> None:
        self.peer_name = "robot-a"
        self.message = "도와줘"


class _NewPeerMessageRequest:
    """재빌드 후 생성코드 — 상관 필드를 가진 slotted 요청."""

    __slots__ = (
        "peer_name",
        "message",
        "message_id",
        "task_id",
        "session_id",
        "reply_to",
        "status",
        "result",
        "kind",
    )

    def __init__(self) -> None:
        self.peer_name = "robot-a"
        self.message = "도와줘"
        self.message_id = ""
        self.task_id = ""
        self.session_id = ""
        self.reply_to = ""
        self.status = ""
        self.result = ""
        self.kind = ""


def test_fill_peer_message_request_reports_rebuild_required_for_old_service():
    from robo_claw_channel.peer_envelope import (
        REBUILD_REQUIRED_ERROR,
        fill_peer_message_request,
    )

    request = _OldPeerMessageRequest()

    error = fill_peer_message_request(request, PeerCorrelation(message_id="msg-1"))

    assert error is not None
    assert "rebuild required" in error
    assert "message_id" in error
    assert not hasattr(request, "message_id")
    assert REBUILD_REQUIRED_ERROR in error


def test_fill_peer_message_request_sets_fields_on_rebuilt_service():
    from robo_claw_channel.peer_envelope import fill_peer_message_request

    request = _NewPeerMessageRequest()
    correlation = PeerCorrelation(
        message_id="msg-1", task_id="task-1", session_id="session-1", kind=KIND_RESULT
    )

    assert fill_peer_message_request(request, correlation) is None
    assert request.message_id == "msg-1"
    assert request.task_id == "task-1"
    assert request.session_id == "session-1"
    assert request.kind == KIND_RESULT
    assert request.reply_to == ""


def test_peer_message_fields_include_kind():
    from robo_claw_channel.peer_envelope import PEER_MESSAGE_FIELDS

    assert "kind" in PEER_MESSAGE_FIELDS


def test_callback_exception_becomes_failure_tuple():
    from robo_claw_channel.peer_envelope import call_peer_message_callback_safely

    def broken(sender_id: str, content: str, correlation: PeerCorrelation):
        raise RuntimeError("boom")

    success, message, result_json = call_peer_message_callback_safely(
        broken, "robot-a", "도와줘", PeerCorrelation(task_id="task-1")
    )

    assert success is False
    assert "협동 메시지 처리 실패" in message
    assert "RuntimeError" in message
    assert result_json == ""


def test_log_preview_bounds_and_masks_sensitive_text():
    from robo_claw_channel.peer_envelope import MAX_LOG_PREVIEW_CHARS, log_preview

    preview = log_preview("Authorization: Bearer abc.def token=plain " + "가" * 500)

    assert len(preview) <= MAX_LOG_PREVIEW_CHARS
    assert "abc.def" not in preview
    assert "plain" not in preview
