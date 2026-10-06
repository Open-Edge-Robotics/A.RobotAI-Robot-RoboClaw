"""정보성 스킬의 '결과 요약 답변 라운드' 동작 검증.

기존에는 스킬 체인이 성공하면 곧바로 루프를 종료하고 마지막 스킬의 ``message`` 를
사용자 답변으로 썼다. ``ros_command`` 처럼 message 가 "실행 완료"인 스킬은 실제
결과 데이터(result_data)가 사용자에게 전달되지 못했고, "수신 가능한 토픽 목록
보여줘" 같은 조회 요청이 "실행 완료"로만 답해졌다.

이 파일은 정보성 스킬(answer_mode="informational")일 때만 결과를 LLM에 넘겨 최종
답변을 합성하고, 물리 동작(action) 스킬은 기존처럼 즉시 종료하는 계약을 고정한다.
"""

import pytest

pytest.importorskip("rclpy")

import logging
from typing import Any

from robo_claw_agent.agent_node.planner import LLMPlanner
from robo_claw_agent.skill_manager import SkillResult


class _FakeSkill:
    """플래너가 읽는 스킬 메타데이터만 제공하는 스텁."""

    def __init__(self, answer_mode: str, risk_level: str = "read") -> None:
        self.answer_mode = answer_mode
        self.risk_level = risk_level
        self.terminal_behavior = "terminal"

    def check_preconditions(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def validate_input_schema(self, params: dict) -> tuple[bool, str]:
        return True, ""

    def validate_params(self, params: dict) -> bool:
        return True


class _FakeSkillManager:
    def __init__(self, modes: dict[str, str]) -> None:
        self._modes = modes
        self.executed: list[str] = []

    def get_skill(self, name: str) -> Any:
        mode = self._modes.get(name)
        if mode is None:
            return None
        return _FakeSkill(mode, risk_level="read" if mode == "informational" else "action")

    def execute(self, name: str, params: dict, timeout: float) -> SkillResult:
        self.executed.append(name)
        return SkillResult(
            name,
            True,
            "실행 완료",
            0.01,
            {"command": "ros2 topic list", "stdout": "/cmd_vel\n/scan", "returncode": 0},
        )


class _FakeLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, system_prompt=None, **kwargs):
        self.calls.append({"messages": list(messages), "kwargs": kwargs})
        if not self._responses:
            raise AssertionError("LLM 응답이 준비된 것보다 더 많이 호출되었습니다")
        return self._responses.pop(0)


class _FakeNode:
    def __init__(self, responses: list[str], modes: dict[str, str]) -> None:
        self._llm = _FakeLLM(responses)
        self._skills = _FakeSkillManager(modes)
        self._enable_skill_learning = False
        self._logger = logging.getLogger("test_readonly_answer_synthesis")

    def get_logger(self):
        return self._logger

    def _set_state(self, state, skill: str = "", msg: str = "") -> None:
        pass

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


def _run(responses, modes, instruction="수신 가능한 토픽 목록 보여줘"):
    import asyncio

    node = _FakeNode(responses, modes)
    planner = LLMPlanner(node)
    result = asyncio.run(
        planner.run_llm_planning_loop(
            _FakeGoalHandle(), instruction, [], {}, 30.0, lambda *a, **kw: None
        )
    )
    return node, result


# ── 정보성 스킬: 결과 요약 라운드가 실행된다 ──────────────────────────────


def test_informational_skill_triggers_answer_round_with_result_data():
    node, (result_msg, skill_results, ok, _) = _run(
        [
            '{"skill": "ros_command", "params": {"command": "ros2 topic list"}, "reason": "조회"}',
            '{"skill": null, "response": "수신 가능한 토픽은 /cmd_vel, /scan 2개입니다."}',
        ],
        {"ros_command": "informational"},
    )

    assert ok is True
    assert len(node._llm.calls) == 2, "정보성 스킬은 결과 요약을 위해 LLM을 한 번 더 호출해야 한다"
    assert result_msg == "수신 가능한 토픽은 /cmd_vel, /scan 2개입니다."
    assert [r.skill_name for r in skill_results] == ["ros_command"]

    # 요약 라운드 프롬프트에 실제 스킬 결과 데이터가 실려야 한다.
    summary_prompt = "\n".join(str(m.get("content", "")) for m in node._llm.calls[1]["messages"])
    assert "/cmd_vel" in summary_prompt
    assert "스킬 체인 실행 결과" in summary_prompt


