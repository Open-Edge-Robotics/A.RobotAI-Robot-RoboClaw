"""네이티브 도구(tool) 호출 경로 테스트.

두 가지를 검증한다.

1. 스킬이 LLM API 의 ``tools`` 파라미터로 등록될 때, 호출 계약의 원본이
   스킬의 ``input_schema`` 하나뿐인지(=프롬프트용 어댑터/중복 정의가 없는지),
   그리고 그 스키마에서 무엇을 제거하고 무엇을 보존하는지.
2. 기존 JSON 응답 경로(기준선)와 네이티브 tool calling 경로가 **같은 스킬
   체인 실행 로직**을 공유하며, 툴 경로에서는 JSON 파싱이 필요 없다는 것.
"""

import importlib.util
import json
from pathlib import Path

import pytest
from robo_claw_agent.llm_bridge import ToolCall, ToolTurn
from robo_claw_agent.skills.tool_specs import build_tool_specs

_PROMPTS_PATH = (
    Path(__file__).resolve().parents[1] / "robo_claw_agent" / "agent_node" / "prompts.py"
)
_SPEC = importlib.util.spec_from_file_location("tool_calling_prompts_under_test", _PROMPTS_PATH)
_PROMPTS = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_PROMPTS)


# ── A. 도구 스펙 생성 ─────────────────────────────────────────────────────


def _skill(name="navigate_to", **overrides):
    skill = {
        "name": name,
        "description": "이동 스킬. 파라미터: target_name(str, 장소명, 필수)",
        "input_schema": {
            "title": "NavigateTo",
            "type": "object",
            "properties": {
                "target_name": {"type": "string", "description": "목적지 장소명"},
                "speed": {"type": "number", "default": 0.3, "minimum": 0.0, "maximum": 1.0},
                "mode": {"type": "string", "enum": ["fast", "slow"]},
            },
            "required": ["target_name"],
            "additionalProperties": False,
        },
    }
    skill.update(overrides)
    return skill


def test_build_tool_specs_uses_skill_name_and_description():
    specs = build_tool_specs([_skill()])

    assert len(specs) == 1
    assert specs[0]["type"] == "function"
    assert specs[0]["function"]["name"] == "navigate_to"
    assert specs[0]["function"]["description"].startswith("이동 스킬")


def test_build_tool_specs_keeps_required_default_and_enum():
    specs = build_tool_specs([_skill()])
    params = specs[0]["function"]["parameters"]

    assert params["required"] == ["target_name"]
    assert params["properties"]["speed"]["default"] == 0.3
    assert params["properties"]["speed"]["minimum"] == 0.0
    assert params["properties"]["mode"]["enum"] == ["fast", "slow"]


def test_build_tool_specs_strips_property_description_by_default():
    """per-property 설명은 제거된다 (실측: 제거가 더 정확하고 프롬프트가 더 싸다)."""
    specs = build_tool_specs([_skill()])
    params = specs[0]["function"]["parameters"]

    assert "description" not in params["properties"]["target_name"]


def test_build_tool_specs_can_keep_property_description():
    specs = build_tool_specs([_skill()], include_property_description=True)
    params = specs[0]["function"]["parameters"]

    assert params["properties"]["target_name"]["description"] == "목적지 장소명"


def test_build_tool_specs_drops_schema_title():
    specs = build_tool_specs([_skill()])

    assert "title" not in specs[0]["function"]["parameters"]


def test_build_tool_specs_does_not_mutate_source_schema():
    skill = _skill()
    build_tool_specs([skill])

    assert "description" in skill["input_schema"]["properties"]["target_name"]
    assert skill["input_schema"]["title"] == "NavigateTo"


def test_build_tool_specs_handles_missing_input_schema():
    specs = build_tool_specs([{"name": "say", "description": "말하기"}])

    assert specs[0]["function"]["parameters"] == {"type": "object", "properties": {}}


