"""메시지 검증 로직 단위 테스트 — 에이전트에 전달할 채팅 메시지 규칙."""

import pytest
from robo_claw_dashboard.domain.validation import (
    DEFAULT_MAX_CHAT_CHARS,
    validate_chat_message,
)

pytestmark = pytest.mark.unit


class TestValidateChatMessage:
    def test_empty_message_rejected(self):
        ok, err = validate_chat_message("")
        assert ok is False
        assert err

    def test_whitespace_only_rejected(self):
        ok, err = validate_chat_message("   \n\t ")
        assert ok is False
        assert err

    def test_normal_message_accepted(self):
        ok, err = validate_chat_message("현재 배터리 상태 알려줘")
        assert ok is True
        assert err == ""

    def test_exceeding_max_chars_rejected(self):
        ok, err = validate_chat_message("a" * (DEFAULT_MAX_CHAT_CHARS + 1))
        assert ok is False
        assert "길이" in err

    def test_exactly_max_chars_accepted(self):
        ok, _ = validate_chat_message("a" * DEFAULT_MAX_CHAT_CHARS)
        assert ok is True

    def test_custom_max_chars_respected(self):
        ok, err = validate_chat_message("abcd", max_chars=3)
        assert ok is False
        assert "3" in err

    def test_non_string_input_rejected(self):
        ok, err = validate_chat_message(None)
        assert ok is False
        assert err
