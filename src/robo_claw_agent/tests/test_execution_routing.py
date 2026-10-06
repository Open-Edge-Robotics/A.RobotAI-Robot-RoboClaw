"""ExecutionMixin 의 라우팅/성공 판정 회귀 테스트.

두 가지를 고정한다.

1. 복합 명령은 다이렉트 스킬 라우팅으로 선점되지 않는다. 선점되면 단일 스킬로
   축약돼 나머지 절("회의실로 이동한 **다음** 컵을 집어줘"의 이동)이 조용히 사라진다.
2. 중간에 실패한 스킬이 재계획으로 복구됐다면 전체 태스크를 실패로 뒤집지 않는다.
   과거 ``all(r.success ...)`` 판정은 복구된 실패까지 실패로 만들었고, 단계가 많은
   분해 경로에서는 그 확률이 단계 수만큼 곱해졌다.
"""

import asyncio
import logging
from typing import Any

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.agent_node.execution import ExecutionMixin
from robo_claw_agent.skill_manager import SkillResult
from robo_claw_agent.types import AgentState


class _FakeSkillManager:
    def __init__(self) -> None:
        self.executed: list[tuple[str, dict]] = []

    def get_skill(self, name: str) -> Any:
        return None

    def execute(self, name: str, params: dict, timeout_sec: float = 0.0) -> SkillResult:
        self.executed.append((name, params))
        return SkillResult(name, True, f"{name} 완료", 0.01, {})


class _FakePlanner:
    """collect_task_context / run_llm_planning_loop 만 흉내낸다."""

    def __init__(self, loop_result: tuple) -> None:
        self.loop_result = loop_result
        self.loop_calls: list[str] = []

    async def collect_task_context(self, instruction: str, goal_handle: Any):
        return [{"role": "user", "content": instruction}], {}

    async def run_llm_planning_loop(
        self, goal_handle, instruction, messages, robot_summary, timeout, send_fb
    ):
        self.loop_calls.append(instruction)
        return self.loop_result


class _FakeDecomposer:
    def __init__(self, result: tuple) -> None:
        self.result = result
        self.calls: list[str] = []

    def looks_compound(self, instruction: str) -> bool:
        from robo_claw_agent.agent_node.task_planner import looks_compound

        return looks_compound(instruction)

    async def run(self, goal_handle, instruction, messages, robot_summary, timeout, send_fb):
        self.calls.append(instruction)
        return self.result


class _FakeMemory:
    def add_event(self, *args, **kwargs) -> None:
        pass


class _FakeMetrics:
    def record(self, *args, **kwargs) -> None:
        pass


class _FakeGoalHandle:
    class _Request:
        context_json = ""

    request = _Request()

    def publish_feedback(self, fb) -> None:
        pass


class _Node(ExecutionMixin):
    def __init__(self, planner, decomposer) -> None:
        self._planner = planner
        self._task_decomposer = decomposer
        self._skills = _FakeSkillManager()
        self._memory = _FakeMemory()
        self._runtime_metrics = _FakeMetrics()
        self._llm = object()
        self._enable_task_decomposition = True
        self._enable_skill_learning = False
        self._state = AgentState.IDLE
        self._agent_id = "test_agent"
        self._logger = logging.getLogger("test_execution_routing")

    def get_logger(self):
        return self._logger

    def _set_state(self, state, skill: str = "", msg: str = "") -> None:
        self._state = state


def _run(node: _Node, instruction: str):
    return asyncio.run(node._execute_task_inner(_FakeGoalHandle(), instruction, 30.0))


# ── 1. 복합 명령의 다이렉트 라우팅 선점 방지 ──────────────────────────────


def test_compound_instruction_skips_direct_routing():
    """'이동한 다음 집어줘'는 파지 스킬 단독으로 축약되면 안 된다."""
    decomposer = _FakeDecomposer(("모든 단계 완료", [], True, []))
    node = _Node(_FakePlanner(("", [], True, [])), decomposer)

    _, _, task_success = _run(node, "회의실로 이동한 다음 컵을 집어줘")

    assert task_success is True
    assert decomposer.calls == ["회의실로 이동한 다음 컵을 집어줘"]
    assert node._skills.executed == [], "다이렉트 스킬이 실행되면 이동 절이 사라진다"


def test_simple_instruction_still_uses_direct_routing():
    """단일 명령의 다이렉트 라우팅(지연·환각 회피)은 그대로 유지된다."""
    decomposer = _FakeDecomposer(("", [], True, []))
    planner = _FakePlanner(("", [], True, []))
    node = _Node(planner, decomposer)

    _, skill_results, task_success = _run(node, "앞으로 2미터 가줘")

    assert task_success is True
    assert [name for name, _ in node._skills.executed] == ["move_relative"]
    assert skill_results[0].skill_name == "move_relative"
    assert decomposer.calls == []
    assert planner.loop_calls == []


# ── 2. 복구된 중간 실패는 전체 실패로 뒤집지 않는다 ────────────────────────


def test_recovered_mid_chain_failure_is_not_reported_as_failure():
    failed = SkillResult("navigate_to", False, "경로 없음", 0.1, {})
    recovered = SkillResult("navigate_to", True, "도착", 0.2, {})
    planner = _FakePlanner(("도착했습니다", [failed, recovered], True, []))
    node = _Node(planner, _FakeDecomposer(("", [], True, [])))

    result_msg, skill_results, task_success = _run(node, "주방으로 이동해줘")

    assert task_success is True, "재계획으로 복구했으면 성공으로 보고해야 한다"
    assert result_msg == "도착했습니다"
    assert len(skill_results) == 2


def test_unrecovered_final_failure_is_reported_as_failure():
    ok = SkillResult("stow_for_navigation", True, "접음", 0.1, {})
    failed = SkillResult("navigate_to", False, "경로 없음", 0.2, {})
    planner = _FakePlanner(("이동 실패", [ok, failed], True, []))
    node = _Node(planner, _FakeDecomposer(("", [], True, [])))

    _, _, task_success = _run(node, "주방으로 이동해줘")

    assert task_success is False
