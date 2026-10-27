"""현재 지시문이 LLM 요청에 두 번 들어가는 것의 회귀 테스트.

execution.py 의 _handle_execute_task 는 태스크를 받는 즉시 "task_received"
이벤트를 에피소드 메모리에 기록한다. memory_manager/episodic.py 의
get_conversation_history() 는 그 이벤트를 user 메시지로 되살리므로, 현재
지시문은 이미 대화 이력의 마지막 user 메시지로 들어 있다.

planner/llm_planner.py 의 collect_task_context() 가 이력에 현재 지시문을
한 번 더 붙이면 LLM 요청에 같은 메시지가 두 번 들어가므로, 이력에 이미
있을 때는 중복 추가하지 않아야 한다.
"""

import asyncio
import logging
from typing import Any

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.agent_node.planner import LLMPlanner


class _FakeMemory:
    """get_conversation_history만 구현한 메모리 스텁."""

    def __init__(self, history: list[dict[str, str]]) -> None:
        self._history = history

    def get_conversation_history(self, n: int = 10) -> list[dict[str, str]]:
        return list(self._history)


class _FakeSkillManager:
    def __init__(self) -> None:
        self._skills: dict[str, Any] = {}
        self.mock_robot = True


class _FakeNode:
    def __init__(self, history: list[dict[str, str]]) -> None:
        self._llm = object()  # truthy 면 됨 (호출은 collect_task_context 밖)
        self._memory = _FakeMemory(history)
        self._skills = _FakeSkillManager()
        self._enable_rag = False
        self._enable_skill_learning = False
        self._multiturn_n = 5
        self._logger = logging.getLogger("test_llm_planner_message_dedup")

    def get_logger(self):
        return self._logger

    def _get_robot_health_state(self, robot_summary: dict = None) -> dict:
        return {"mocked": True, "hardware_connected": False}

    async def _run_blocking(self, op_name: str, func, *args, **kwargs):
        return func(*args, **kwargs)


class _FakeGoalHandle:
    class _Request:
        context_json = ""

    request = _Request()


def _collect(node: _FakeNode, instruction: str) -> list[dict[str, str]]:
    planner = LLMPlanner(node)
    messages, _ = asyncio.run(
        planner.collect_task_context(instruction, _FakeGoalHandle())
    )
    return messages


def _user_contents(messages: list[dict[str, str]]) -> list[str]:
    return [m["content"] for m in messages if m.get("role") == "user"]


def test_current_instruction_already_in_history_is_not_duplicated():
    """현재 지시문이 이력에 이미 있으면 한 번만 들어가야 한다."""
    instruction = "이것도 두번이니?"
    node = _FakeNode(
        [
            {"role": "user", "content": "점심 메뉴 추천 부탁합니다."},
            {"role": "assistant", "content": "메뉴 추천은 할 수 없습니다"},
            {"role": "user", "content": instruction},
        ]
    )

    user_contents = _user_contents(_collect(node, instruction))

    assert user_contents.count(instruction) == 1
    assert user_contents == ["점심 메뉴 추천 부탁합니다.", instruction]


def test_current_instruction_absent_from_history_is_appended_once():
    """이력에 없던 지시문은 한 번만 추가되어야 한다."""
    instruction = "상태 알려줘"
    node = _FakeNode([{"role": "user", "content": "이전 질문"}])

    user_contents = _user_contents(_collect(node, instruction))

    assert user_contents.count(instruction) == 1
    assert user_contents == ["이전 질문", instruction]


def test_history_ending_with_assistant_still_gets_current_instruction():
    """이력의 마지막이 assistant 면 현재 지시문을 붙여야 한다."""
    instruction = "상태 알려줘"
    node = _FakeNode(
        [
            {"role": "user", "content": "이전 질문"},
            {"role": "assistant", "content": "이전 답변"},
        ]
    )

    user_contents = _user_contents(_collect(node, instruction))

    assert user_contents.count(instruction) == 1
    assert user_contents == ["이전 질문", instruction]


def test_same_text_in_earlier_turn_is_kept():
    """이전 턴에 같은 문구가 있어도 이력은 그대로 유지되어야 한다."""
    instruction = "같은 말"
    node = _FakeNode(
        [
            {"role": "user", "content": instruction},
            {"role": "assistant", "content": "이전 답변"},
        ]
    )

    user_contents = _user_contents(_collect(node, instruction))

    # 이전 턴의 같은 문구 1개 + 현재 지시문 1개
    assert user_contents == [instruction, instruction]
    assert user_contents[-1] == instruction
