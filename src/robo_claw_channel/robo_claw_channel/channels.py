"""
메신저 채널 클래스 모음 — Telegram, Slack, Discord 지원
"""

import logging
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

# 각 채널별 라이브러리 임포트 시도
try:
    import telebot

    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False

try:
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient
    from slack_sdk.socket_mode.request import SocketModeRequest
    from slack_sdk.socket_mode.response import SocketModeResponse

    HAS_SLACK = True
except ImportError:
    HAS_SLACK = False

try:
    import discord

    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False

logger = logging.getLogger(__name__)

# Discord accepts at most 2,000 UTF-16 code units per message. Keep headroom for
# platform-side length accounting and do not split a Unicode code point.
DISCORD_MESSAGE_SAFE_LIMIT = 1900


def _split_discord_message(text: str) -> list[str]:
    """텍스트를 Discord 제한보다 짧은 조각으로 나누며 내용을 보존합니다."""
    chunks: list[str] = []
    remaining = text

    while remaining:
        used_units = 0
        end = 0
        for end, character in enumerate(remaining, start=1):
            character_units = 2 if ord(character) > 0xFFFF else 1
            if used_units + character_units > DISCORD_MESSAGE_SAFE_LIMIT:
                end -= 1
                break
            used_units += character_units
        else:
            end = len(remaining)

        if end == 0:
            # The fixed safe limit always fits at least one Unicode code point.
            raise ValueError("Discord message limit is too small for a Unicode character")

        if end < len(remaining):
            # Prefer a nearby line or word boundary without creating tiny chunks.
            boundary = max(remaining.rfind("\n", 0, end), remaining.rfind(" ", 0, end))
            if boundary >= end // 2:
                end = boundary + 1

        chunks.append(remaining[:end])
        remaining = remaining[end:]

    return chunks


async def _handle_discord_message(
    channel: Any, on_message: Callable[[str], str], content: str
) -> None:
    """긴 처리와 모든 분할 메시지 전송 중 Discord typing 상태를 유지합니다."""
    import asyncio

    async with channel.typing():
        response = await asyncio.to_thread(on_message, content)
        for chunk in _split_discord_message(response):
            await channel.send(chunk)


