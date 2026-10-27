import asyncio

import pytest
from robo_claw_agent.tracing import (
    current_trace_id,
    redact_trace_data,
    trace_process_inputs,
    traceable,
)

pytestmark = pytest.mark.unit


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


_RECORDED_SPANS = []


@pytest.fixture
def recorded_spans(monkeypatch):
    import functools
    import sys
    import types

    from robo_claw_agent import tracing

    spans = _RECORDED_SPANS
    spans.clear()

    def fake_traceable(*args, **options):
        def decorate(func):
            @functools.wraps(func)
            def wrapped(*args, **kwargs):
                spans.append(options["name"])
                return func(*args, **kwargs)

            return wrapped

        return decorate

    monkeypatch.setattr(tracing, "is_active", lambda: True)
    monkeypatch.setitem(sys.modules, "langsmith", types.SimpleNamespace(traceable=fake_traceable))
    return spans


def test_runtime_operation_keeps_one_span_for_traced_call(recorded_spans):
    from robo_claw_agent.tracing import trace_operation

    @traceable(name="LLM.chat")
    def chat(value):
        return value

    assert trace_operation("llm_chat", chat, ("hello",), {}) == "hello"
    assert recorded_spans == ["LLM.chat"]


def test_runtime_operation_names_untraced_work_and_preserves_failure(recorded_spans):
    from robo_claw_agent.tracing import trace_operation

    def search(query):
        raise TimeoutError(query)

    with pytest.raises(TimeoutError, match="query"):
        trace_operation("rag_search", search, ("query",), {})
    assert recorded_spans == ["rag_search"]


@pytest.mark.parametrize("failure", ["none", "policy", "schema", "precondition"])
def test_skill_validation_preserves_results_without_child_spans(recorded_spans, failure):
    from robo_claw_agent.skills.base import BaseSkill
    from robo_claw_agent.skills.manager import SkillManager

    calls = []

    class Skill(BaseSkill):
        name = "example"

        def check_preconditions(self, params):
            return (failure != "precondition", "missing sensor")

        def validate_input_schema(self, params):
            return (failure != "schema", "invalid value")

        def execute(self, params):
            calls.append(params)
            return {"success": True, "message": "done"}

    manager = SkillManager()
    manager.register(Skill())
    if failure == "policy":
        manager.set_skill_policy(blocked=["example"])
    result = manager.execute("example", {}, timeout_sec=0)
    if failure == "none":
        assert result.success is True
        assert calls == [{}]
    else:
        assert calls == []
        assert result.success is False
        assert (
            result.result_data["failure_reason"]
            == {
                "policy": "safety_rejected",
                "schema": "invalid_input",
                "precondition": "precondition_failed",
            }[failure]
        )
        assert result.message
    assert recorded_spans == (
        ["SkillManager.execute", "skill_attempt_direct"]
        if failure == "none"
        else ["SkillManager.execute"]
    )


def test_orchestration_summary_omits_duplicate_conversation():
    from robo_claw_agent.tracing import trace_context_outputs, trace_summary_inputs

    messages = [{"role": "user", "content": "full conversation"}]
    assert trace_summary_inputs(
        {"messages": messages, "instruction": "status", "goal_handle": object()}
    ) == {
        "message_count": 1,
        "instruction": "status",
    }
    assert trace_context_outputs((messages, {"api_key": "secret", "healthy": True})) == {
        "message_count": 1,
        "robot_summary": {"api_key": "<REDACTED>", "healthy": True},
    }


def test_runtime_operation_disabled_preserves_arguments(monkeypatch):
    from robo_claw_agent.tracing import trace_operation

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    expected = object()
    assert trace_operation(
        "search", lambda value, *, limit: (value, limit), (expected,), {"limit": 2}
    ) == (expected, 2)


def test_context_output_summary_preserves_exception_completion():
    from robo_claw_agent.tracing import trace_context_outputs

    assert trace_context_outputs(None) is None
