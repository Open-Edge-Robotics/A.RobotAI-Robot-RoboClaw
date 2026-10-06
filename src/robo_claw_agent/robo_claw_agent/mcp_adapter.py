"""
MCP(Model Context Protocol) 서버를 기존 SkillManager에 연결하는 어댑터.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import logging
import threading
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from functools import partial
from typing import Any, Protocol

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import (
    streamable_http_client,
)

# mcp 1.x/2.x 모두 streamable_http가 아니라 shared._httpx_utils에 정의한다
# (공개 재수출이 선언되어 있지 않아 streamable_http에서 import하면 타입 검사 경고 발생).
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.types import (
    AudioContent,
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
    TextContent,
    Tool,
)

from .skill_manager import BaseSkill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# mcp 1.x / 2.x 클라이언트 API 호환 계층
#
# mcp 2.0.0은 ClientSession.list_tools의 cursor 파라미터를 제거하고
# (list_tools(*, params=...)만 지원), Tool/CallToolResult/ContentBlock의 필드명을
# snake_case로 바꿨다. 설치된 버전에 관계없이 동일하게 동작하도록 이름/타입
# 차이를 여기서만 처리한다.
# ---------------------------------------------------------------------------
# 2.x(snake_case)를 먼저 찾고, 없으면 1.x(camelCase)를 사용한다.
_NEXT_CURSOR_KEYS = ("next_cursor", "nextCursor")
_INPUT_SCHEMA_KEYS = ("input_schema", "inputSchema")
_IS_ERROR_KEYS = ("is_error", "isError")
_STRUCTURED_CONTENT_KEYS = ("structured_content", "structuredContent")
_MIME_TYPE_KEYS = ("mime_type", "mimeType")


def _first_attr(obj: Any, keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if hasattr(obj, key):
            return getattr(obj, key)
    return default


def result_next_cursor(result: Any) -> str | None:
    """``ListToolsResult``의 다음 페이지 커서를 1.x/2.x 필드명 모두에서 읽는다."""
    return _first_attr(result, _NEXT_CURSOR_KEYS, None)


def tool_input_schema(tool: Any) -> dict[str, Any] | None:
    """``Tool.inputSchema``/``input_schema``를 읽는다. dict가 아니면 None."""
    schema = _first_attr(tool, _INPUT_SCHEMA_KEYS, None)
    return schema if isinstance(schema, dict) else None


def result_is_error(result: Any) -> bool:
    """``CallToolResult``의 오류 여부를 1.x/2.x 필드명 모두에서 읽는다."""
    return bool(_first_attr(result, _IS_ERROR_KEYS, False))


def result_structured_content(result: Any) -> Any:
    """``CallToolResult``의 구조화 결과를 1.x/2.x 필드명 모두에서 읽는다."""
    return _first_attr(result, _STRUCTURED_CONTENT_KEYS, None)


def block_mime_type(block: Any) -> str | None:
    """이미지/오디오 content block의 MIME 타입을 1.x/2.x 필드명 모두에서 읽는다."""
    return _first_attr(block, _MIME_TYPE_KEYS, None)


def accepts_keyword(func: Any, name: str) -> bool:
    """``func``가 ``name`` 키워드 인자를 받을 수 있는지 확인한다."""
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):  # 내장 함수 등 시그니처를 알 수 없는 경우
        return False
    if name in parameters:
        return True
    return any(parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())


def read_timeout_value(func: Any, timeout_sec: float) -> Any:
    """``func``가 요구하는 read timeout 타입에 맞춰 값을 변환한다.

    mcp 1.x는 ``timedelta``(내부에서 ``.total_seconds()`` 호출)를, 2.x는
    ``float``(초, ``anyio.fail_after``에 직접 전달)를 요구한다. 반환 타입이 설치
    버전에 따라 달라 정적 타입은 ``Any``로 둔다. 시그니처를 알 수 없으면 2.x
    규약인 초 단위 float를 사용한다.
    """
    try:
        annotation = inspect.signature(func).parameters["read_timeout_seconds"].annotation
    except (KeyError, TypeError, ValueError):
        return timeout_sec
    if "timedelta" in str(annotation):
        return timedelta(seconds=timeout_sec)
    return timeout_sec


def pagination_params(cursor: str | None) -> Any:
    """mcp 2.x의 ``list_tools(params=...)``에 전달할 페이지 파라미터를 만든다."""
    from mcp.types import PaginatedRequestParams

    return PaginatedRequestParams(cursor=cursor) if cursor is not None else None


def _float_option(config: dict[str, Any], key: str, default: float) -> float:
    """서버 설정의 숫자 옵션을 float로 변환한다. 값이 잘못되면 어떤 키인지 알려준다."""
    raw = config.get(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"MCP 서버 설정 {key}는 숫자여야 합니다: {raw!r}") from exc


class MCPToolCaller(Protocol):
    """MCPSkill이 사용하는 MCP 도구 호출 인터페이스.

    ``MCPManager``와 테스트 대역이 이 프로토콜만 만족하면 된다. 호출 결과는
    설치된 mcp 버전에 따라 필드명이 달라 ``Any``로 두고 위의 호환 헬퍼로 읽는다.
    """

    def call_tool(
        self,
        server: str,
        tool: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any: ...


@dataclass
class _ServerHandle:
    name: str
    session: ClientSession
    tools: list[Tool]
    shutdown_event: asyncio.Event
    task: asyncio.Task[None] | None = None


class MCPManager:
    """백그라운드 asyncio 루프에서 MCP 서버 세션을 유지한다."""

    MAX_RECONNECT_ATTEMPTS = 5
    INITIAL_CONNECT_ATTEMPTS = 3
    INITIAL_CONNECT_RETRY_DELAY_SEC = 0.5
    RECONNECT_BACKOFF_BASE_SEC = 2.0
    RECONNECT_BACKOFF_MAX_SEC = 60.0

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="robo-claw-mcp",
            daemon=True,
        )
        self._loop_ready = threading.Event()
        self._closed = False
        self._servers: dict[str, _ServerHandle] = {}
        self._retry_counts: dict[str, int] = {}
        self._reconnect_timers: dict[str, asyncio.TimerHandle] = {}
        self._thread.start()
        if not self._loop_ready.wait(timeout=5.0):
            raise RuntimeError("MCP 이벤트 루프 시작 실패")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop_ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def _submit(self, coro: Any, timeout: float | None = None) -> Any:
        if self._closed:
            raise RuntimeError("MCP 매니저가 이미 종료되었습니다.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError("MCP 작업 타임아웃") from None

    def load_servers(self, configs: Iterable[dict[str, Any]]) -> list[MCPSkill]:
        skills: list[MCPSkill] = []
        for raw_config in configs:
            if not isinstance(raw_config, dict):
                logger.error("Invalid MCP server config format: %r", raw_config)
                continue

            server_name = str(raw_config.get("name") or "").strip() or "<unnamed>"
            handle = None
            for attempt in range(1, self.INITIAL_CONNECT_ATTEMPTS + 1):
                try:
                    handle = self._submit(self._bootstrap_server(raw_config), timeout=30.0)
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == self.INITIAL_CONNECT_ATTEMPTS:
                        logger.exception(
                            "Failed to connect to MCP server [%s] after %d attempts: %s",
                            server_name,
                            attempt,
                            exc,
                        )
                    else:
                        logger.warning(
                            "MCP server initial connection failed [%s] (%d/%d): %s; retrying",
                            server_name,
                            attempt,
                            self.INITIAL_CONNECT_ATTEMPTS,
                            exc,
                        )
                        # load_servers는 ROS 초기화 스레드에서 동기 실행되므로
                        # 재시도 사이에 이벤트 루프를 막지 않고 잠시 양보한다.
                        threading.Event().wait(self.INITIAL_CONNECT_RETRY_DELAY_SEC)
            if handle is None:
                continue

            for tool in handle.tools:
                description = tool.description or tool.title or f"{server_name} MCP 도구"
                skills.append(
                    MCPSkill(
                        tool_name=tool.name,
                        tool_desc=description,
                        server_name=handle.name,
                        manager=self,
                        input_schema=tool_input_schema(tool),
                        risk_level=str(raw_config.get("risk_level") or "action"),
                        allow_with_others=bool(raw_config.get("allow_with_others", False)),
                    )
                )
        return skills

    async def _bootstrap_server(self, config: dict[str, Any]) -> _ServerHandle:
        ready: asyncio.Future[_ServerHandle] = self._loop.create_future()
        server_name = self._get_server_name(config)
        task = self._loop.create_task(self._serve_server(config, ready))
        task.add_done_callback(partial(self._on_server_task_done, server_name, config))
        handle = await ready
        handle.task = task
        self._servers[handle.name] = handle
        self._retry_counts.pop(handle.name, None)
        return handle

    def _on_server_task_done(
        self, server_name: str, config: dict[str, Any], task: asyncio.Task[None]
    ) -> None:
        handle = self._servers.pop(server_name, None)
        if self._closed:
            return
        if task.cancelled():
            return
        if handle is None:
            # 최초 연결 자체가 실패한 경우 - load_servers 호출부에서 이미 로그 처리되며,
            # 스킬이 등록된 적 없으므로 백그라운드 재연결을 시도하지 않는다.
            return
        exc = task.exception()
        if exc is not None:
            logger.error("MCP server session ended [%s]: %s", server_name, exc)
        else:
            logger.warning("MCP server session ended unexpectedly [%s]", server_name)
        self._schedule_reconnect(server_name, config)

    def _schedule_reconnect(self, server_name: str, config: dict[str, Any]) -> None:
        if self._closed:
            return
        attempt = self._retry_counts.get(server_name, 0) + 1
        if attempt > self.MAX_RECONNECT_ATTEMPTS:
            logger.error(
                "Giving up on MCP server reconnect [%s]: exceeded max retry count (%d)",
                server_name,
                self.MAX_RECONNECT_ATTEMPTS,
            )
            self._retry_counts.pop(server_name, None)
            return
        self._retry_counts[server_name] = attempt
        delay = min(
            self.RECONNECT_BACKOFF_BASE_SEC * (2 ** (attempt - 1)),
            self.RECONNECT_BACKOFF_MAX_SEC,
        )
        logger.warning(
            "Scheduled MCP server reconnect [%s]: attempt %d/%d, in %.1fs",
            server_name,
            attempt,
            self.MAX_RECONNECT_ATTEMPTS,
            delay,
        )
        timer = self._loop.call_later(
            delay, lambda: self._loop.create_task(self._reconnect(server_name, config))
        )
        self._reconnect_timers[server_name] = timer

    async def _reconnect(self, server_name: str, config: dict[str, Any]) -> None:
        self._reconnect_timers.pop(server_name, None)
        if self._closed:
            return
        try:
            await self._bootstrap_server(config)
            logger.info("MCP server reconnected successfully [%s]", server_name)
        except Exception as exc:  # noqa: BLE001
            logger.error("MCP server reconnect failed [%s]: %s", server_name, exc)
            self._schedule_reconnect(server_name, config)

    async def _serve_server(
        self,
        config: dict[str, Any],
        ready: asyncio.Future[_ServerHandle],
    ) -> None:
        server_name = self._get_server_name(config)
        shutdown_event = asyncio.Event()
        try:
            async with self._open_transport(config) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await self._list_all_tools(session)
                    ready.set_result(
                        _ServerHandle(
                            name=server_name,
                            session=session,
                            tools=tools,
                            shutdown_event=shutdown_event,
                        )
                    )
                    await shutdown_event.wait()
        except Exception as exc:  # noqa: BLE001
            if not ready.done():
                ready.set_exception(exc)
                return
            raise

    async def _list_all_tools(self, session: ClientSession) -> list[Tool]:
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            result = await self._list_tools_page(session, cursor)
            tools.extend(result.tools)
            cursor = result_next_cursor(result)
            if not cursor:
                return tools

    @staticmethod
    async def _list_tools_page(session: ClientSession, cursor: str | None) -> Any:
        """mcp 1.x(``cursor=``)와 2.x(``params=``) 어느 쪽 API로도 페이지를 요청한다."""
        if accepts_keyword(session.list_tools, "cursor"):
            return await session.list_tools(cursor=cursor)
        return await session.list_tools(params=pagination_params(cursor))

    @asynccontextmanager
    async def _open_transport(self, config: dict[str, Any]) -> AsyncIterator[tuple[Any, Any]]:
        transport = str(config.get("transport") or "").strip().lower()
        if transport == "stdio":
            command = str(config.get("command") or "").strip()
            if not command:
                raise ValueError("stdio MCP 서버에는 command가 필요합니다.")
            args = [str(arg) for arg in config.get("args", [])]
            env_raw = config.get("env")
            env = (
                {str(key): str(value) for key, value in env_raw.items()}
                if isinstance(env_raw, dict)
                else None
            )
            cwd = config.get("cwd")
            params = StdioServerParameters(
                command=command,
                args=args,
                env=env,
                cwd=str(cwd) if cwd else None,
            )
            async with stdio_client(params) as streams:
                yield streams
            return

        if transport in ("streamable_http", "streamable-http", "http"):
            url = str(config.get("url") or "").strip()
            if not url:
                raise ValueError("streamable_http MCP 서버에는 url이 필요합니다.")
            headers_raw = config.get("headers")
            headers = (
                {str(key): str(value) for key, value in headers_raw.items()}
                if isinstance(headers_raw, dict)
                else None
            )
            async with create_mcp_http_client(headers=headers) as http_client:
                async with streamable_http_client(url=url, http_client=http_client) as streams:
                    # streamable_http_client는 세 번째 값으로 session id getter를
                    # 반환하지만 ClientSession에는 양방향 스트림만 전달한다.
                    yield streams[0], streams[1]
            return

        if transport == "sse":
            url = str(config.get("url") or "").strip()
            if not url:
                raise ValueError("sse MCP 서버에는 url이 필요합니다.")
            headers_raw = config.get("headers")
            headers = (
                {str(key): str(value) for key, value in headers_raw.items()}
                if isinstance(headers_raw, dict)
                else None
            )
            async with sse_client(
                url=url,
                headers=headers,
                timeout=_float_option(config, "connect_timeout_sec", 5.0),
                sse_read_timeout=_float_option(config, "read_timeout_sec", 300.0),
            ) as streams:
                yield streams
            return

        raise ValueError(f"지원하지 않는 MCP transport: {transport}")

    def _get_server_name(self, config: dict[str, Any]) -> str:
        return str(config.get("name") or "").strip() or "<unnamed>"

    def call_tool(
        self,
        server: str,
        tool: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> CallToolResult:
        handle = self._servers.get(server)
        if handle is None:
            raise KeyError(f"MCP 서버를 찾을 수 없습니다: {server}")

        return self._submit(
            self._call_tool(handle, tool, params or {}, timeout),
            timeout=timeout + 1.0,
        )

    async def _call_tool(
        self,
        handle: _ServerHandle,
        tool: str,
        params: dict[str, Any],
        timeout: float,
    ) -> CallToolResult:
        return await handle.session.call_tool(
            name=tool,
            arguments=params,
            read_timeout_seconds=read_timeout_value(handle.session.call_tool, timeout),
        )

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        future = asyncio.run_coroutine_threadsafe(self._shutdown_async(), self._loop)
        try:
            future.result(timeout=10.0)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5.0)

    async def _shutdown_async(self) -> None:
        for timer in self._reconnect_timers.values():
            timer.cancel()
        self._reconnect_timers.clear()
        self._retry_counts.clear()
        tasks: list[asyncio.Task[None]] = []
        for handle in list(self._servers.values()):
            handle.shutdown_event.set()
            if handle.task is not None:
                tasks.append(handle.task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._servers.clear()


class MCPSkill(BaseSkill):
    """MCP tool을 기존 BaseSkill 형태로 래핑한다.

    MCP의 inputSchema는 LLM이 정확한 파라미터를 생성하는 데 필요한 정보이므로
    스킬 메타데이터에 보존한다. ``name``은 에이전트가 호출하는 공개 이름이고,
    ``_tool_name``은 MCP 서버에 전달할 실제 도구 이름이다.
    """

    name = "__mcp__"
    description = "MCP tool wrapper"
    enabled = True

    def __init__(
        self,
        tool_name: str,
        tool_desc: str,
        server_name: str,
        manager: MCPToolCaller,
        input_schema: dict[str, Any] | None = None,
        risk_level: str = "action",
        allow_with_others: bool = False,
    ) -> None:
        super().__init__()
        self.name = tool_name
        self._tool_name = tool_name
        self.description = tool_desc
        self.input_schema = input_schema if isinstance(input_schema, dict) else None
        # MCP 서버는 기본적으로 action/단독 실행으로 취급한다. 서버 설정에서
        # 읽기 전용 또는 체이닝 허용을 명시한 경우에만 완화한다.
        self.risk_level = risk_level
        self.allow_with_others = allow_with_others
        self._server = server_name
        self._manager = manager
        self._timeout_hint: float = 30.0

    def rename(self, exposed_name: str) -> None:
        """서버 간 도구 이름 충돌을 피하기 위한 공개 이름 변경."""
        self.name = exposed_name

    def set_timeout_hint(self, timeout_sec: float) -> None:
        self._timeout_hint = timeout_sec if timeout_sec and timeout_sec > 0 else 30.0

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        logger.info(
            "[MCP skill call] server='%s', tool='%s', params=%s",
            self._server,
            self.name,
            params,
        )
        result = self._manager.call_tool(
            self._server, self._tool_name, params, timeout=self._timeout_hint
        )
        payload = self._convert_result(result)
        success = not result_is_error(result)
        message = payload["message"] or ("MCP 도구 실행 완료" if success else "MCP 도구 실행 실패")
        logger.info(
            "[MCP skill complete] server='%s', tool='%s', success=%s, message='%s'",
            self._server,
            self.name,
            success,
            message,
        )
        return {
            "success": success,
            "message": message,
            "result": payload["result"],
        }

    def _convert_result(self, result: Any) -> dict[str, Any]:
        text_parts: list[str] = []
        blocks: list[Any] = []

        for content in result.content:
            converted = self._convert_content_block(content)
            blocks.append(converted)
            if isinstance(content, TextContent):
                text_parts.append(content.text)

        message = "\n".join(part for part in text_parts if part).strip()
        return {
            "message": message,
            "result": {
                "server": self._server,
                "tool": self._tool_name,
                "content": blocks,
                "structured_content": result_structured_content(result),
                "is_error": result_is_error(result),
            },
        }

    def _convert_content_block(self, block: Any) -> Any:
        if isinstance(block, TextContent):
            return {"type": "text", "text": block.text}
        if isinstance(block, ImageContent):
            return {
                "type": "image",
                "mime_type": block_mime_type(block),
                "data": block.data,
            }
        if isinstance(block, AudioContent):
            return {
                "type": "audio",
                "mime_type": block_mime_type(block),
                "data": block.data,
            }
        if isinstance(block, ResourceLink):
            return block.model_dump(mode="json", by_alias=True)
        if isinstance(block, EmbeddedResource):
            return block.model_dump(mode="json", by_alias=True)
        return block.model_dump(mode="json", by_alias=True)
