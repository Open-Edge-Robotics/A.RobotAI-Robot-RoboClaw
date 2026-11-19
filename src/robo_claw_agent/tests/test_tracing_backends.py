"""트레이싱 백엔드(LangSmith/Langfuse) 회귀 테스트.

실제 SDK/네트워크 없이 fake 모듈로 검증한다. 확인하는 계약은 다음과 같다.

* 백엔드별 환경변수 게이트와 비활성 시 no-op
* Langfuse observation 유형(generation/span)과 토큰 사용량 기록
* trace metadata/tag 전달과 redaction
* 마스킹 훅 연결, flush 실패 격리, 억제 컨텍스트
"""

from __future__ import annotations

import asyncio
import functools
import sys
import types

import pytest
from robo_claw_agent.tracing_backends import BACKENDS

pytestmark = pytest.mark.unit


class FakeLangfuseContext:
    """langfuse.decorators.langfuse_context 대역."""

    def __init__(self) -> None:
        self.configured: list[dict] = []
        self.trace_updates: list[dict] = []
        self.observation_updates: list[dict] = []
        self.current_trace_id = "trace-lf"
        self.current_observation_id = "obs-lf"
        self.flush_count = 0

    def configure(self, **kwargs):
        self.configured.append(kwargs)

    def get_current_trace_id(self):
        return self.current_trace_id

    def get_current_observation_id(self):
        return self.current_observation_id

    def update_current_trace(self, **kwargs):
        self.trace_updates.append(kwargs)

    def update_current_observation(self, **kwargs):
        self.observation_updates.append(kwargs)

    def flush(self):
        self.flush_count += 1


class FakeRun:
    def __init__(self, trace_id: str) -> None:
        self.id = "run-id"
        self.trace_id = trace_id
        self.extra: dict = {}
        self.tags: list[str] = []


@pytest.fixture(autouse=True)
def clean_tracing(monkeypatch):
    """백엔드 캐시와 트레이싱 환경변수를 초기화한다."""
    for name in (
        "LANGSMITH_TRACING",
        "LANGCHAIN_TRACING_V2",
        "LANGFUSE_TRACING",
        "LANGSMITH_PROJECT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf")
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    for backend in BACKENDS:
        backend.reset()
    yield
    for backend in BACKENDS:
        backend.reset()


def install_fake_langfuse(monkeypatch, *, records: list | None = None):
    """가짜 langfuse 모듈을 sys.modules에 주입한다."""
    observations = records if records is not None else []

    def observe(*args, **options):
        def decorate(func):
            if asyncio.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*a, **kw):
                    observations.append(
                        {"name": options.get("name"), "as_type": options.get("as_type")}
                    )
                    return await func(*a, **kw)

                return async_wrapper

            @functools.wraps(func)
            def wrapper(*a, **kw):
                observations.append(
                    {"name": options.get("name"), "as_type": options.get("as_type")}
                )
                return func(*a, **kw)

            return wrapper

        return decorate

    langfuse = types.ModuleType("langfuse")
    decorators = types.ModuleType("langfuse.decorators")
    context = FakeLangfuseContext()
    decorators.observe = observe
    decorators.langfuse_context = context
    langfuse.decorators = decorators
    monkeypatch.setitem(sys.modules, "langfuse", langfuse)
    monkeypatch.setitem(sys.modules, "langfuse.decorators", decorators)
    return context, observations


def install_fake_langsmith(monkeypatch, *, trace_id: str = "trace-ls", flush_fails: bool = False):
    """가짜 langsmith 모듈을 sys.modules에 주입한다."""
    runs: list[FakeRun] = []
    names: list[str] = []
    state: dict = {"run": None}

    def traceable(*args, **options):
        def decorate(func):
            @functools.wraps(func)
            def wrapper(*a, **kw):
                names.append(options.get("name"))
                run = FakeRun(trace_id)
                state["run"] = run
                runs.append(run)
                return func(*a, **kw)

            return wrapper

        return decorate

    def get_current_run_tree():
        return state["run"]

    class Client:
        def flush(self):
            if flush_fails:
                raise RuntimeError("flush failed")

        def close(self):
            return None

    helpers = types.ModuleType("langsmith.run_helpers")
    helpers.get_current_run_tree = get_current_run_tree
    langsmith = types.SimpleNamespace(traceable=traceable, Client=Client, run_helpers=helpers)
    monkeypatch.setitem(sys.modules, "langsmith", types.SimpleNamespace(**vars(langsmith)))
    monkeypatch.setitem(sys.modules, "langsmith.run_helpers", helpers)
    return names, runs