def test_build_tool_specs_skips_entries_without_name():
    assert build_tool_specs([{"description": "이름 없음"}]) == []


# ── B. 프롬프트 조립 ─────────────────────────────────────────────────────


class _Param:
    value = "moveit"


class _FakeNode:
    def __init__(self):
        self._system_prompt_base_cache = None
        self._tool_prompt_base_cache = None
        self._system_prompt = _PROMPTS._DEFAULT_SYSTEM_PROMPT
        self._skills = type(
            "Skills",
            (),
            {
                "list_skills": lambda self, include_internal=True: [
                    {
                        "name": "navigate_to",
                        "description": "이동",
                        "input_schema": {
                            "type": "object",
                            "properties": {"target_name": {"type": "string"}},
                            "required": ["target_name"],
                        },
                    }
                ]
            },
        )()
        self._skills_guide = ""
        self._script_skills_guide = ""
        self._robot_limits = ""
        self._troubleshooting_guide = ""
        self._agent_workspace_dir = ""
        self._startup_knowledge_ctx = ""
        self._robot_soul = ""

    def get_parameter(self, _name):
        return _Param()

    def _skill_lessons_section(self):
        return ""


def test_tool_mode_prompt_drops_json_only_sections():
    prompt = _PROMPTS.assemble_system_prompt(_FakeNode(), tool_mode=True)

    assert "[파라미터 규칙]" not in prompt
    assert "[응답 형식 (JSON만 반환" not in prompt
    assert "[응답 형식 (최종 확인" not in prompt
    assert "JSON 형식으로만 최종 답변하세요" not in prompt


def test_tool_mode_prompt_omits_skill_list_but_keeps_selection_strategy():
    prompt = _PROMPTS.assemble_system_prompt(_FakeNode(), tool_mode=True)

    # 스킬 목록은 tools 파라미터로 전달되므로 프롬프트에 중복 노출하지 않는다.
    assert "- navigate_to: 이동" not in prompt
    # 도구 선택 전략은实测에서 빼면 정확도가 크게 떨어지므로 반드시 남긴다.
    assert "[도구 선택 전략]" in prompt


def test_tool_mode_prompt_ends_with_tool_rules():
    prompt = _PROMPTS.assemble_system_prompt(_FakeNode(), tool_mode=True)

    assert prompt.rstrip().endswith(_PROMPTS._TOOL_MODE_TOOL_RULES.rstrip())
    assert "[도구 사용 규칙]" in prompt


def test_tool_mode_prompt_drops_skill_list_placeholder():
    prompt = _PROMPTS.assemble_system_prompt(_FakeNode(), tool_mode=True)

    assert "{skill_list}" not in prompt


def test_json_mode_prompt_is_unchanged():
    """기준선(JSON) 경로의 프롬프트는 여전히 스킬 목록과 JSON 형식 지시를 담는다."""
    prompt = _PROMPTS.assemble_system_prompt(_FakeNode())

    assert "- navigate_to: 이동" in prompt
    assert "[파라미터 규칙]" in prompt
    assert prompt.rstrip().endswith(_PROMPTS._RESPONSE_FORMAT_REMINDER.rstrip())


def test_strip_json_response_sections_replaces_json_wording():
    stripped = _PROMPTS.strip_json_response_sections(_PROMPTS._DEFAULT_SYSTEM_PROMPT)

    assert '{"skill": null, "response": "..."}' not in stripped
    assert "제공된 도구(tool) 목록" in stripped


# ── C. 브릿지 ────────────────────────────────────────────────────────────


def test_ollama_chat_with_tools_returns_structured_tool_calls(monkeypatch):
    from robo_claw_agent.llm_bridge import create_llm_bridge

    captured = {}

    class FakeClient:
        def chat(self, **kwargs):
            captured.update(kwargs)
            return {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "navigate_to",
                                "arguments": {"target_name": "거실"},
                            },
                        }
                    ],
                }
            }

    bridge = create_llm_bridge("ollama", model="qwen3.5:9b")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())

    turn = bridge.chat_with_tools([], tools=[{"type": "function", "function": {"name": "x"}}])

    assert turn.has_tool_calls
    assert turn.tool_calls[0].name == "navigate_to"
    assert turn.tool_calls[0].arguments == {"target_name": "거실"}
    assert captured["tools"] == [{"type": "function", "function": {"name": "x"}}]


