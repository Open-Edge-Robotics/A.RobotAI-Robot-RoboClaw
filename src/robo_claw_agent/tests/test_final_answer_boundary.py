"""최종 답변 경계(ExecutionMixin)의 새니타이즈 검증.

LLM/스킬 결과는 ``ExecuteTask.Result.result_message`` 로 채널(HTTP/gRPC/ROS
action/메신저)에 전달된다. 이 경계에서 JSON 원문·파이썬 repr·계획 envelope 이
사용자에게 그대로 노출되지 않아야 한다.
"""

import asyncio
import logging

import pytest

pytest.importorskip("rclpy")
pytest.importorskip("robo_claw_msgs.action")

from robo_claw_agent.agent_node.execution import ExecutionMixin
from robo_claw_agent.answer import UNRENDERABLE_ANSWER
from robo_claw_agent.types import AgentState


class _FakeMemory:
    def add_event(self, *args, **kwargs) -> None:
        pass


class _FakeGoalHandle:
    class _Request:
        context_json = ""

    request = _Request()

    def succeed(self) -> None:
        pass

    def abort(self) -> None:
        pass

    def publish_feedback(self, fb) -> None:
        pass


class _Clock:
    class _Now:
        def to_msg(self):
            return object()

    def now(self):
        return self._Now()


class _Node(ExecutionMixin):
    def __init__(self, inner_result) -> None:
        self._memory = _FakeMemory()
        self._state = AgentState.IDLE
        self._agent_id = "test_agent"
        self._clock = _Clock()
        self._inner_result = inner_result
        self._logger = logging.getLogger("test_final_answer_boundary")

    def get_logger(self):
        return self._logger

    def get_clock(self):
        return self._clock

    def _set_state(self, state, skill: str = "", msg: str = "") -> None:
        self._state = state

    async def _execute_task_inner(self, goal_handle, instruction, timeout):
        return self._inner_result


def _result_message(inner_result) -> str:
    node = _Node(inner_result)
    result = asyncio.run(node._execute_task_direct(_FakeGoalHandle(), "질문", 30.0))
    return result.result_message


def test_plain_answer_passes_through():
    assert _result_message(("도착했습니다", [], True)) == "도착했습니다"


def test_envelope_answer_is_unwrapped_at_boundary():
    raw = '{"skill": null, "response": "수신 가능한 토픽은 2개입니다."}'
    assert _result_message((raw, [], True)) == "수신 가능한 토픽은 2개입니다."


def test_structured_json_is_not_shown_verbatim():
    raw = '{"topics": ["/cmd_vel", "/scan"], "count": 2}'
    message = _result_message((raw, [], True))

    assert message == "topics: /cmd_vel, /scan; count: 2"
    assert "{" not in message and "}" not in message
    assert "'" not in message


def test_plan_envelope_without_answer_is_not_leaked():
    raw = '{"skill": "ros_command", "params": {"command": "ros2 topic list"}, "reason": "조회"}'
    assert _result_message((raw, [], True)) == UNRENDERABLE_ANSWER


def test_empty_result_message_becomes_default():
    assert _result_message(("", [], True)) == "완료"


def test_think_residue_is_stripped_at_boundary():
    open_tok = chr(60) + "think" + chr(62)
    close_tok = chr(60) + "/think" + chr(62)
    raw = f"{open_tok}고민중{close_tok}" + '{"skill": null, "response": "토픽은 2개입니다."}'
    assert _result_message((raw, [], True)) == "토픽은 2개입니다."
