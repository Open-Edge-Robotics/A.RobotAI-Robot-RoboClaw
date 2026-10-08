"""Discord 응답 분할 및 typing 표시 테스트."""

import pytest
from robo_claw_channel.channels import (
    DISCORD_MAX_CHUNKS,
    DISCORD_MESSAGE_SAFE_LIMIT,
    _handle_discord_message,
    _split_discord_message,
)

pytestmark = pytest.mark.unit


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_split_discord_message_preserves_text_and_respects_safe_limit():
    response = ("상세 분석 결과입니다. 🚪\n" * 400) + ("🤖" * 1_200)

    chunks = _split_discord_message(response)

    assert len(chunks) > 1
    assert "".join(chunks) == response
    assert all(0 < _utf16_length(chunk) <= DISCORD_MESSAGE_SAFE_LIMIT for chunk in chunks)


def test_split_discord_message_caps_to_max_chunks():
    # 5개 청크를 훨씬 초과하는 거대한 메시지 (약 75,000자)
    huge_response = "엄청나게 긴 메시지 내용입니다. " * 4000
    chunks = _split_discord_message(huge_response)

    assert len(chunks) <= DISCORD_MAX_CHUNKS
    assert all(0 < _utf16_length(chunk) <= DISCORD_MESSAGE_SAFE_LIMIT for chunk in chunks)
    assert "생략" in chunks[-1]


def test_split_discord_message_keeps_short_response_unchanged():
    response = "작업이 완료되었습니다."

    assert _split_discord_message(response) == [response]


@pytest.mark.asyncio
async def test_discord_response_shows_typing_while_generating_and_sending():
    class FakeTyping:
        def __init__(self, channel):
            self.channel = channel

        async def __aenter__(self):
            self.channel.typing_active = True
            self.channel.typing_events.append("start")

        async def __aexit__(self, exc_type, exc, traceback):
            self.channel.typing_active = False
            self.channel.typing_events.append("stop")

    class FakeChannel:
        def __init__(self):
            self.typing_active = False
            self.typing_events = []
            self.sent = []
            self.sent_during_typing = []

        def typing(self):
            return FakeTyping(self)

        async def send(self, content):
            self.sent.append(content)
            self.sent_during_typing.append(self.typing_active)

    channel = FakeChannel()
    callback_typing_states = []
    response = "처리 결과입니다. " * 400

    def callback(request):
        assert request == "주방으로 이동해줘"
        callback_typing_states.append(channel.typing_active)
        return response

    await _handle_discord_message(channel, callback, "주방으로 이동해줘")

    assert callback_typing_states == [True]
    assert channel.typing_events == ["start", "stop"]
    assert channel.sent_during_typing and all(channel.sent_during_typing)
    assert "".join(channel.sent) == response
    assert all(_utf16_length(chunk) <= DISCORD_MESSAGE_SAFE_LIMIT for chunk in channel.sent)


@pytest.mark.asyncio
async def test_discord_handle_caps_sending_on_huge_payload():
    class FakeTyping:
        async def __aenter__(self):
            pass

        async def __aexit__(self, exc_type, exc, traceback):
            pass

    class FakeChannel:
        def __init__(self):
            self.sent = []

        def typing(self):
            return FakeTyping()

        async def send(self, content):
            self.sent.append(content)

    channel = FakeChannel()
    huge_response = "Base64 payload or huge output " * 5000  # 150,000자

    await _handle_discord_message(channel, lambda req: huge_response, "상태 확인")

    assert len(channel.sent) <= DISCORD_MAX_CHUNKS
    assert "생략" in channel.sent[-1]