def test_ollama_chat_with_tools_returns_plain_text_when_no_tool_called(monkeypatch):
    from robo_claw_agent.llm_bridge import create_llm_bridge

    class FakeClient:
        def chat(self, **kwargs):
            return {"message": {"content": "안녕하세요", "tool_calls": None}}

    bridge = create_llm_bridge("ollama", model="qwen3.5:9b")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())

    turn = bridge.chat_with_tools([], tools=[])

    assert turn.content == "안녕하세요"
    assert turn.tool_calls == []


def test_ollama_chat_with_tools_tolerates_invalid_arguments(monkeypatch):
    from robo_claw_agent.llm_bridge import create_llm_bridge

    class FakeClient:
        def chat(self, **kwargs):
            return {
                "message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "say", "arguments": "not-json"}}],
                }
            }

    bridge = create_llm_bridge("ollama", model="qwen3.5:9b")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())

    turn = bridge.chat_with_tools([], tools=[])

    assert turn.tool_calls[0].arguments == {}


def test_openai_chat_with_tools_parses_json_string_arguments(monkeypatch):
    from robo_claw_agent.llm_bridge import create_llm_bridge

    captured = {}

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
            captured.update(kwargs)
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    bridge = create_llm_bridge("openai", model="gpt-4o-mini", api_key="mock")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())

    turn = bridge.chat_with_tools([], tools=[{"type": "function", "function": {"name": "x"}}])

    assert turn.tool_calls[0].name == "navigate_to"
    assert turn.tool_calls[0].arguments == {"target_name": "거실"}
    assert captured["tools"] == [{"type": "function", "function": {"name": "x"}}]


