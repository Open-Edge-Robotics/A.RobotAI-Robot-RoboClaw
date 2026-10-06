"""
AgentNode 및 LLM 브릿지 기본 테스트 (ROS2 없이 순수 Python 로직 검증)
"""

import pytest


def test_llm_bridge_factory_ollama():
    """올바른 provider로 브릿지 생성 확인"""
    from robo_claw_agent.llm_bridge import OllamaBridge, create_llm_bridge

    bridge = create_llm_bridge("ollama", model="llama3.2")
    assert isinstance(bridge, OllamaBridge)


def test_llm_bridge_factory_openai():
    from robo_claw_agent.llm_bridge import OpenAIBridge, create_llm_bridge

    bridge = create_llm_bridge("openai", model="gpt-4o-mini")
    assert isinstance(bridge, OpenAIBridge)


def test_llm_bridge_factory_azure():
    from robo_claw_agent.llm_bridge import AzureOpenAIBridge, create_llm_bridge

    bridge = create_llm_bridge("azure", deployment="gpt-4o")
    assert isinstance(bridge, AzureOpenAIBridge)


def test_azure_embedder_factory_accepts_configured_timeout(monkeypatch):
    """setup_llm passes timeout_sec to every provider, including Azure embeddings."""
    import openai
    from robo_claw_agent.llm_bridge import azure, create_llm_bridge

    captured = {}

    class FakeAzureOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "AzureOpenAI", FakeAzureOpenAI)
    monkeypatch.setattr(azure, "wrap_openai_client", lambda client: client)
    bridge = create_llm_bridge(
        "azure",
        model="gpt-4o",
        embedding_model="text-embedding-3-small",
        endpoint="https://example.invalid",
        api_key="mock",
        timeout_sec=17.0,
    )

    bridge._get_client()

    assert bridge._timeout_sec == 17.0
    assert captured["timeout"] == 17.0


def test_llm_bridge_factory_anthropic():
    from robo_claw_agent.llm_bridge import AnthropicBridge, create_llm_bridge

    bridge = create_llm_bridge("anthropic", model="claude-3-5-sonnet-20241022")
    assert isinstance(bridge, AnthropicBridge)


def test_llm_bridge_factory_invalid():
    """지원하지 않는 provider는 ValueError 발생"""
    from robo_claw_agent.llm_bridge import create_llm_bridge

    with pytest.raises(ValueError):
        create_llm_bridge("unknown_provider")


def test_llm_and_embedder_separate_initialization(tmp_path):
    """메인 LLM(Anthropic)과 임베더(OpenAI)를 각각 분리 생성하여 MemoryManager에 바인딩하는 통합 검증"""
    from unittest.mock import MagicMock

    from robo_claw_agent.llm_bridge import create_llm_bridge
    from robo_claw_agent.memory_manager import MemoryManager

    # 1. 메인 LLM으로 AnthropicBridge 생성
    create_llm_bridge("anthropic", model="claude-3-5-sonnet-20241022")

    # 2. 임베더로 OpenAIBridge 생성 (API 키는 임시 mock 처리)
    embedder = create_llm_bridge("openai", model="gpt-4o-mini", api_key="mock-key")

    # embedder.embed 동작 Mocking
    mock_vector = [0.1, 0.2, 0.3]
    embedder.embed = MagicMock(return_value=mock_vector)

    # 3. MemoryManager 생성 및 RAG 임베더 주입
    mem = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        rag_score_threshold=0.7,
    )
    mem.set_embedder(embedder)

    # RAG에 지식 수동 등록 시도
    stored = mem.add_knowledge("로봇 팔 그리퍼 동작 매뉴얼", {"type": "manual"})

    assert stored is True
    embedder.embed.assert_called_once_with("로봇 팔 그리퍼 동작 매뉴얼")
    assert mem._vector_store.count() == 1

    # 검색 테스트
    embedder.embed.reset_mock()
    embedder.embed.return_value = [0.1, 0.2, 0.35]
    results = mem.search_knowledge("그리퍼 동작", top_k=1)

    assert len(results) == 1
    assert results[0]["text"] == "로봇 팔 그리퍼 동작 매뉴얼"
    embedder.embed.assert_called_once_with("그리퍼 동작")


