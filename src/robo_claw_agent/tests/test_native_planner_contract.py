import json
import logging
from copy import deepcopy
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.planner import LLMPlanner
from robo_claw_agent.llm_bridge import ToolCall, ToolTurn
from robo_claw_agent.skill_manager import SkillResult
from robo_claw_agent.skills.tool_specs import build_tool_specs

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class Node:
    _llm_tool_calling = True
    _enable_skill_learning = False

    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []
        self.executed = []
        self._llm = SimpleNamespace(supports_tools=lambda: True, chat_with_tools=self.chat)
        self._skills = SimpleNamespace(execute=self.execute)
        self.policy_error = None

    def chat(self, messages, **kwargs):
        self.requests.append(deepcopy(messages))
        return next(self.turns)

    def execute(self, name, params, timeout):
        self.executed.append((name, params))
        return SkillResult(name, True, f"[MOCK] {name}", 0.01, {"mocked": True})

    def get_logger(self):
        return logging.getLogger("native_contract")

    def _set_state(self, *args):
        pass

    def _build_system_prompt(self):
        return "system"

    def _tool_specs(self):
        return build_tool_specs(
            [{"name": n} for n in ["get_location", "get_status", "navigate_to"]]
        )

    def _validate_skill_chain(self, chain):
        return self.policy_error

    async def _run_blocking(self, op, func, *args, **kwargs):
        return func(*args, **kwargs)


async def run(node, instruction="거실 위치를 알려줘."):
    return await LLMPlanner(node).run_llm_planning_loop(
        SimpleNamespace(),
        instruction,
        [{"role": "user", "content": instruction}],
        {},
        5,
        lambda *a: None,
    )


async def test_location_final_answer_does_not_repeat_tool():
    node = Node(
        [
            ToolTurn(
                tool_calls=[ToolCall(name="get_location", arguments={"location_name": "거실"})]
            ),
            ToolTurn(content="모의 실행이라 좌표를 확인할 수 없습니다."),
        ]
    )
    result, results, success, _ = await run(node)
    assert success
    assert result == "모의 실행이라 좌표를 확인할 수 없습니다."
    assert node.executed == [("get_location", {"location_name": "거실"})]
    assert len(results) == 1


async def test_native_direct_answer_does_not_force_motion():
    node = Node([ToolTurn(content="목적지가 불명확합니다. 어느 거실인가요?")])
    result, _, success, _ = await run(node, "거실로 이동해")
    assert success
    assert result == "목적지가 불명확합니다. 어느 거실인가요?"
    assert node.executed == []


async def test_native_selected_tool_is_not_replaced_by_location_rule():
    node = Node(
        [
            ToolTurn(tool_calls=[ToolCall(name="get_status")]),
            ToolTurn(content="상태 조회를 마쳤습니다."),
        ]
    )
    await run(node)
    assert node.executed == [("get_status", {})]


async def test_policy_rejection_returns_matching_tool_result():
    node = Node(
        [
            ToolTurn(tool_calls=[ToolCall(name="get_location", id="call-location")]),
            ToolTurn(content="정책에 따라 실행하지 않았습니다."),
        ]
    )
    node.policy_error = "blocked by policy"
    await run(node)
    assert node.executed == []
    followup = node.requests[1]
    assert [m["tool_call_id"] for m in followup if m["role"] == "tool"] == ["call-location"]
    assert any(m.get("tool_calls") for m in followup)


async def test_unknown_tool_returns_error_instead_of_becoming_final_answer():
    node = Node(
        [
            ToolTurn(tool_calls=[ToolCall(name="unregistered", id="bad")]),
            ToolTurn(content="사용 가능한 도구가 없습니다."),
        ]
    )
    result, _, _, _ = await run(node, "상태 조회")
    assert result == "사용 가능한 도구가 없습니다."
    assert node.executed == []
    assert any(m.get("tool_call_id") == "bad" for m in node.requests[1])


async def test_safety_prelude_is_reported_without_unrequested_execution(monkeypatch):
    from robo_claw_agent.agent_node.planner import llm_planner

    node = Node(
        [
            ToolTurn(
                tool_calls=[
                    ToolCall(name="navigate_to", arguments={"target_name": "거실"}, id="nav")
                ]
            ),
            ToolTurn(content="주행 준비가 필요합니다."),
        ]
    )
    monkeypatch.setattr(
        llm_planner, "ensure_navigation_safety", lambda chain, *args: [{"skill": "stow"}] + chain
    )
    result, _, success, _ = await run(node, "거실로 이동해")
    assert success is False
    assert node.executed == []
    assert result == "주행 준비가 필요합니다."
    assert "주행 안전 준비" in node.requests[1][-1]["content"]


async def test_native_loop_is_bounded_and_preserves_partial_execution():
    node = Node([ToolTurn(tool_calls=[ToolCall(name="get_status")]) for _ in range(5)])
    result, results, success, _ = await run(node, "상태 확인")
    assert success is False
    assert "횟수 초과" in result
    assert len(results) == 5
    ids = [m["tool_call_id"] for m in node.requests[-1] if m["role"] == "tool"]
    assert len(ids) == len(set(ids))


async def test_model_timeout_reports_failure_after_partial_execution():
    node = Node([ToolTurn(tool_calls=[ToolCall(name="get_status")])])
    original = node.chat

    def chat(messages, **kwargs):
        if node.executed:
            raise TimeoutError("model timeout")
        return original(messages, **kwargs)

    node._llm.chat_with_tools = chat
    result, results, success, _ = await run(node, "상태 확인")
    assert success is False
    assert "model timeout" in result
    assert len(results) == 1