def test_anthropic_tools_are_converted_to_input_schema_format():
    from robo_claw_agent.llm_bridge.anthropic import to_anthropic_tools

    converted = to_anthropic_tools(
        [
            {
                "type": "function",
                "function": {
                    "name": "navigate_to",
                    "description": "이동",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    )

    assert converted == [
        {
            "name": "navigate_to",
            "description": "이동",
            "input_schema": {"type": "object", "properties": {}},
        }
    ]


def test_parse_tool_arguments_handles_dict_str_and_invalid():
    from robo_claw_agent.llm_bridge.base import parse_tool_arguments

    assert parse_tool_arguments({"a": 1}) == {"a": 1}
    assert parse_tool_arguments('{"a": 1}') == {"a": 1}
    assert parse_tool_arguments("") == {}
    assert parse_tool_arguments("oops") == {}
    assert parse_tool_arguments(None) == {}


# ── D. 플래너: 툴 경로와 JSON 경로가 같은 실행 로직을 공유하는지 ──────────


@pytest.mark.asyncio
async def test_tool_mode_planner_executes_tool_call_chain():
    pytest.importorskip("rclpy")
    from robo_claw_agent.agent_node.planner import LLMPlanner
    from robo_claw_agent.skill_manager import SkillResult

    class _FakeSkillManager:
        def get_skill(self, name):
            return None

        def execute(self, name, params, timeout):
            return SkillResult(name, True, f"{name} 실행 완료", 0.01, {"echo": params})

    class _FakeToolLLM:
        def __init__(self):
            self.calls = []

        def supports_tools(self):
            return True

        def chat_with_tools(self, messages, system_prompt=None, tools=None, **kwargs):
            self.calls.append({"messages": list(messages), "tools": tools})
            # First call: return tool call
            if len(self.calls) == 1:
                return ToolTurn(
                    content="이동을 시작합니다.",
                    assistant_message={
                        "role": "assistant",
                        "content": "이동을 시작합니다.",
                        "thinking": "목적지를 확인합니다.",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "navigate_to",
                                    "arguments": {"target_name": "거실"},
                                }
                            }
                        ],
                    },
                    tool_calls=[
                        ToolCall(name="navigate_to", arguments={"target_name": "거실"}, id="call_1")
                    ],
                )
            # Second call: return final answer after seeing tool result
            return ToolTurn(content="거실로 이동 완료했습니다.", tool_calls=[])

    class _FakeNode:
        def __init__(self):
            self._llm = _FakeToolLLM()
            self._llm_tool_calling = True
            self._skills = _FakeSkillManager()
            self._enable_skill_learning = False
            self.states = []

        def get_logger(self):
            import logging

            return logging.getLogger("test_tool_calling")

        def _set_state(self, state, skill="", msg=""):
            self.states.append((state, skill, msg))

        def _build_system_prompt(self):
            return "system prompt"

        def _tool_specs(self):
            return build_tool_specs(
                [
                    {
                        "name": "navigate_to",
                        "description": "이동",
                        "input_schema": {
                            "type": "object",
                            "properties": {"target_name": {"type": "string"}},
                            "required": ["target_name"],
                        },
                    }
                ]
            )

        async def _run_blocking(self, op_name, func, *args, **kwargs):
            return func(*args, **kwargs)

        def _validate_skill_chain(self, chain):
            return None

        def _start_channel_send(self, msg_text, file_path):
            raise AssertionError("파일 전송이 없어야 합니다")

    class _FakeGoalHandle:
        class _Request:
            context_json = ""

        request = _Request()

    node = _FakeNode()
    planner = LLMPlanner(node)
    feedback = []
    result_msg, skill_results, task_success, _ = await planner.run_llm_planning_loop(
        _FakeGoalHandle(), "거실로 가줘", [], {}, 30.0, lambda text, progress: feedback.append(text)
    )

    assert [text for text in feedback if text.startswith("assistant: ")] == [
        "assistant: 이동을 시작합니다."
    ]
    assert task_success is True
    assert len(skill_results) == 1
    assert skill_results[0].skill_name == "navigate_to"
    assert skill_results[0].result_data["echo"] == {"target_name": "거실"}
    # 툴 목록이 LLM 에 전달되었어야 한다.
    assert node._llm.calls[0]["tools"][0]["function"]["name"] == "navigate_to"
    followup = node._llm.calls[1]["messages"]
    assistant = next(m for m in followup if m.get("tool_calls"))
    assert assistant["thinking"] == "목적지를 확인합니다."
    assert assistant["content"] == "이동을 시작합니다."
    assert followup[followup.index(assistant) + 1]["tool_name"] == "navigate_to"


@pytest.mark.asyncio
async def test_tool_mode_planner_uses_plain_text_final_answer():
    pytest.importorskip("rclpy")
    from robo_claw_agent.agent_node.planner import LLMPlanner

    class _FakeSkillManager:
        def get_skill(self, name):
            return None

        def execute(self, name, params, timeout):
            raise AssertionError("스킬이 실행되면 안 됩니다")

    class _FakeToolLLM:
        def supports_tools(self):
            return True

        def chat_with_tools(self, messages, system_prompt=None, tools=None, **kwargs):
            return ToolTurn(content="안녕하세요, 무엇을 도와드릴까요?", tool_calls=[])

    class _FakeNode:
        def __init__(self):
            self._llm = _FakeToolLLM()
            self._llm_tool_calling = True
            self._skills = _FakeSkillManager()
            self._enable_skill_learning = False

        def get_logger(self):
            import logging

            return logging.getLogger("test_tool_calling")

        def _set_state(self, state, skill="", msg=""):
            pass

        def _build_system_prompt(self):
            return "system prompt"

        def _tool_specs(self):
            return []

        async def _run_blocking(self, op_name, func, *args, **kwargs):
            return func(*args, **kwargs)

        def _validate_skill_chain(self, chain):
            return None

    class _FakeGoalHandle:
        class _Request:
            context_json = ""

        request = _Request()

    node = _FakeNode()
    planner = LLMPlanner(node)
    result_msg, skill_results, task_success, _ = await planner.run_llm_planning_loop(
        _FakeGoalHandle(), "안녕", [], {}, 30.0, lambda *a, **kw: None
    )

    assert task_success is True
    assert result_msg == "안녕하세요, 무엇을 도와드릴까요?"
    assert skill_results == []