def test_embed_batch_default_fallback():
    """embed_batch 기본 구현이 embed 순차 호출 결과와 동일 순서로 반환하는지 검증"""
    from unittest.mock import MagicMock

    from robo_claw_agent.llm_bridge import create_llm_bridge

    # OllamaBridge는 embed_batch를 override하지 않아 기본 fallback을 사용
    bridge = create_llm_bridge("ollama", model="llama3.2")
    bridge.embed = MagicMock(side_effect=lambda t: [float(len(t))])

    result = bridge.embed_batch(["a", "bb", "ccc"])

    assert result == [[1.0], [2.0], [3.0]]
    assert bridge.embed.call_count == 3


def test_ollama_think_false_keeps_json_format(monkeypatch):
    from robo_claw_agent.llm_bridge import create_llm_bridge

    calls = []

    class FakeClient:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"content": '{"skill": null, "response": "ok"}'}}

    bridge = create_llm_bridge(
        "ollama",
        model="gemma4:e4b",
        options_json='{"think": false, "temperature": 0}',
    )
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(bridge, "_supports_think", lambda: True)

    bridge.chat([], response_format={"type": "json_object"})

    assert calls[0]["think"] is False
    assert calls[0]["format"] == "json"
    assert calls[0]["options"] == {"temperature": 0}


def test_ollama_think_true_skips_json_format(monkeypatch):
    from robo_claw_agent.llm_bridge import create_llm_bridge

    calls = []

    class FakeClient:
        def chat(self, **kwargs):
            calls.append(kwargs)
            return {"message": {"content": '{"skill": null, "response": "ok"}'}}

    bridge = create_llm_bridge(
        "ollama",
        model="gemma4:e4b",
        options_json='{"think": true, "temperature": 0}',
    )
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())
    monkeypatch.setattr(bridge, "_supports_think", lambda: True)

    bridge.chat([], response_format={"type": "json_object"})

    assert calls[0]["think"] is True
    assert "format" not in calls[0]
    assert calls[0]["options"] == {"temperature": 0}


# ── Anthropic: kwargs 처리 + content 블록 방어 (§D) ────────────────────────


def test_anthropic_chat_ignores_response_format_without_crashing(monkeypatch):
    """response_format(OpenAI 전용 kwarg)을 전달해도 예외 없이 무시되어야 한다."""
    import anthropic
    from robo_claw_agent.llm_bridge import AnthropicBridge

    captured = {}

    class FakeTextBlock:
        type = "text"
        text = '{"skill": null, "response": "ok"}'

    class FakeResponse:
        content = [FakeTextBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: FakeClient())

    bridge = AnthropicBridge(model="claude-3-5-sonnet-20241022", api_key="mock")
    result = bridge.chat(
        [{"role": "user", "content": "hi"}], response_format={"type": "json_object"}
    )

    assert result == '{"skill": null, "response": "ok"}'
    # response_format 자체는 Anthropic API에 없는 kwarg이므로 create()에 전달되지 않아야 한다.
    assert "response_format" not in captured


def test_anthropic_chat_forwards_supported_kwargs(monkeypatch):
    """temperature 등 Anthropic이 실제 지원하는 kwargs는 그대로 전달되어야 한다."""
    import anthropic
    from robo_claw_agent.llm_bridge import AnthropicBridge

    captured = {}

    class FakeTextBlock:
        type = "text"
        text = "ok"

    class FakeResponse:
        content = [FakeTextBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: FakeClient())

    bridge = AnthropicBridge(model="claude-3-5-sonnet-20241022", api_key="mock")
    bridge.chat([{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=512)

    assert captured["temperature"] == 0.2
    assert captured["max_tokens"] == 512


def test_anthropic_chat_raises_clear_error_when_no_text_block(monkeypatch):
    """content[0]이 thinking/tool_use뿐이면 원인 불명 AttributeError 대신 명확한 예외."""
    import anthropic
    from robo_claw_agent.llm_bridge import AnthropicBridge

    class FakeThinkingBlock:
        type = "thinking"
        thinking = "생각 중..."

    class FakeResponse:
        content = [FakeThinkingBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: FakeClient())

    bridge = AnthropicBridge(model="claude-3-5-sonnet-20241022", api_key="mock")
    with pytest.raises(ValueError, match="text 블록"):
        bridge.chat([{"role": "user", "content": "hi"}])


def test_anthropic_chat_raises_clear_error_when_content_empty(monkeypatch):
    import anthropic
    from robo_claw_agent.llm_bridge import AnthropicBridge

    class FakeResponse:
        content = []

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: FakeClient())

    bridge = AnthropicBridge(model="claude-3-5-sonnet-20241022", api_key="mock")
    with pytest.raises(ValueError, match="text 블록"):
        bridge.chat([{"role": "user", "content": "hi"}])


def test_anthropic_chat_finds_text_block_after_thinking_block(monkeypatch):
    """thinking 블록이 먼저 와도 이후 text 블록을 정상적으로 찾아야 한다."""
    import anthropic
    from robo_claw_agent.llm_bridge import AnthropicBridge

    class FakeThinkingBlock:
        type = "thinking"
        thinking = "생각 중..."

    class FakeTextBlock:
        type = "text"
        text = '{"skill": "get_status", "params": {}}'

    class FakeResponse:
        content = [FakeThinkingBlock(), FakeTextBlock()]

    class FakeMessages:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: FakeClient())

    bridge = AnthropicBridge(model="claude-3-5-sonnet-20241022", api_key="mock")
    result = bridge.chat([{"role": "user", "content": "hi"}])

    assert result == '{"skill": "get_status", "params": {}}'


