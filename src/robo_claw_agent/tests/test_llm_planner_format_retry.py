"""LLMPlanner.run_llm_planning_loop 의 형식 교정 재시도 게이트 통합 테스트.

utils.py의 parse_llm_plan은 이미 단위 테스트가 있지만, "형식 교정 재시도를
언제 트리거하는가"는 planner.py의 루프 안에서만 결정된다(§B). 기존
test_task_planner.py는 이 루프를 통째로 mock하므로 실제로 검증된 적이 없다.

이 파일은 LLMPlanner를 최소 의존성 fake node로 인스턴스화해 실제 루프를 돌린다.
"""

import pytest

pytest.importorskip("rclpy")

import logging
from typing import Any

from robo_claw_agent.agent_node.planner import LLMPlanner
from robo_claw_agent.skill_manager import SkillResult
from robo_claw_agent.types import AgentState


class _FakeSkillManager:
    """nav_safety/_validate_skill_chain이 참조하는 최소 스킬 매니저 스텁."""

    def get_skill(self, name: str) -> Any:
        return None  # stow_for_navigation 미등록 취급 -> nav_safety no-op

    def execute(self, name: str, params: dict, timeout: float) -> SkillResult:
        return SkillResult(name, True, f"{name} 실행 완료", 0.01, {})


class _FakeLLM:
    """준비된 응답을 순서대로 반환하는 가짜 LLM. chat()은 동기 함수(실제 브릿지와 동일)."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, system_prompt=None, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        if not self._responses:
            raise AssertionError("LLM 응답이 준비된 것보다 더 많이 호출되었습니다")
        return self._responses.pop(0)


class _FakeNode:
    """run_llm_planning_loop/execute_skill_chain이 필요로 하는 node 표면만 구현."""

    def __init__(self, responses: list[str]) -> None:
        self._llm = _FakeLLM(responses)
        self._skills = _FakeSkillManager()
        self._enable_skill_learning = False
        self.states: list[tuple[Any, str, str]] = []
        self._logger = logging.getLogger("test_llm_planner_format_retry")

    def get_logger(self):
        return self._logger

    def _set_state(self, state, skill: str = "", msg: str = "") -> None:
        self.states.append((state, skill, msg))

    def _build_system_prompt(self) -> str:
        return "system prompt"

    async def _run_blocking(self, op_name: str, func, *args, **kwargs):
        return func(*args, **kwargs)

    def _validate_skill_chain(self, chain):
        return None

    def _start_channel_send(self, msg_text, file_path):
        raise AssertionError("이 테스트 시나리오에서는 파일 전송이 없어야 합니다")


class _FakeGoalHandle:
    class _Request:
        context_json = ""

    request = _Request()


def _run(node: _FakeNode, instruction: str = "상태 알려줘"):
    import asyncio

    planner = LLMPlanner(node)
    return asyncio.run(
        planner.run_llm_planning_loop(
            _FakeGoalHandle(), instruction, [], {}, 30.0, lambda *a, **kw: None
        )
    )


# ── 산문 1회 → 형식 교정 → 두 번째 응답이 정상 JSON → 성공 (§B 핵심 검증) ──


def test_prose_response_triggers_retry_then_succeeds_on_valid_json():
    node = _FakeNode(
        [
            "거실로 이동하겠습니다.",  # 1회차: 파싱 실패 -> parse_failed=True -> 재시도
            '{"skill": "get_status", "params": {}, "reason": "상태 확인"}',  # 2회차: 정상
        ]
    )

    result_msg, skill_results, task_success, _ = _run(node)

    assert task_success is True
    assert len(skill_results) == 1
    assert skill_results[0].skill_name == "get_status"
    assert len(node._llm.calls) == 2, "형식 교정 재시도로 LLM이 2번 호출되어야 한다"
    # 재시도 메시지가 실제로 교정 지시를 담고 있는지 확인
    second_call_messages = node._llm.calls[1]["messages"]
    assert any("JSON 형식이 아닙니다" in m.get("content", "") for m in second_call_messages)


# ── 산문이 재시도 예산 내내 반복 → 그 산문을 최종 답변으로 사용 (사용자 확정 폴백) ──


def test_prose_response_repeated_through_retry_budget_is_used_as_final_answer():
    node = _FakeNode(
        [
            "1차 산문 응답",
            "2차 산문 응답",
            "3차 산문 응답",  # max_format_retries=2 이므로 총 3회(최초+재시도 2회) 소모
        ]
    )

    result_msg, skill_results, task_success, _ = _run(node)

    assert task_success is True, "재시도를 다 썼어도 산문을 답변으로 써서 성공 처리해야 한다"
    assert result_msg == "3차 산문 응답"
    assert skill_results == []
    assert len(node._llm.calls) == 3


# ── 정상 최종 답변(parse_failed=False)은 재시도 없이 즉시 종료 ─────────────


def test_explicit_final_response_does_not_trigger_retry():
    node = _FakeNode(['{"skill": null, "response": "안녕하세요, 무엇을 도와드릴까요?"}'])

    result_msg, skill_results, task_success, _ = _run(node)

    assert task_success is True
    assert result_msg == "안녕하세요, 무엇을 도와드릴까요?"
    assert len(node._llm.calls) == 1, "의도된 최종 답변은 재시도하지 않아야 한다"


# ── 정상 skills 응답은 즉시 실행되고 재시도가 발생하지 않는다(회귀 확인) ──


def test_valid_skill_response_executes_without_retry():
    node = _FakeNode(['{"skill": "get_status", "params": {}, "reason": "상태 확인"}'])

    result_msg, skill_results, task_success, _ = _run(node)

    assert task_success is True
    assert len(skill_results) == 1
    assert len(node._llm.calls) == 1


# ── 빈 skills + final=False (예: tool_calls: []) 도 여전히 재시도 대상 (회귀 확인) ──


def test_empty_tool_calls_triggers_retry_then_succeeds():
    node = _FakeNode(
        [
            '{"tool_calls": []}',
            '{"skill": "get_status", "params": {}}',
        ]
    )

    result_msg, skill_results, task_success, _ = _run(node)

    assert task_success is True
    assert len(node._llm.calls) == 2


# ── AgentState 전이도 그대로 발생하는지 확인 ───────────────────────────────


def test_error_state_not_set_when_prose_falls_back_to_final_answer():
    """재시도 소진 후 산문을 답변으로 쓰는 경로는 실패가 아니므로 ERROR 상태를 거치지 않는다."""
    node = _FakeNode(["산문1", "산문2", "산문3"])

    _run(node)

    assert AgentState.ERROR not in [s[0] for s in node.states]