@pytest.mark.asyncio
async def test_unknown_tool_name_is_dropped_and_answer_taken():
    pytest.importorskip("rclpy")
    from robo_claw_agent.agent_node.planner import LLMPlanner

    class _FakeSkillManager:
        def get_skill(self, name):
            return None

        def execute(self, name, params, timeout):
            raise AssertionError("등록되지 않은 도구는 실행되면 안 됩니다")

    class _FakeToolLLM:
        def supports_tools(self):
            return True

        def chat_with_tools(self, messages, system_prompt=None, tools=None, **kwargs):
            return ToolTurn(content="해당 도구는 없습니다", tool_calls=[])

    class _FakeNode:
        def __init__(self):
            self._llm = _FakeToolLLM()
            self._llm_tool_calling = True
            self._skills = _FakeSkillManager()
            self._enable_skill_learning = False

        def get_logger(self):
            import logging

            return logging.getLogger("test_tool_calling")

        def _set_state(self, state, skill="", msg=""):
            pass

        def _build_system_prompt(self):
            return "system prompt"

        def _tool_specs(self):
            return build_tool_specs([{"name": "navigate_to", "description": "이동"}])

        async def _run_blocking(self, op_name, func, *args, **kwargs):
            return func(*args, **kwargs)

        def _validate_skill_chain(self, chain):
            return None

    class _FakeGoalHandle:
        class _Request:
            context_json = ""

        request = _Request()

    node = _FakeNode()
    planner = LLMPlanner(node)
    result_msg, skill_results, task_success, _ = await planner.run_llm_planning_loop(
        _FakeGoalHandle(), "뭐 해줄 수 있어?", [], {}, 30.0, lambda *a, **kw: None
    )

    assert task_success is True
    assert skill_results == []
    assert result_msg == "해당 도구는 없습니다"


@pytest.mark.asyncio
async def test_json_mode_is_used_when_provider_lacks_tool_support():
    """툴 호출을 지원하지 않는 프로바이더에서는 기존 JSON 경로가 그대로 쓰인다."""
    pytest.importorskip("rclpy")
    from robo_claw_agent.agent_node.planner import LLMPlanner
    from robo_claw_agent.skill_manager import SkillResult

    class _FakeSkillManager:
        def get_skill(self, name):
            return None

        def execute(self, name, params, timeout):
            return SkillResult(name, True, f"{name} 실행 완료", 0.01, {})

    class _FakeJSONLLM:
        def __init__(self):
            self.calls = []

        def supports_tools(self):
            return False

        def chat(self, messages, system_prompt=None, **kwargs):
            self.calls.append(kwargs)
            return json.dumps({"skill": "get_status", "params": {}, "reason": "확인"})

    class _FakeNode:
        def __init__(self):
            self._llm = _FakeJSONLLM()
            self._llm_tool_calling = True  # 설정은 켜져 있지만 프로바이더가 미지원
            self._skills = _FakeSkillManager()
            self._enable_skill_learning = False

        def get_logger(self):
            import logging

            return logging.getLogger("test_tool_calling")

        def _set_state(self, state, skill="", msg=""):
            pass

        def _build_system_prompt(self):
            return "system prompt"

        async def _run_blocking(self, op_name, func, *args, **kwargs):
            return func(*args, **kwargs)

        def _validate_skill_chain(self, chain):
            return None

    class _FakeGoalHandle:
        class _Request:
            context_json = ""

        request = _Request()

    node = _FakeNode()
    planner = LLMPlanner(node)
    _result_msg, skill_results, task_success, _ = await planner.run_llm_planning_loop(
        _FakeGoalHandle(), "상태 알려줘", [], {}, 30.0, lambda *a, **kw: None
    )

    assert task_success is True
    assert len(skill_results) == 1
    assert skill_results[0].skill_name == "get_status"
    assert node._llm.calls[0]["response_format"] == {"type": "json_object"}