async def test_cancelled_native_request_does_not_call_model_or_tools():
    node = Node([])
    result, results, success, _ = await LLMPlanner(node).run_llm_planning_loop(
        SimpleNamespace(is_cancel_requested=True), "상태 확인", [], {}, 5, lambda *args: None
    )
    assert not success
    assert not results
    assert not node.requests
    assert not node.executed
    assert "취소" in result


@pytest.mark.parametrize("mock_robot", [False, True])
async def test_mock_manager_does_not_record_experience_without_payload_marker(mock_robot):
    from unittest.mock import Mock

    node = Node(
        [ToolTurn(tool_calls=[ToolCall(name="get_status")]), ToolTurn(content="상태 확인 완료")]
    )
    node._enable_skill_learning = True
    node._memory = SimpleNamespace(record_skill_episode=Mock())
    node._skills.mock_robot = mock_robot
    node._skills.execute = lambda name, params, timeout: SkillResult(name, True, "완료", 0.01, {})
    await run(node, "상태 확인")
    assert node._memory.record_skill_episode.call_count == (0 if mock_robot else 1)


# ── 정책 prelude(내부 안전 스킬) 실행 ─────────────────────────────────────────


def _internal_prelude_before(chain, *args):
    """CLOiD 주행 전 자세처럼 LLM 도구 목록에 없는 prelude 를 삽입한다."""
    from robo_claw_agent.agent_node.nav_safety import POLICY_PRELUDE_FLAG

    return [
        {"skill": "cloid_navigation_pose", "params": {}, POLICY_PRELUDE_FLAG: True},
        *chain,
    ]


async def test_internal_prelude_is_executed_instead_of_deadlocking(monkeypatch):
    """LLM 이 호출할 수 없는 내부 안전 스킬은 게이트가 대신 실행해야 한다.

    도구 목록에 없는 prelude 를 "먼저 호출하세요" 로 되돌리면 모델은 영원히
    만족시킬 수 없고, 이동 요청이 매번 실패한다(CLOiD 2호기 주방 이동 사례).
    """
    from robo_claw_agent.agent_node.planner import llm_planner

    node = Node(
        [
            ToolTurn(
                tool_calls=[
                    ToolCall(name="navigate_to", arguments={"target_name": "주방"}, id="nav")
                ]
            ),
            ToolTurn(content="주방으로 이동했습니다."),
        ]
    )
    monkeypatch.setattr(llm_planner, "ensure_navigation_safety", _internal_prelude_before)

    result, _, success, _ = await run(node, "주방으로 이동해")

    assert success is True
    assert node.executed == [
        ("cloid_navigation_pose", {}),
        ("navigate_to", {"target_name": "주방"}),
    ]
    assert result == "주방으로 이동했습니다."
    tool_messages = [m for m in node.requests[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["nav"]
    payload = json.loads(tool_messages[0]["content"])
    assert payload["navigation_safety_prelude"] == [
        {
            "skill": "cloid_navigation_pose",
            "success": True,
            "message": "[MOCK] cloid_navigation_pose",
        }
    ]


async def test_prelude_does_not_shift_tool_call_result_mapping(monkeypatch):
    """prelude 는 tool_call 을 소모하지 않으므로 결과가 한 칸씩 밀리면 안 된다."""
    from robo_claw_agent.agent_node.nav_safety import POLICY_PRELUDE_FLAG
    from robo_claw_agent.agent_node.planner import llm_planner

    def insert_only_before_navigation(chain, *args):
        protected = []
        for item in chain:
            if item.get("skill") == "navigate_to":
                protected.append(
                    {"skill": "cloid_navigation_pose", "params": {}, POLICY_PRELUDE_FLAG: True}
                )
            protected.append(item)
        return protected

    node = Node(
        [
            ToolTurn(
                tool_calls=[
                    ToolCall(name="get_status", id="status"),
                    ToolCall(name="navigate_to", arguments={"target_name": "주방"}, id="nav"),
                ]
            ),
            ToolTurn(content="이동했습니다."),
        ]
    )
    monkeypatch.setattr(llm_planner, "ensure_navigation_safety", insert_only_before_navigation)

    _, _, success, _ = await run(node, "주방으로 이동해")

    assert success is True
    tool_messages = [m for m in node.requests[1] if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["tool_name"]) for m in tool_messages] == [
        ("status", "get_status"),
        ("nav", "navigate_to"),
    ]


async def test_internal_prelude_failure_blocks_execution_and_reports_reason(monkeypatch):
    """prelude 가 실패하면 요청한 이동은 실행하지 않고, 남은 도구 호출도 실패로 보고한다."""
    from robo_claw_agent.agent_node.planner import llm_planner

    node = Node(
        [
            ToolTurn(
                tool_calls=[
                    ToolCall(name="navigate_to", arguments={"target_name": "주방"}, id="nav")
                ]
            ),
            ToolTurn(content="주행 전 자세 준비가 실패해 이동하지 못했습니다."),
        ]
    )

    def execute(name, params, timeout):
        node.executed.append((name, params))
        return SkillResult(name, False, "script_runner 응답 없음", 0.01, {"mocked": True})

    node._skills.execute = execute
    monkeypatch.setattr(llm_planner, "ensure_navigation_safety", _internal_prelude_before)

    result, _, success, _ = await run(node, "주방으로 이동해")

    assert success is False
    assert node.executed == [("cloid_navigation_pose", {})]
    tool_messages = [m for m in node.requests[1] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["nav"]
    assert json.loads(tool_messages[0]["content"])["error_type"] == "NotExecuted"
    assert "cloid_navigation_pose" in node.requests[1][-1]["content"]