def test_tracing_is_inactive_by_default():
    from robo_claw_agent.tracing import is_active, traceable

    calls = []

    @traceable(name="unit")
    def operation(value):
        calls.append(value)
        return value

    assert is_active() is False
    assert operation(3) == 3
    assert calls == [3]


def test_langfuse_gate_requires_keys(monkeypatch, caplog):
    from robo_claw_agent import tracing

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY")
    context, observations = install_fake_langfuse(monkeypatch)
    messages: list[str] = []
    tracing.init_tracing(messages.append)

    assert tracing.is_active() is False
    assert any("LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY" in message for message in messages)
    assert observations == []


def test_langfuse_maps_generation_span_types(monkeypatch):
    from robo_claw_agent.tracing import traceable

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    _, observations = install_fake_langfuse(monkeypatch)

    @traceable(run_type="llm", name="Bridge.chat")
    def chat():
        return "ok"

    @traceable(name="OpenAIBridge.chat")
    def chat_without_run_type():
        return "ok"

    @traceable(run_type="chain", name="execute_task")
    def orchestrate():
        return "ok"

    chat()
    chat_without_run_type()
    orchestrate()

    assert observations == [
        {"name": "Bridge.chat", "as_type": "generation"},
        {"name": "OpenAIBridge.chat", "as_type": "generation"},
        {"name": "execute_task", "as_type": None},
    ]


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            types.SimpleNamespace(
                usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=4)
            ),
            {"input": 10, "output": 4, "total": 14},
        ),
        (
            types.SimpleNamespace(usage=types.SimpleNamespace(input_tokens=7, output_tokens=3)),
            {"input": 7, "output": 3, "total": 10},
        ),
        ({"prompt_eval_count": 5, "eval_count": 2}, {"input": 5, "output": 2, "total": 7}),
        ({"embedding": [0.1]}, {}),
        (None, {}),
    ],
)
def test_langfuse_usage_details_are_recorded(monkeypatch, response, expected):
    from robo_claw_agent.tracing import record_generation_usage

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    context, _ = install_fake_langfuse(monkeypatch)

    record_generation_usage(response)

    if expected:
        assert context.observation_updates == [
            {"usage": {"unit": "TOKENS", **expected}, "usage_details": expected}
        ]
    else:
        assert context.observation_updates == []


def test_langfuse_usage_is_skipped_without_active_observation(monkeypatch):
    from robo_claw_agent.tracing import record_generation_usage

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    context, _ = install_fake_langfuse(monkeypatch)
    context.current_observation_id = None

    record_generation_usage({"prompt_eval_count": 1, "eval_count": 1})

    assert context.observation_updates == []


def test_trace_metadata_is_redacted_and_tagged(monkeypatch):
    from robo_claw_agent.tracing import set_current_trace_metadata

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    context, _ = install_fake_langfuse(monkeypatch)

    set_current_trace_metadata({"robot_id": "former-01", "api_key": "secret"}, ["robot:former-01"])

    assert context.trace_updates == [
        {
            "metadata": {"robot_id": "former-01", "api_key": "<REDACTED>"},
            "tags": ["robot:former-01"],
        }
    ]


def test_trace_metadata_is_skipped_without_active_trace(monkeypatch):
    from robo_claw_agent.tracing import set_current_trace_metadata

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    context, _ = install_fake_langfuse(monkeypatch)
    context.current_trace_id = None

    set_current_trace_metadata({"robot_id": "former-01"})

    assert context.trace_updates == []