# ── E. 프롬프트 모드와 실행 모드가 어긋나지 않는지 ────────────────────────


def test_tool_calling_enabled_requires_flag_and_provider_support():
    """플래그가 켜져 있어도 프로바이더가 미지원이면 False."""

    class _NoTools:
        def supports_tools(self):
            return False

    class _WithTools:
        def supports_tools(self):
            return True

    from robo_claw_agent.agent_node.utils import tool_calling_enabled

    assert tool_calling_enabled(True, _WithTools()) is True
    assert tool_calling_enabled(True, _NoTools()) is False
    assert tool_calling_enabled(False, _WithTools()) is False
    assert tool_calling_enabled(True, None) is False


def test_prompt_mode_matches_planner_mode_when_provider_lacks_tools():
    """프로바이더가 tool 미지원이면 프롬프트도 JSON 모드여야 한다.

    프롬프트만 툴 모드(JSON 강제 섹션 제거)인데 실행은 JSON 경로로 가면
    모델이 JSON envelope 를 만들지 못해 응답이 깨진다.
    """
    from robo_claw_agent.agent_node.utils import tool_calling_enabled

    class _NoTools:
        def supports_tools(self):
            return False

    node = _FakeNode()
    node._llm = _NoTools()
    node._llm_tool_calling = True
    tool_mode = tool_calling_enabled(node._llm_tool_calling, node._llm)

    assert tool_mode is False
    prompt = _PROMPTS.assemble_system_prompt(node, tool_mode=tool_mode)
    # JSON 강제 섹션이 남아 있어야 한다 (툴 모드였다면 제거된다).
    assert "[응답 형식 (JSON만 반환, 설명 없이)]" in prompt
    assert "[도구 사용 규칙]" not in prompt


@pytest.mark.unit
def test_ollama_preserves_assistant_context(monkeypatch):
    from copy import deepcopy

    from robo_claw_agent.llm_bridge import create_llm_bridge

    message = {
        "role": "assistant",
        "thinking": "도구 결과를 확인해야 합니다.",
        "content": "<think>내부 추론</think>조회합니다.",
        "tool_calls": [{"function": {"index": 0, "name": "get_status", "arguments": {}}}],
    }
    captured = []

    class FakeClient:
        def chat(self, **kwargs):
            captured.append(deepcopy(kwargs["messages"]))
            return {"message": message}

    bridge = create_llm_bridge("ollama", model="fake")
    monkeypatch.setattr(bridge, "_get_client", lambda: FakeClient())
    turn = bridge.chat_with_tools([], tools=[])
    assert turn.content == "조회합니다."
    assert turn.assistant_message == message
    history = [turn.assistant_message, {"role": "tool", "tool_name": "get_status", "content": "{}"}]
    original = deepcopy(history)
    bridge.chat_with_tools(history, tools=[])
    assert captured[1] == original
    assert history == original


