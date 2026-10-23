import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from robo_claw_agent.agent_node.planner import LLMPlanner

pytestmark = pytest.mark.unit


@pytest.fixture
def node():
    async def run_blocking(op_name, func, *args, timeout_sec=None, **kwargs):
        return func(*args, **kwargs)

    return SimpleNamespace(
        _enable_rag=True,
        _enable_skill_learning=False,
        _multiturn_n=5,
        _rag_top_k=2,
        _llm_timeout_sec=12.0,
        _llm=SimpleNamespace(chat=Mock(return_value="주방 위치")),
        _memory=SimpleNamespace(
            get_conversation_history=Mock(return_value=[]),
            search_knowledge=Mock(return_value=[]),
        ),
        _skills=SimpleNamespace(mock_robot=True, _skills={}),
        _run_blocking=AsyncMock(side_effect=run_blocking),
        _get_robot_health_state=lambda summary: {"mocked": True},
        get_logger=lambda: Mock(),
    )


def collect(node, instruction):
    goal = SimpleNamespace(request=SimpleNamespace(context_json=""))
    return asyncio.run(LLMPlanner(node).collect_task_context(instruction, goal))[0]


@pytest.mark.parametrize("current_in_history", [False, True])
def test_refinement_uses_history_and_disables_thinking(node, current_in_history):
    instruction = "거기로 가줘"
    history = [
        {"role": "user", "content": "주방이 어디야?"},
        {"role": "assistant", "content": "주방은 복도 끝에 있습니다."},
    ]
    if current_in_history:
        history.append({"role": "user", "content": instruction})
    original = deepcopy(history)
    node._memory.get_conversation_history.return_value = history

    messages = collect(node, instruction)

    request = node._llm.chat.call_args
    assert request.args[0] == original[:2] + [{"role": "user", "content": instruction}]
    assert request.kwargs["think"] is False
    assert "검색 쿼리" in request.kwargs["system_prompt"]
    assert "대화" in request.kwargs["system_prompt"]
    assert "명령을 실행" in request.kwargs["system_prompt"]
    assert messages[1:] == request.args[0]
    assert history == original
    node._memory.get_conversation_history.assert_called_once_with(5)
    assert node._memory.search_knowledge.call_args.args[:2] == ("주방 위치", 2)
    assert node._run_blocking.call_args_list[0].kwargs["timeout_sec"] == 12.0


@pytest.mark.parametrize("response", ["", "  ", ' "주방 위치" '])
def test_refinement_without_history_keeps_original_fallback(node, response):
    node._llm.chat.return_value = response

    collect(node, "주방으로 가줘")

    assert node._llm.chat.call_args.args[0] == [{"role": "user", "content": "주방으로 가줘"}]
    expected_query = "주방 위치" if response.strip() else "주방으로 가줘"
    assert node._memory.search_knowledge.call_args.args[0] == expected_query


@pytest.mark.parametrize("error", [TimeoutError("timeout"), RuntimeError("offline")])
def test_refinement_failure_searches_original_instruction(node, error):
    node._llm.chat.side_effect = error

    messages = collect(node, "주방으로 가줘")

    assert node._memory.search_knowledge.call_args.args[0] == "주방으로 가줘"
    assert messages[-1] == {"role": "user", "content": "주방으로 가줘"}


def test_disabled_rag_does_not_refine_query(node):
    node._enable_rag = False

    collect(node, "상태 알려줘")

    node._llm.chat.assert_not_called()
    node._memory.search_knowledge.assert_not_called()