class BaseMessengerChannel(ABC):
    """메신저 채널 공통 인터페이스"""

    def __init__(self, name: str, on_message_cb: Callable[[str], str]):
        self.name = name
        self.on_message = (
            on_message_cb  # 메시지 수신 시 호출할 콜백 (instruction -> response_text)
        )
        self._thread: threading.Thread | None = None
        self._running = False

    @abstractmethod
    def _run(self):
        """실제 리스닝 루프 (별도 스레드에서 실행됨)"""
        pass

    @abstractmethod
    def send_text(self, text: str, **kwargs: Any) -> bool:
        """텍스트 메시지 전송"""
        pass

    @abstractmethod
    def send_file(self, file_path: str, caption: str = "", **kwargs: Any) -> bool:
        """파일(이미지 등) 전송"""
        pass

    def start(self):
        """채널 시작"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("[%s] Channel started", self.name)

    def stop(self):
        """채널 정지"""
        self._running = False
        logger.info("[%s] Channel stopped", self.name)


# ── 텔레그램 채널 ──────────────────────────────────────────────────


class TelegramChannel(BaseMessengerChannel):
    def __init__(
        self,
        token: str,
        on_message_cb: Callable[[str], str],
        proxy: str | None = None,
    ):
        super().__init__("Telegram", on_message_cb)
        self.token = token
        self.proxy = proxy
        self.bot: telebot.TeleBot | None = None
        self._last_chat_id: int | None = None

    def _run(self):
        if not HAS_TELEGRAM:
            logger.error("Telegram library (pyTelegramBotAPI) is not installed.")
            return

        try:
            if self.proxy:
                from telebot import apihelper

                apihelper.proxy = {"https": self.proxy}
                logger.info("[Telegram] Using proxy: %s", self.proxy)

            self.bot = telebot.TeleBot(self.token)

            @self.bot.message_handler(func=lambda message: True)
            def handle_message(message):
                self._last_chat_id = message.chat.id
                response = self.on_message(message.text)
                if self.bot is None:
                    raise Exception("Telegram bot is not available.")
                self.bot.reply_to(message, response)

            logger.info("Starting Telegram bot polling...")
            self.bot.infinity_polling()
        except Exception as e:
            logger.error("Telegram channel error: %s", e)

    def send_text(self, text: str, **kwargs: Any) -> bool:
        chat_id = kwargs.get("chat_id") or self._last_chat_id
        if not self.bot or not chat_id:
            return False
        try:
            self.bot.send_message(chat_id, text)
            return True
        except Exception as e:
            logger.error("[Telegram] Failed to send message: %s", e)
            return False

    def send_file(self, file_path: str, caption: str = "", **kwargs: Any) -> bool:
        chat_id = kwargs.get("chat_id") or self._last_chat_id
        if not self.bot or not chat_id:
            return False
        try:
            with open(file_path, "rb") as f:
                self.bot.send_document(chat_id, f, caption=caption)
            return True
        except Exception as e:
            logger.error("[Telegram] Failed to send file: %s", e)
            return False


# ── 슬랙 채널 (Socket Mode) ────────────────────────────────────────


class SlackChannel(BaseMessengerChannel):
    def __init__(
        self, app_token: str, bot_token: str, on_message_cb: Callable[[str], str]
    ):
        super().__init__("Slack", on_message_cb)
        self.app_token = app_token
        self.bot_token = bot_token
        self.client: SocketModeClient | None = None
        self._last_channel_id: str | None = None

    def _run(self):
        if not HAS_SLACK:
            logger.error("Slack SDK (slack-sdk) is not installed.")
            return

        try:
            self.client = SocketModeClient(
                app_token=self.app_token, web_client=WebClient(token=self.bot_token)
            )

            def process(client: SocketModeClient, req: SocketModeRequest):
                if req.type == "events_api":
                    event = req.payload.get("event", {})
                    if event.get("type") == "message" and not event.get("bot_id"):
                        text = event.get("text", "")
                        channel = event.get("channel")
                        self._last_channel_id = channel

                        response = SocketModeResponse(envelope_id=req.envelope_id)
                        client.send_socket_mode_response(response)

                        res_text = self.on_message(text)
                        client.web_client.chat_postMessage(
                            channel=channel, text=res_text
                        )

            self.client.socket_mode_request_listeners.append(process)
            self.client.connect()

            while self._running:
                time.sleep(1)
        except Exception as e:
            logger.error("Slack channel error: %s", e)

    def send_text(self, text: str, **kwargs: Any) -> bool:
        channel = kwargs.get("channel") or self._last_channel_id
        if not self.client or not channel:
            return False
        try:
            self.client.web_client.chat_postMessage(channel=channel, text=text)
            return True
        except Exception as e:
            logger.error("[Slack] Failed to send message: %s", e)
            return False

    def send_file(self, file_path: str, caption: str = "", **kwargs: Any) -> bool:
        channel = kwargs.get("channel") or self._last_channel_id
        if not self.client or not channel:
            return False
        try:
            self.client.web_client.files_upload_v2(
                channel=channel, file=file_path, title=caption, initial_comment=caption
            )
            return True
        except Exception as e:
            logger.error("[Slack] Failed to send file: %s", e)
            return False


# ── 디스코드 채널 ──────────────────────────────────────────────────


class DiscordChannel(BaseMessengerChannel):
    def __init__(self, token: str, on_message_cb: Callable[[str], str]):
        super().__init__("Discord", on_message_cb)
        self.token = token
        self.client: discord.Client | None = None
        self._last_channel: Any | None = None

    def _run(self):
        if not HAS_DISCORD:
            logger.error("Discord library (discord.py) is not installed.")
            return

        try:
            intents = discord.Intents.default()
            intents.message_content = True
            self.client = discord.Client(intents=intents)

            @self.client.event
            async def on_ready():
                if self.client is None:
                    logger.error("Discord bot is not available.")
                    return
                logger.info("Discord bot login complete: %s", self.client.user)

            @self.client.event
            async def on_message(message):
                if self.client is None:
                    logger.error("Discord bot is not available.")
                    return
                if message.author == self.client.user:
                    return
                self._last_channel = message.channel
                await _handle_discord_message(
                    message.channel, self.on_message, message.content
                )

            self.client.run(self.token)
        except Exception as e:
            logger.error("Discord channel error: %s", e)

    def send_text(self, text: str, **kwargs: Any) -> bool:
        # 비동기 라이브러리이므로 동기 래퍼 필요 (간소화를 위해 스텁 처리하거나 비동기 처리 루프 필요)
        # 여기서는 단순화를 위해 마지막 채널이 있고 루프가 살아있을 때 시도
        if not self.client or not self._last_channel:
            return False
        try:
            import asyncio

            asyncio.run_coroutine_threadsafe(
                self._last_channel.send(text), self.client.loop
            )
            return True
        except Exception as e:
            logger.error("[Discord] Failed to send message: %s", e)
            return False

    def send_file(self, file_path: str, caption: str = "", **kwargs: Any) -> bool:
        if not self.client or not self._last_channel:
            return False
        try:
            import asyncio
            import os

            file = discord.File(file_path, filename=os.path.basename(file_path))
            asyncio.run_coroutine_threadsafe(
                self._last_channel.send(content=caption, file=file), self.client.loop
            )
            return True
        except Exception as e:
            logger.error("[Discord] Failed to send file: %s", e)
            return False


# ── gRPC 채널 ──────────────────────────────────────────────────


class GrpcChannel(BaseMessengerChannel):
    """gRPC 서버를 다른 메신저 채널처럼 다루기 위한 래퍼"""

    def __init__(self, grpc_server: Any):
        # gRPC는 서버 역할을 하므로 on_message는 서버 내부에서 직접 처리함
        super().__init__("gRPC", lambda x: x)
        self.server = grpc_server

    def _run(self):
        # gRPC 서버는 ChannelNode에서 별도로 시작하므로 여기서는 대기만 함
        while self._running:
            time.sleep(1)

    def send_text(self, text: str, **kwargs: Any) -> bool:
        try:
            self.server._servicer.push_message(text)
            return True
        except Exception as e:
            logger.error("[gRPC] Failed to push message: %s", e)
            return False

    def send_file(self, file_path: str, caption: str = "", **kwargs: Any) -> bool:
        try:
            self.server.notify_file_available(file_path, caption)
            return True
        except Exception as e:
            logger.error("[gRPC] Failed to notify file: %s", e)
            return False