# ── OpenAI/Azure: 네이티브 tool_call 시 content=None 방어 (§D) ─────────────


def test_openai_chat_returns_content_when_present(monkeypatch):
    from robo_claw_agent.llm_bridge import OpenAIBridge

    class FakeMessage:
        content = '{"skill": null, "response": "ok"}'
        tool_calls = None

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    bridge = OpenAIBridge(model="gpt-4o-mini", api_key="mock")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())

    assert bridge.chat([{"role": "user", "content": "hi"}]) == '{"skill": null, "response": "ok"}'


def test_openai_chat_falls_back_to_tool_calls_when_content_none(monkeypatch):
    """content=None(네이티브 tool_call 응답)이어도 빈 문자열 대신 tool_calls JSON을 반환해야 한다."""
    import json

    from robo_claw_agent.agent_node.utils import parse_llm_plan
    from robo_claw_agent.llm_bridge import OpenAIBridge

    class FakeFunction:
        name = "navigate_to"
        arguments = '{"target_name": "거실"}'

    class FakeToolCall:
        function = FakeFunction()

    class FakeMessage:
        content = None
        tool_calls = [FakeToolCall()]

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    bridge = OpenAIBridge(model="gpt-4o-mini", api_key="mock")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())

    result = bridge.chat([{"role": "user", "content": "거실로 가"}])

    assert result != ""
    parsed = json.loads(result)
    assert "tool_calls" in parsed

    # parse_llm_plan의 기존 tool_calls 폴백 경로가 그대로 소화하는지 확인 —
    # 새 파싱 경로가 아니라 기존 처리기로 흘려보내는 것이 이 수정의 핵심이다.
    plan = parse_llm_plan(result)
    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "navigate_to"
    assert plan.skills[0].params == {"target_name": "거실"}


def test_openai_chat_returns_empty_string_when_no_content_and_no_tool_calls(monkeypatch):
    from robo_claw_agent.llm_bridge import OpenAIBridge

    class FakeMessage:
        content = None
        tool_calls = None

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    bridge = OpenAIBridge(model="gpt-4o-mini", api_key="mock")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())

    assert bridge.chat([{"role": "user", "content": "hi"}]) == ""


def test_azure_chat_falls_back_to_tool_calls_when_content_none(monkeypatch):
    """Azure OpenAI 브릿지도 동일한 tool_calls 폴백을 가져야 한다."""
    from robo_claw_agent.llm_bridge import AzureOpenAIBridge

    class FakeFunction:
        name = "get_status"
        arguments = "{}"

    class FakeToolCall:
        function = FakeFunction()

    class FakeMessage:
        content = None
        tool_calls = [FakeToolCall()]

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]

    class FakeCompletions:
        def create(self, **kwargs):
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    bridge = AzureOpenAIBridge(deployment="gpt-4o", endpoint="https://x", api_key="mock")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())

    result = bridge.chat([{"role": "user", "content": "hi"}])

    assert "tool_calls" in result
    assert "get_status" in result
