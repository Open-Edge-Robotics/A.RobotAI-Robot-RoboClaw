"""Eligibility Gate(분해된 하위 단계의 사전 라우팅 적격성)와 TaskDecomposer 연동 테스트.

실기 회귀(2026-10-08): '카메라 이미지 보내고 분석해줘'가 3단계로 분해됐다. 1단계("촬영")는 혼자
완결되지만 2·3단계("촬영한 이미지를 전송", "전송한 이미지를 분석")는 앞 단계 결과에 의존한다.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from robo_claw_agent.agent_node.eligibility_gate import (
    REASON_COMPOUND,
    REASON_CONDITIONAL,
    REASON_DEPENDS,
    REASON_EMPTY,
    check_step_eligibility,
    step_routing_mode,
)
from robo_claw_agent.agent_node.fast_router import RouteDecision
from robo_claw_agent.agent_node.task_planner import TaskDecomposer

pytestmark = pytest.mark.unit


class TestGateRules:
    @pytest.mark.parametrize(
        "step",
        [
            "카메라로 현재 장면의 이미지를 촬영하세요.",
            "주방으로 이동해",
            "지금 배터리 얼마나 남았어?",
            "카메라 화면 캡처해",  # '화면'의 '면'은 조건이 아니다
            "정면을 바라봐",
            "앞으로 2미터 가줘",
        ],
    )
    def test_self_contained_steps_are_eligible(self, step):
        decision = check_step_eligibility(step)
        assert decision.eligible, decision.reasons

    @pytest.mark.parametrize(
        "step",
        [
            "촬영한 카메라 이미지를 전송하세요.",
            "전송한 카메라 이미지를 분석하세요.",
            "찾은 컵을 집어",
            "방금 저장한 위치로 가",
            "그거 다시 해줘",
            "해당 물체 앞으로 이동해",
        ],
    )
    def test_steps_depending_on_previous_results_are_not_eligible(self, step):
        decision = check_step_eligibility(step)
        assert not decision.eligible
        assert REASON_DEPENDS in decision.reasons

    @pytest.mark.parametrize(
        "step",
        [
            "배터리가 20% 이하면 충전대로 가",
            "사람이 보이면 멈춰",
            "만약 문이 닫혀 있으면 알려줘",
            "물건이 없는 경우 돌아와",
            "도착할 때 알려줘",
        ],
    )
    def test_conditional_steps_are_not_eligible(self, step):
        decision = check_step_eligibility(step)
        assert not decision.eligible
        assert REASON_CONDITIONAL in decision.reasons

    def test_compound_step_is_not_eligible(self):
        decision = check_step_eligibility("주방으로 가서 사진 찍어")
        assert not decision.eligible
        assert REASON_COMPOUND in decision.reasons

    def test_empty_step(self):
        assert check_step_eligibility("  ").reasons == [REASON_EMPTY]

    def test_mode_parsing(self):
        assert step_routing_mode({}) == "shadow"
        assert step_routing_mode({"SYSTEM1_STEP_ROUTING": "ON"}) == "on"
        assert step_routing_mode({"SYSTEM1_STEP_ROUTING": "off"}) == "off"
        assert step_routing_mode({"SYSTEM1_STEP_ROUTING": "maybe"}) == "shadow"


# ── TaskDecomposer 연동 ────────────────────────────────────────────────────


def _direct(skill="capture_camera_image", background=False):
    return RouteDecision(kind="direct_skill", skill=skill, background=background, source="laya")


def _make_node(mode, route=None, router_error=None, direct_result=None):
    node = SimpleNamespace()
    node._llm = MagicMock()
    node._llm.chat = MagicMock(
        return_value=json.dumps(
            {"steps": [{"instruction": "카메라로 촬영해"}, {"instruction": "촬영한 이미지를 전송해"}]}
        )
    )
    node._task_decomposition_max_steps = 6
    node._task_step_max_retries = 0
    node.logs = []
    logger = MagicMock()
    logger.info.side_effect = node.logs.append
    node.get_logger = MagicMock(return_value=logger)

    async def _run_blocking(op_name, func, *args, **kwargs):
        return func(*args, **kwargs)

    node._run_blocking = _run_blocking
    node._planner = SimpleNamespace(
        run_llm_planning_loop=AsyncMock(
            return_value=("LLM 완료", [SimpleNamespace(success=True, result_data={})], True, [])
        )
    )
    if mode is not None:
        node._step_routing_mode = mode
    router = SimpleNamespace(name="laya")
    if router_error is not None:
        router.decide = AsyncMock(side_effect=router_error)
    else:
        router.decide = AsyncMock(return_value=route or RouteDecision.to_llm(source="laya"))
    node._router = router
    node._execute_direct_route = AsyncMock(
        return_value=direct_result
        or ("카메라 이미지를 캡처했습니다.", [SimpleNamespace(success=True, result_data={})], True, [])
    )
    return node


async def _run(node):
    return await TaskDecomposer(node).run(
        goal_handle=MagicMock(),
        instruction="카메라 이미지 보내줘",
        messages=[{"role": "user", "content": "카메라 이미지 보내줘"}],
        robot_summary={},
        timeout=30.0,
        send_fb=MagicMock(),
    )


def _gate_records(node):
    return [
        json.loads(line.split("System1 step gate: ", 1)[1])
        for line in node.logs
        if isinstance(line, str) and line.startswith("System1 step gate: ")
    ]


class TestDecomposerFastPath:
    async def test_on_mode_runs_eligible_step_directly_and_dependent_step_with_llm(self):
        node = _make_node("on", route=_direct())

        _msg, _results, success, _events = await _run(node)

        assert success is True
        node._execute_direct_route.assert_awaited_once()
        assert node._execute_direct_route.await_args.args[1] == "카메라로 촬영해"
        # 2단계는 '촬영한' 때문에 게이트에서 탈락 → 라우터 호출 없이 LLM
        assert node._router.decide.await_count == 1
        assert node._planner.run_llm_planning_loop.await_count == 1
        assert node._planner.run_llm_planning_loop.await_args.args[1] == "촬영한 이미지를 전송해"
        records = _gate_records(node)
        assert [r["action"] for r in records] == ["direct_skill", "llm"]
        assert records[1]["reasons"] == [REASON_DEPENDS]

    async def test_shadow_mode_records_decision_but_executes_with_llm(self):
        node = _make_node("shadow", route=_direct())

        await _run(node)

        node._execute_direct_route.assert_not_awaited()
        assert node._planner.run_llm_planning_loop.await_count == 2
        first = _gate_records(node)[0]
        assert first["would"] == "direct_skill"
        assert first["action"] == "llm"
        assert first["route"]["skill"] == "capture_camera_image"

    @pytest.mark.parametrize("mode", ["off", None])
    async def test_off_or_missing_mode_keeps_legacy_path(self, mode):
        node = _make_node(mode, route=_direct())

        await _run(node)

        node._router.decide.assert_not_awaited()
        node._execute_direct_route.assert_not_awaited()
        assert node._planner.run_llm_planning_loop.await_count == 2
        assert _gate_records(node) == []

    async def test_background_direct_route_is_not_used_in_steps(self):
        """백그라운드 실행은 시작 즉시 성공으로 보고되어 다음 단계가 먼저 시작되므로 쓰지 않는다."""
        node = _make_node("on", route=_direct("adaptive_pick_object", background=True))

        await _run(node)

        node._execute_direct_route.assert_not_awaited()
        assert node._planner.run_llm_planning_loop.await_count == 2
        assert _gate_records(node)[0]["would"] == "llm"

    async def test_direct_failure_falls_back_to_llm(self):
        failed = ("capture_camera_image 실행 실패", [SimpleNamespace(success=False, result_data={})], False, [])
        node = _make_node("on", route=_direct(), direct_result=failed)

        _msg, results, success, _events = await _run(node)

        assert success is True
        assert node._planner.run_llm_planning_loop.await_count == 2
        assert node._planner.run_llm_planning_loop.await_args_list[0].args[1] == "카메라로 촬영해"
        assert any(getattr(r, "success", True) is False for r in results), "실패한 빠른 경로 기록 보존"

    async def test_router_error_falls_back_to_llm(self):
        node = _make_node("on", router_error=RuntimeError("boom"))

        await _run(node)

        assert node._planner.run_llm_planning_loop.await_count == 2
        assert "RuntimeError" in _gate_records(node)[0]["error"]

    async def test_simple_reply_route_completes_step_without_llm(self):
        node = _make_node("on", route=RouteDecision(kind="simple_reply", reply="네, 무엇을 도와드릴까요?"))

        await _run(node)

        assert node._planner.run_llm_planning_loop.await_count == 1