def test_init_tracing_registers_mask_and_reports_both_backends(monkeypatch):
    from robo_claw_agent import tracing

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_HOST", "http://192.168.50.47:3000")
    install_fake_langsmith(monkeypatch)
    context, _ = install_fake_langfuse(monkeypatch)
    messages: list[str] = []

    tracing.init_tracing(messages.append)

    assert any("LangSmith tracing ENABLED" in message for message in messages)
    assert any(
        "Langfuse tracing ENABLED (host=http://192.168.50.47:3000)" in message
        for message in messages
    )
    mask = context.configured[0]["mask"]
    assert mask(data={"api_key": "secret", "binary": b"12"}) == {
        "api_key": "<REDACTED>",
        "binary": "<BYTES:2>",
    }


def test_both_backends_record_the_same_call(monkeypatch):
    from robo_claw_agent.tracing import traceable

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    langsmith_names, _ = install_fake_langsmith(monkeypatch)
    _, observations = install_fake_langfuse(monkeypatch)

    @traceable(run_type="tool", name="SkillManager.execute")
    def execute():
        return "done"

    assert execute() == "done"
    assert langsmith_names == ["SkillManager.execute"]
    assert observations == [{"name": "SkillManager.execute", "as_type": None}]


def test_langfuse_preserves_return_identity_and_exceptions(monkeypatch):
    from robo_claw_agent.tracing import traceable

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    install_fake_langfuse(monkeypatch)
    expected = object()

    @traceable(name="Identity.call")
    def operation(value):
        return value

    @traceable(name="Identity.fail")
    def failing():
        raise TimeoutError("timeout")

    @traceable(name="Identity.async")
    async def async_operation():
        return "async-ok"

    assert operation(expected) is expected
    with pytest.raises(TimeoutError, match="timeout"):
        failing()
    assert asyncio.run(async_operation()) == "async-ok"


def test_current_trace_ids_reports_alignment_with_langsmith_first(monkeypatch):
    from robo_claw_agent.tracing import current_trace_id, current_trace_ids, traceable

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    install_fake_langsmith(monkeypatch, trace_id="trace-ls")
    install_fake_langfuse(monkeypatch)

    @traceable(name="Trace.call")
    def operation():
        return (current_trace_id(), current_trace_ids())

    primary, ids = operation()

    assert primary == "trace-ls"
    assert ids == {"langsmith": "trace-ls", "langfuse": "trace-lf"}


def test_wrap_clients_is_a_noop_for_langfuse(monkeypatch):
    from robo_claw_agent.tracing import wrap_anthropic_client, wrap_openai_client

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    install_fake_langfuse(monkeypatch)
    client = object()

    assert wrap_openai_client(client) is client
    assert wrap_anthropic_client(client) is client


def test_flush_tracing_isolates_backend_failures(monkeypatch):
    from robo_claw_agent.tracing import flush_tracing

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    install_fake_langsmith(monkeypatch, flush_fails=True)
    context, _ = install_fake_langfuse(monkeypatch)

    flush_tracing()

    assert context.flush_count == 1


def test_suppress_call_logging_skips_langfuse_observations(monkeypatch):
    from robo_claw_agent.tracing import suppress_call_logging, traceable

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    _, observations = install_fake_langfuse(monkeypatch)

    @traceable(name="Healthcheck.embed")
    def embed():
        return "ok"

    with suppress_call_logging():
        assert embed() == "ok"

    assert observations == []
    assert embed() == "ok"
    assert observations == [{"name": "Healthcheck.embed", "as_type": "generation"}]


def test_missing_langfuse_package_disables_only_that_backend(monkeypatch):
    from robo_claw_agent import tracing

    monkeypatch.setenv("LANGFUSE_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2-test")
    monkeypatch.setitem(sys.modules, "langfuse", None)
    install_fake_langsmith(monkeypatch)
    messages: list[str] = []

    tracing.init_tracing(messages.append)

    assert any("the 'langfuse' package is not installed" in message for message in messages)
    assert any("LangSmith tracing ENABLED" in message for message in messages)
    assert tracing.is_active() is True