@pytest.mark.unit
@pytest.mark.asyncio
async def test_native_tool_failure_reports_unexecuted_calls():
    pytest.importorskip("rclpy")
    import logging
    from types import SimpleNamespace

    from robo_claw_agent.agent_node.planner import LLMPlanner
    from robo_claw_agent.skill_manager import SkillResult

    executed = []

    def execute(name, params, timeout):
        executed.append(name)
        return SkillResult(name, False, "timeout", 0.01, {"error_type": "Timeout"})

    class Node:
        _skills = SimpleNamespace(execute=execute, _skills={})
        _enable_skill_learning = False

        def get_logger(self):
            return logging.getLogger("test_context")

        def _set_state(self, *args):
            pass

        async def _run_blocking(self, op, func, *args, **kwargs):
            return func(*args, **kwargs)

    planner = LLMPlanner(Node())
    calls = [
        {"id": str(i), "function": {"name": name, "arguments": {}}}
        for i, name in enumerate(["get_status", "get_location"])
    ]
    messages = []
    success, _ = await planner.execute_skill_chain(
        [{"skill": "get_status"}, {"skill": "get_location"}],
        messages,
        "상태를 확인해줘",
        1,
        [],
        [],
        False,
        lambda *args: None,
        native_tool_calls=calls,
    )
    assert success is False
    assert executed == ["get_status"]
    results = [m for m in messages if m["role"] == "tool"]
    assert [m["tool_name"] for m in results] == ["get_status", "get_location"]
    assert json.loads(results[0]["content"])["success"] is False
    assert json.loads(results[1]["content"])["error_type"] == "NotExecuted"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_battery_and_scene_share_native_tool_history():
    pytest.importorskip("rclpy")
    import logging
    from copy import deepcopy
    from types import SimpleNamespace

    from robo_claw_agent.agent_node.planner import LLMPlanner
    from robo_claw_agent.skill_manager import SkillResult

    instruction = "배터리 상태를 확인하고 주변 환경을 분석해줘."
    requests = []
    turns = iter(
        [
            ToolTurn(content="배터리를 확인합니다.", tool_calls=[ToolCall(name="get_status")]),
            ToolTurn(
                content="배터리는 80%입니다. 주변을 분석합니다.",
                tool_calls=[ToolCall(name="analyze_scene")],
            ),
            ToolTurn(content="배터리는 80%이며 주변 통로는 비어 있습니다."),
        ]
    )

    class LLM:
        def supports_tools(self):
            return True

        def chat_with_tools(self, messages, **kwargs):
            requests.append(deepcopy(messages))
            return next(turns)

    executed = []

    def execute(name, params, timeout):
        executed.append(name)
        data = {"battery_pct": 80} if name == "get_status" else {"scene": "통로가 비어 있습니다."}
        return SkillResult(name, True, "완료", 0.01, data)

    class Node:
        _llm = LLM()
        _llm_tool_calling = True
        _enable_skill_learning = False
        _skills = SimpleNamespace(execute=execute)

        def get_logger(self):
            return logging.getLogger("native_compound")

        def _set_state(self, *args):
            pass

        def _build_system_prompt(self):
            return "도구 결과를 근거로 답하세요."

        def _tool_specs(self):
            return build_tool_specs([{"name": name} for name in ["get_status", "analyze_scene"]])

        def _validate_skill_chain(self, chain):
            return None

        async def _run_blocking(self, op, func, *args, **kwargs):
            return func(*args, **kwargs)

    feedback = []
    messages = [{"role": "user", "content": instruction}]
    result, _, success, _ = await LLMPlanner(Node()).run_llm_planning_loop(
        SimpleNamespace(),
        instruction,
        messages,
        {},
        30,
        lambda text, progress: feedback.append(text),
    )
    assert success is True
    assert executed == ["get_status", "analyze_scene"]
    assert result == "배터리는 80%이며 주변 통로는 비어 있습니다."
    assert requests[2][0] == {"role": "user", "content": instruction}
    assert [m["tool_name"] for m in requests[2] if m["role"] == "tool"] == [
        "get_status",
        "analyze_scene",
    ]
    assert not any(m["role"] == "system" for m in requests[2])
    assert [m for m in feedback if m.startswith("assistant: ")] == [
        "assistant: 배터리를 확인합니다.",
        "assistant: 배터리는 80%입니다. 주변을 분석합니다.",
    ]
