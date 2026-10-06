"""mcp 1.x/2.x API 차이를 흡수하는 호환 헬퍼(mcp_adapter 내부) 검증."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")

from robo_claw_agent.mcp_adapter import (
    accepts_keyword,
    block_mime_type,
    pagination_params,
    read_timeout_value,
    result_is_error,
    result_next_cursor,
    result_structured_content,
    tool_input_schema,
)

pytestmark = [pytest.mark.unit, pytest.mark.mcp]


class _SnakeCaseResult:
    """mcp 2.x 스타일 필드명."""

    next_cursor = "page-2"
    structured_content = {"value": 1}
    is_error = True
    input_schema = {"type": "object"}


class _CamelCaseResult:
    """mcp 1.x 스타일 필드명."""

    nextCursor = "page-2"
    structuredContent = {"value": 1}
    isError = True
    inputSchema = {"type": "object"}


def test_result_next_cursor_reads_both_naming_conventions():
    assert result_next_cursor(_SnakeCaseResult()) == "page-2"
    assert result_next_cursor(_CamelCaseResult()) == "page-2"
    assert result_next_cursor(SimpleNamespace()) is None


def test_result_next_cursor_keeps_none_as_end_of_pagination():
    assert result_next_cursor(SimpleNamespace(next_cursor=None)) is None
    assert result_next_cursor(SimpleNamespace(nextCursor=None)) is None


def test_result_next_cursor_prefers_snake_case_when_both_exist():
    both = SimpleNamespace(next_cursor="snake", nextCursor="camel")
    assert result_next_cursor(both) == "snake"


def test_tool_input_schema_reads_both_naming_conventions():
    assert tool_input_schema(_SnakeCaseResult()) == {"type": "object"}
    assert tool_input_schema(_CamelCaseResult()) == {"type": "object"}


def test_tool_input_schema_returns_none_when_missing_or_not_dict():
    assert tool_input_schema(SimpleNamespace()) is None
    assert tool_input_schema(SimpleNamespace(input_schema="oops")) is None


def test_result_is_error_reads_both_naming_conventions():
    assert result_is_error(_SnakeCaseResult()) is True
    assert result_is_error(_CamelCaseResult()) is True
    assert result_is_error(SimpleNamespace()) is False


def test_result_structured_content_reads_both_naming_conventions():
    assert result_structured_content(_SnakeCaseResult()) == {"value": 1}
    assert result_structured_content(_CamelCaseResult()) == {"value": 1}
    assert result_structured_content(SimpleNamespace()) is None


def test_block_mime_type_reads_both_naming_conventions():
    assert block_mime_type(SimpleNamespace(mime_type="image/png")) == "image/png"
    assert block_mime_type(SimpleNamespace(mimeType="image/png")) == "image/png"
    assert block_mime_type(SimpleNamespace()) is None


def test_accepts_keyword_detects_explicit_and_var_keyword_parameters():
    class _Explicit:
        def list_tools(self, cursor=None):
            pass

    class _KeywordOnly:
        def list_tools(self, *, params=None):
            pass

    class _VarKeyword:
        def list_tools(self, **kwargs):
            pass

    assert accepts_keyword(_Explicit().list_tools, "cursor") is True
    assert accepts_keyword(_KeywordOnly().list_tools, "cursor") is False
    assert accepts_keyword(_VarKeyword().list_tools, "cursor") is True


def test_accepts_keyword_returns_false_without_signature():
    assert accepts_keyword(object(), "cursor") is False


def test_read_timeout_value_returns_timedelta_for_mcp1_signature():
    def call_tool(name, arguments=None, read_timeout_seconds: timedelta | None = None):
        pass

    value = read_timeout_value(call_tool, 5.0)

    assert isinstance(value, timedelta)
    assert value.total_seconds() == 5.0


def test_read_timeout_value_returns_seconds_for_mcp2_signature():
    def call_tool(name, arguments=None, read_timeout_seconds: float | None = None):
        pass

    value = read_timeout_value(call_tool, 5.0)

    assert isinstance(value, float)
    assert value == 5.0


def test_read_timeout_value_falls_back_to_seconds_without_annotation():
    def call_tool(name, arguments=None, read_timeout_seconds=None):
        pass

    assert read_timeout_value(call_tool, 5.0) == 5.0
    assert read_timeout_value(object(), 5.0) == 5.0


def test_pagination_params_builds_params_only_when_cursor_present():
    from mcp.types import PaginatedRequestParams

    assert pagination_params(None) is None

    params = pagination_params("page-2")

    assert isinstance(params, PaginatedRequestParams)
    assert params.cursor == "page-2"
