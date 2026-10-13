"""System 1 Fast Router(사전 라우팅 계층) 회귀 테스트.

RuleRouter 는 리팩터링 전 ``ExecutionMixin._execute_task_inner`` 의 인라인 규칙
(복합 명령 선점 방지 → ``check_direct_skill`` → ``_SIMPLE_QUERY_PATTERNS``)과
같은 결과를 내야 한다.
"""

import asyncio
import logging

from robo_claw_agent.agent_node.fast_router import (
    ROUTE_DIRECT_SKILL,
    ROUTE_LLM,
    ROUTE_SIMPLE_REPLY,
    SIMPLE_REPLY_MESSAGE,
    RouteDecision,
    RuleRouter,
)


class _Node:
    def __init__(self) -> None:
        self._logger = logging.getLogger("test_fast_router")

    def get_logger(self):
        return self._logger


def _decide(router, instruction: str) -> RouteDecision:
    return asyncio.run(router.decide(_Node(), instruction))


class TestRuleRouter:
    def test_relative_move_routes_to_direct_skill(self):
        route = _decide(RuleRouter(), "앞으로 2미터 가줘")

        assert route.kind == ROUTE_DIRECT_SKILL
        assert route.as_direct_skill() == {
            "skill": "move_relative",
            "params": {"forward": 2.0},
            "background": False,
        }
        assert route.source == "rule"

    def test_compound_instruction_is_not_preempted(self):
        route = _decide(RuleRouter(), "회의실로 이동한 다음 컵을 집어줘")

        assert route.kind == ROUTE_LLM

    def test_simple_query_gets_fixed_reply(self):
        for text in ("안녕", "고마워!", "hello", "thanks"):
            route = _decide(RuleRouter(), text)
            assert route.kind == ROUTE_SIMPLE_REPLY, text
            assert route.reply == SIMPLE_REPLY_MESSAGE

    def test_other_instruction_goes_to_llm(self):
        for text in ("주방으로 이동해", "지금 배터리 상태 알려줘", "안녕 오늘 할 일 알려줘"):
            assert _decide(RuleRouter(), text).kind == ROUTE_LLM, text

    def test_cleanup_demo_protocol_routes_even_with_connective_scene_summary(self):
        """씬 요약문에 접속어('그리고', '하고')가 있어도 프로토콜 요청은 direct_skill로 라우팅되어야 한다."""
        from robo_claw_agent.skills.cleanup_demo_protocol import encode_cleanup_demo_request

        instruction = encode_cleanup_demo_request(
            {
                "request_id": "r-comp",
                "target_location": "안방",
                "target_object": "수건",
                "scene_summary": "안방 바닥에 수건이 있고 그리고 침대 옆에 쿠션이 놓여 있습니다.",
            }
        )
        route = _decide(RuleRouter(), instruction)
        assert route.kind == ROUTE_DIRECT_SKILL
        assert route.skill == "cleanup_demo"
        assert route.params["target_object"] == "수건"


def test_trace_metadata_contains_route_fields():
    route = RouteDecision(
        kind=ROUTE_DIRECT_SKILL,
        skill="get_status",
        intent="single_skill",
        confidence=0.91234,
        source="laya",
        latency_ms=33.33,
    )

    meta = route.trace_metadata()

    assert meta["route.kind"] == ROUTE_DIRECT_SKILL
    assert meta["route.source"] == "laya"
    assert meta["route.fallback"] is False
    assert meta["route.intent"] == "single_skill"
    assert meta["route.confidence"] == 0.9123
    assert meta["route.skill"] == "get_status"
