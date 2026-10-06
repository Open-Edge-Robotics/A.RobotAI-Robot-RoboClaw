"""MCP 어댑터가 mcp 1.x/2.x 클라이언트 API 양쪽에서 동작하는지 검증한다.

mcp 2.0.0은 ``list_tools``의 ``cursor`` 파라미터를 제거하고 Tool/CallToolResult의
필드명을 snake_case로 변경했다. 여기서는 실제 설치 버전에 의존하지 않도록
두 API 모양을 흉내내는 대역(fake)으로 어댑터를 검증한다.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp.types import TextContent
from robo_claw_agent.mcp_adapter import MCPManager, MCPSkill

pytestmark = [pytest.mark.unit, pytest.mark.mcp]


# ---------------------------------------------------------------------------
# mcp 1.x(camelCase) / 2.x(snake_case) 응답 대역
# ---------------------------------------------------------------------------
class _FakeTool:
    def __init__(
        self,
        *,
        snake_case: bool,
        name: str = "remote_echo",
        description: str = "원격 에코",
    ) -> None:
        self.name = name
        self.description = description
        if snake_case:
            self.input_schema = {"type": "object"}
        else:
            self.inputSchema = {"type": "object"}


class _FakeListToolsResult:
    def __init__(
        self, tools: list[_FakeTool], *, snake_case: bool, cursor: str | None = None
    ) -> None:
        self.tools = tools
        if snake_case:
            self.next_cursor = cursor
        else:
            self.nextCursor = cursor


class _FakeCallToolResult:
    def __init__(
        self,
        content: list[Any],
        *,
        snake_case: bool,
        structured: Any = None,
        is_error: bool = False,
    ) -> None:
        self.content = content
        if snake_case:
            self.structured_content = structured
            self.is_error = is_error
        else:
            self.structuredContent = structured
            self.isError = is_error


class _SessionState:
    """세션 대역이 공유하는 호출 기록과 응답 설정."""

    def __init__(self, *, snake_case: bool, pages: dict | None = None) -> None:
        self.snake_case = snake_case
        self.pages = pages or {None: {"tools": [], "next_cursor": None}}
        self.list_cursors: list[str | None] = []
        self.timeouts: list[Any] = []
        self.closed = False
        self.initialize_count = 0
        self.expect_tool = "remote_echo"
        self.expect_arguments: dict[str, Any] | None = None
        self.reply = "pong"

    def next_page(self, cursor: str | None) -> _FakeListToolsResult:
        self.list_cursors.append(cursor)
        page = self.pages.get(cursor, {"tools": [], "next_cursor": None})
        return _FakeListToolsResult(
            page["tools"], snake_case=self.snake_case, cursor=page["next_cursor"]
        )

    def call_result(self, name: str, arguments: dict[str, Any] | None) -> _FakeCallToolResult:
        assert name == self.expect_tool
        if self.expect_arguments is not None:
            assert arguments == self.expect_arguments
        return _FakeCallToolResult(
            [TextContent(type="text", text=self.reply)],
            snake_case=self.snake_case,
            structured={"reply": self.reply},
        )


class _SessionBase:
    _state: _SessionState

    def __init__(self, read_stream: Any, write_stream: Any) -> None:
        self._read_stream = read_stream
        self._write_stream = write_stream

    async def __aenter__(self) -> _SessionBase:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self._state.closed = True

    async def initialize(self) -> None:
        self._state.initialize_count += 1


class _Mcp1ClientSession(_SessionBase):
    """mcp 1.x: ``list_tools(cursor=...)``와 camelCase 필드."""

    async def list_tools(self, cursor: str | None = None) -> _FakeListToolsResult:
        return self._state.next_page(cursor)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: timedelta | None = None,
    ) -> _FakeCallToolResult:
        self._state.timeouts.append(read_timeout_seconds)
        return self._state.call_result(name, arguments)


class _Mcp2ClientSession(_SessionBase):
    """mcp 2.x: ``list_tools(*, params=...)``와 snake_case 필드."""

    async def list_tools(self, *, params: Any = None) -> _FakeListToolsResult:
        cursor = None if params is None else params.cursor
        return self._state.next_page(cursor)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: float | None = None,
    ) -> _FakeCallToolResult:
        self._state.timeouts.append(read_timeout_seconds)
        return self._state.call_result(name, arguments)


def _install_session(
    monkeypatch: pytest.MonkeyPatch,
    state: _SessionState,
) -> type[_SessionBase]:
    session_cls = _Mcp2ClientSession if state.snake_case else _Mcp1ClientSession

    class _BoundSession(session_cls):  # type: ignore[misc, valid-type]
        _state = state

    @asynccontextmanager
    async def fake_stdio_client(params: Any):
        yield object(), object()

    monkeypatch.setattr("robo_claw_agent.mcp_adapter.stdio_client", fake_stdio_client)
    monkeypatch.setattr("robo_claw_agent.mcp_adapter.ClientSession", _BoundSession)
    return _BoundSession


def _stdio_config(name: str = "fake-server") -> dict[str, Any]:
    return {"name": name, "transport": "stdio", "command": "dummy"}


# ---------------------------------------------------------------------------
# MCPSkill 결과 변환
# ---------------------------------------------------------------------------
class _FakeManager:
    def __init__(self, *, snake_case: bool) -> None:
        self._snake_case = snake_case

    def call_tool(
        self,
        server: str,
        tool: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> _FakeCallToolResult:
        assert server == "fake"
        assert tool == "echo"
        assert timeout == 30.0
        assert params is not None
        return _FakeCallToolResult(
            [TextContent(type="text", text=f"echo:{params['text']}")],
            snake_case=self._snake_case,
            structured={"echo": params["text"]},
        )


@pytest.mark.parametrize("snake_case", [False, True], ids=["mcp1", "mcp2"])
def test_mcp_skill_execute_converts_result(snake_case):
    skill = MCPSkill(
        "echo",
        "에코",
        "fake",
        _FakeManager(snake_case=snake_case),
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    result = skill.execute({"text": "hello"})

    assert result["success"] is True
    assert result["message"] == "echo:hello"
    assert result["result"]["structured_content"] == {"echo": "hello"}
    assert result["result"]["content"] == [{"type": "text", "text": "echo:hello"}]


@pytest.mark.parametrize("snake_case", [False, True], ids=["mcp1", "mcp2"])
def test_mcp_skill_execute_marks_error(snake_case):
    class _FailingManager:
        def call_tool(
            self,
            server: str,
            tool: str,
            params: dict[str, Any] | None = None,
            timeout: float = 30.0,
        ) -> _FakeCallToolResult:
            return _FakeCallToolResult(
                [TextContent(type="text", text="bad request")],
                snake_case=snake_case,
                is_error=True,
            )

    skill = MCPSkill("echo", "에코", "fake", _FailingManager())
    result = skill.execute({})

    assert result["success"] is False
    assert result["message"] == "bad request"
    assert result["result"]["is_error"] is True


def test_mcp_skill_execute_passes_timeout_hint():
    """set_timeout_hint로 지정한 timeout이 manager.call_tool에 그대로 전달되어야 한다"""
    calls = {}

    class _RecordingManager:
        def call_tool(
            self,
            server: str,
            tool: str,
            params: dict[str, Any] | None = None,
            timeout: float = 30.0,
        ) -> _FakeCallToolResult:
            calls["timeout"] = timeout
            return _FakeCallToolResult([TextContent(type="text", text="ok")], snake_case=False)

    skill = MCPSkill("echo", "에코", "fake", _RecordingManager())
    skill.set_timeout_hint(5.0)
    skill.execute({"text": "hi"})
    assert calls["timeout"] == 5.0

    # set_timeout_hint를 호출하지 않으면 기본값 30.0이 유지되어야 한다
    default_skill = MCPSkill("echo", "에코", "fake", _RecordingManager())
    default_skill.execute({"text": "hi"})
    assert calls["timeout"] == 30.0


def test_mcp_skill_set_timeout_hint_ignores_non_positive():
    """timeout_sec이 0 이하이면 기본값(30.0)으로 대체되어야 한다"""
    skill = MCPSkill("echo", "에코", "fake", _FakeManager(snake_case=False))
    skill.set_timeout_hint(0)
    assert skill._timeout_hint == 30.0
    skill.set_timeout_hint(-1)
    assert skill._timeout_hint == 30.0


# ---------------------------------------------------------------------------
# 세션 연결: mcp 1.x / 2.x API 양쪽 지원 (회귀 방지)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("snake_case", [False, True], ids=["mcp1", "mcp2"])
def test_mcp_manager_loads_stdio_server(monkeypatch, snake_case):
    state = _SessionState(snake_case=snake_case)
    state.pages = {None: {"tools": [_FakeTool(snake_case=snake_case)], "next_cursor": None}}
    state.expect_arguments = {"text": "ping"}
    _install_session(monkeypatch, state)

    manager = MCPManager()
    try:
        skills = manager.load_servers([_stdio_config()])

        assert len(skills) == 1
        assert skills[0].name == "remote_echo"
        # mcp 2.x의 input_schema 필드도 스키마로 전달되어야 한다.
        assert skills[0].input_schema == {"type": "object"}

        result = skills[0].execute({"text": "ping"})
        assert result["success"] is True
        assert result["message"] == "pong"
        assert result["result"]["structured_content"] == {"reply": "pong"}
    finally:
        manager.shutdown()

    assert state.closed is True
    assert state.initialize_count == 1


def test_mcp_manager_follows_pagination_with_mcp2_params(monkeypatch):
    """mcp 2.x의 params 기반 페이지 커서를 따라 모든 도구를 수집한다."""
    state = _SessionState(snake_case=True)
    state.pages = {
        None: {
            "tools": [_FakeTool(snake_case=True, name="first_tool")],
            "next_cursor": "page-2",
        },
        "page-2": {
            "tools": [_FakeTool(snake_case=True, name="second_tool")],
            "next_cursor": None,
        },
    }
    _install_session(monkeypatch, state)

    manager = MCPManager()
    try:
        skills = manager.load_servers([_stdio_config()])
    finally:
        manager.shutdown()

    assert [skill.name for skill in skills] == ["first_tool", "second_tool"]
    assert state.list_cursors == [None, "page-2"]


def test_mcp_manager_follows_pagination_with_mcp1_cursor(monkeypatch):
    """mcp 1.x에서도 동일한 페이지 순회가 유지된다."""
    state = _SessionState(snake_case=False)
    state.pages = {
        None: {"tools": [_FakeTool(snake_case=False, name="first_tool")], "next_cursor": "page-2"},
        "page-2": {"tools": [_FakeTool(snake_case=False, name="second_tool")], "next_cursor": None},
    }
    _install_session(monkeypatch, state)

    manager = MCPManager()
    try:
        skills = manager.load_servers([_stdio_config()])
    finally:
        manager.shutdown()

    assert [skill.name for skill in skills] == ["first_tool", "second_tool"]
    assert state.list_cursors == [None, "page-2"]


@pytest.mark.parametrize(
    ("snake_case", "expected_type"),
    [(False, timedelta), (True, float)],
    ids=["mcp1", "mcp2"],
)
def test_call_tool_read_timeout_matches_installed_api(monkeypatch, snake_case, expected_type):
    """mcp 1.x는 timedelta를, 2.x는 초 단위 float를 요구한다."""
    state = _SessionState(snake_case=snake_case)
    state.pages = {None: {"tools": [_FakeTool(snake_case=snake_case)], "next_cursor": None}}
    state.reply = "pong"
    _install_session(monkeypatch, state)

    manager = MCPManager()
    try:
        skills = manager.load_servers([_stdio_config()])
        skills[0].execute({"text": "ping"})
    finally:
        manager.shutdown()

    assert len(state.timeouts) == 1
    timeout = state.timeouts[0]
    assert isinstance(timeout, expected_type)
    if isinstance(timeout, timedelta):
        assert timeout.total_seconds() == 30.0
    else:
        assert timeout == 30.0


def test_mcp_manager_reconnects_after_server_crash(monkeypatch):
    """정상 연결됐던 서버 세션이 예기치 않게 종료되면 백오프 후 재연결을 시도한다"""
    monkeypatch.setattr(MCPManager, "RECONNECT_BACKOFF_BASE_SEC", 0.05)
    monkeypatch.setattr(MCPManager, "RECONNECT_BACKOFF_MAX_SEC", 0.05)

    state = _SessionState(snake_case=False)
    connect_count = {"value": 0}
    session_cls = _install_session(monkeypatch, state)

    @asynccontextmanager
    async def counting_stdio_client(params):
        connect_count["value"] += 1
        yield object(), object()

    monkeypatch.setattr("robo_claw_agent.mcp_adapter.stdio_client", counting_stdio_client)
    monkeypatch.setattr("robo_claw_agent.mcp_adapter.ClientSession", session_cls)

    manager = MCPManager()
    try:
        manager.load_servers([_stdio_config()])
        assert connect_count["value"] == 1
        assert "fake-server" in manager._servers

        # 세션이 예기치 않게 종료된 상황을 시뮬레이션 (shutdown()이 아닌 이벤트 직접 트리거)
        # asyncio.Event.set()은 스레드 안전하지 않으므로 루프 스레드에서 실행해야 한다.
        handle = manager._servers["fake-server"]
        manager._loop.call_soon_threadsafe(handle.shutdown_event.set)

        for _ in range(50):
            if connect_count["value"] >= 2:
                break
            time.sleep(0.05)

        assert connect_count["value"] >= 2
        assert "fake-server" in manager._servers
        assert manager._retry_counts.get("fake-server", 0) == 0
    finally:
        manager.shutdown()


def test_mcp_manager_gives_up_after_max_reconnect_attempts():
    """최대 재시도 횟수를 초과하면 재연결을 포기하고 카운터를 정리한다"""
    manager = MCPManager()
    try:
        name = "ghost-server"
        config = _stdio_config(name)

        async def _call_schedule():
            manager._schedule_reconnect(name, config)

        for _ in range(MCPManager.MAX_RECONNECT_ATTEMPTS):
            manager._submit(_call_schedule())
        assert manager._retry_counts.get(name) == MCPManager.MAX_RECONNECT_ATTEMPTS
        assert name in manager._reconnect_timers

        # 최대 횟수를 초과하는 호출 -> 포기
        manager._submit(_call_schedule())
        assert name not in manager._retry_counts
    finally:
        manager.shutdown()


def test_mcp_manager_no_reconnect_for_initial_connect_failure(monkeypatch):
    """최초 연결 자체가 실패한 서버는 재연결을 시도하지 않는다"""

    @asynccontextmanager
    async def fake_stdio_client(params):
        yield object(), object()

    class FailingClientSession:
        def __init__(self, read_stream, write_stream):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def initialize(self):
            raise RuntimeError("초기화 실패")

    monkeypatch.setattr("robo_claw_agent.mcp_adapter.stdio_client", fake_stdio_client)
    monkeypatch.setattr("robo_claw_agent.mcp_adapter.ClientSession", FailingClientSession)

    manager = MCPManager()
    try:
        skills = manager.load_servers([_stdio_config("bad-server")])
        assert skills == []
        assert manager._retry_counts == {}
        assert manager._reconnect_timers == {}
        assert "bad-server" not in manager._servers
    finally:
        manager.shutdown()


def test_mcp_manager_shutdown_cancels_pending_reconnect_timers():
    """shutdown() 호출 시 대기 중인 재연결 타이머가 모두 취소되어야 한다"""
    manager = MCPManager()
    name = "ghost-server"
    config = _stdio_config(name)

    async def _call_schedule():
        manager._schedule_reconnect(name, config)

    manager._submit(_call_schedule())
    assert name in manager._reconnect_timers

    manager.shutdown()

    assert manager._reconnect_timers == {}
    assert manager._retry_counts == {}