def test_informational_chain_keeps_existing_results_and_does_not_reexecute():
    node, (result_msg, skill_results, ok, _) = _run(
        [
            '{"skills": [{"skill": "get_status", "params": {}}, {"skill": "ros_command", "params": {"command": "ros2 topic list"}}]}',
            '{"skill": null, "response": "정리된 답변"}',
        ],
        {"get_status": "informational", "ros_command": "informational"},
    )

    assert ok is True
    assert result_msg == "정리된 답변"
    # 요약 라운드에서 스킬이 다시 실행되지 않는다.
    assert node._skills.executed == ["get_status", "ros_command"]


def test_summary_round_skill_replan_is_not_executed_and_falls_back():
    """요약 라운드가 스킬 계획을 내면 재실행하지 않고 결정론적 폴백으로 종료한다."""
    node, (result_msg, skill_results, ok, _) = _run(
        [
            '{"skill": "ros_command", "params": {"command": "ros2 topic list"}}',
            '{"skill": "ros_command", "params": {"command": "ros2 topic list"}}',
        ],
        {"ros_command": "informational"},
    )

    assert ok is True
    assert node._skills.executed == ["ros_command"], "요약 라운드에서 재실행하면 안 된다"
    assert len(node._llm.calls) == 2
    # 폴백은 실제 결과 데이터를 담은 텍스트여야 한다(JSON 원문/내부 reason 아님).
    assert "/cmd_vel" in result_msg
    assert "{" not in result_msg


# ── 물리 동작 스킬: 기존처럼 즉시 종료(추가 라운드 없음) ────────────────


def test_action_skill_does_not_trigger_answer_round():
    node, (result_msg, skill_results, ok, _) = _run(
        ['{"skill": "navigate_to", "params": {"target_name": "주방"}}'],
        {"navigate_to": "action"},
    )

    assert ok is True
    assert len(node._llm.calls) == 1, "물리 동작 스킬은 추가 LLM 라운드를 만들지 않아야 한다"
    assert result_msg == "실행 완료"  # 스킬 message 그대로
    assert [r.skill_name for r in skill_results] == ["navigate_to"]


def test_mixed_chain_with_action_skill_does_not_trigger_answer_round():
    """정보성 + 물리 동작 혼합 체인은 두 번째 물리 동작 위험을 피해 요약하지 않는다."""
    node, (result_msg, skill_results, ok, _) = _run(
        [
            '{"skills": [{"skill": "get_status", "params": {}}, '
            '{"skill": "navigate_to", "params": {"target_name": "주방"}}]}'
        ],
        {"get_status": "informational", "navigate_to": "action"},
    )

    assert ok is True
    assert len(node._llm.calls) == 1
    assert node._skills.executed == ["get_status", "navigate_to"]


# ── reason 은 사용자 답변이 아니다 ──────────────────────────────────────


def test_reason_is_not_used_as_final_answer_for_action_skill():
    node, (result_msg, skill_results, ok, _) = _run(
        [
            '{"skill": "navigate_to", "params": {"target_name": "주방"}, '
            '"reason": "navigate_to로 이동시킵니다", "final": true}'
        ],
        {"navigate_to": "action"},
    )

    assert ok is True
    assert result_msg == "실행 완료"
    assert "navigate_to로" not in result_msg


# ── 요약 라운드가 답변을 내지 못한 경우의 폴백 ──────────────────────────


def test_summary_round_prose_is_used_as_answer():
    """요약 라운드가 산문으로 답하면(형식 재시도 소진) 그 산문을 답변으로 쓴다."""
    node, (result_msg, skill_results, ok, _) = _run(
        [
            '{"skill": "ros_command", "params": {"command": "ros2 topic list"}}',
            "1차 산문 응답",
            "2차 산문 응답",
            "수신 가능한 토픽은 /cmd_vel, /scan 입니다.",
        ],
        {"ros_command": "informational"},
    )

    assert ok is True
    assert result_msg == "수신 가능한 토픽은 /cmd_vel, /scan 입니다."
    assert len(node._llm.calls) == 4, "요약 라운드 형식 교정 재시도까지 포함한 호출 수"


def test_summary_round_json_never_reaches_user_as_json():
    """요약 라운드가 JSON을 벗어나지 못하면 실행 결과 데이터로 직접 답한다."""
    node, (result_msg, skill_results, ok, _) = _run(
        [
            '{"skill": "ros_command", "params": {"command": "ros2 topic list"}}',
            '{"topics": ["/cmd_vel",',
            '{"topics": ["/cmd_vel",',
            '{"topics": ["/cmd_vel",',
        ],
        {"ros_command": "informational"},
    )

    assert ok is True
    assert "/cmd_vel" in result_msg
    assert "{" not in result_msg and "}" not in result_msg
    assert "returncode" not in result_msg
