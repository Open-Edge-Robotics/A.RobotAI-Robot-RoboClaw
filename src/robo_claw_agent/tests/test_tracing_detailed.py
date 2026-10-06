import asyncio

import pytest
from robo_claw_agent.tracing import (
    current_trace_id,
    redact_trace_data,
    trace_process_inputs,
    traceable,
)


def test_disabled_tracing_preserves_sync_behavior(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    calls = []
    expected = {"value": 7}

    @traceable(run_type="tool", name="test")
    def operation(value):
        calls.append(value)
        return expected

    result = operation(7)

    assert result is expected
    assert calls == [7]
    assert current_trace_id() == ""


def test_disabled_tracing_preserves_async_behavior_and_exceptions(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    @traceable(run_type="chain", name="async_test")
    async def operation(fail=False):
        if fail:
            raise RuntimeError("original error")
        return "ok"

    assert asyncio.run(operation()) == "ok"
    with pytest.raises(RuntimeError, match="original error"):
        asyncio.run(operation(fail=True))


def test_trace_redaction_removes_secrets_and_large_payloads():
    redacted = redact_trace_data(
        {
            "api_key": "secret",
            "nested": {"authorization": "Bearer token", "normal": "ok"},
            "image_base64": "abc",
            "binary": b"1234",
        }
    )

    assert redacted == {
        "api_key": "<REDACTED>",
        "nested": {"authorization": "<REDACTED>", "normal": "ok"},
        "image_base64": "<REDACTED>",
        "binary": "<BYTES:4>",
    }


def test_trace_process_inputs_omits_runtime_objects():
    result = trace_process_inputs(
        {
            "self": object(),
            "goal_handle": object(),
            "func": lambda: None,
            "skill_name": "get_status",
            "params": {"token": "secret", "query": "status"},
        }
    )

    assert result == {
        "skill_name": "get_status",
        "params": {"token": "<REDACTED>", "query": "status"},
    }
