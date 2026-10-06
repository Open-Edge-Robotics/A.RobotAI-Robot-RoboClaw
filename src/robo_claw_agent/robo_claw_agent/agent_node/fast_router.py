"""LLM 호출 전 사전 라우팅 계층 (System 1 Fast Router).

설계: ``docs/SYSTEM1_FAST_ROUTER_DESIGN.md``

사전 라우팅(그룹 A)은 요청이 LLM planner(System 2)로 가기 전에 경로를 정한다.

* ``direct_skill``: LLM 없이 스킬을 바로 실행
* ``simple_reply``: LLM 없이 고정 응답
* ``llm``: System 2(기존 LLM planner / task decomposer)로 넘김

LLM 출력을 사후 교정하는 규칙(그룹 B: ``_force_navigation_if_misrouted``,
``_route_location_query`` 등)과 가드(그룹 B': ``_is_likely_conversational``,
``_extract_save_place``, ``nav_safety``)는 planner 안에 그대로 두며 이 모듈에서
참조하지 않는다. 라우터 모드와 무관하게 항상 동작해야 하기 때문이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

# 스킬이 필요없는 단순 대화형 쿼리를 LLM 호출 전에 차단하는 패턴
_SIMPLE_QUERY_PATTERNS = re.compile(
    r"^(안녕|고마워|반가워|잘가|뭐야|누구야|할 수 있어\??|"
    r"고마워|수고해|들어가|꺼져|잘했어|좋아|응|아니|"
    r"ㅋ|ㅎ|넵|ok|yes|no|hello|hi|bye|thanks|thank you|"
    r"help|도움)\s*[.!?]*\s*$",
    re.IGNORECASE,
)

SIMPLE_REPLY_MESSAGE = "네, 무엇을 도와드릴까요?"

ROUTE_DIRECT_SKILL = "direct_skill"
ROUTE_SIMPLE_REPLY = "simple_reply"
ROUTE_LLM = "llm"


@dataclass
class RouteDecision:
    """사전 라우팅 결과."""

    kind: str
    skill: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    background: bool = False
    reply: str = ""
    intent: str = ""
    confidence: float | None = None
    # 실제로 판단한 라우터("rule" / "laya" / "jev"). 장애 대체 시 "rule".
    source: str = "rule"
    # 선택된 라우터가 장애로 실패해 fallback 라우터가 판단했는지 여부
    fallback: bool = False
    latency_ms: float = 0.0
    # System 2로 넘길 때 planner가 참고할 수 있는 System 1 판단 요약
    hint: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def to_llm(cls, source: str = "rule", **kwargs: Any) -> RouteDecision:
        return cls(kind=ROUTE_LLM, source=source, **kwargs)

    def as_direct_skill(self) -> dict[str, Any]:
        """기존 ``check_direct_skill`` 반환 형식으로 변환한다."""
        return {"skill": self.skill, "params": dict(self.params), "background": self.background}

    def trace_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "route.kind": self.kind,
            "route.source": self.source,
            "route.fallback": self.fallback,
            "route.latency_ms": round(self.latency_ms, 1),
        }
        if self.intent:
            data["route.intent"] = self.intent
        if self.confidence is not None:
            data["route.confidence"] = round(self.confidence, 4)
        if self.skill:
            data["route.skill"] = self.skill
        if self.hint:
            data["route.hint"] = self.hint
        return data


class FastRouter(Protocol):
    name: str

    async def decide(self, node: Any, instruction: str) -> RouteDecision:
        """사전 라우팅 결과를 반환한다. 판단할 수 없으면 ``ROUTE_LLM``."""
        ...


class RuleRouter:
    """기존 규칙 기반 사전 라우팅(그룹 A). 동작은 리팩터링 전과 동일하다.

    1. 복합 명령은 다이렉트 라우팅으로 선점하지 않는다. 단일 스킬로 축약하면
       "회의실로 이동한 다음 컵을 집어줘"가 이동 없이 집기만 실행되고, 그마저
       백그라운드 시작을 곧바로 성공으로 보고해 나머지 절이 조용히 사라진다.
    2. ``check_direct_skill`` 이 잡으면 스킬을 바로 실행한다.
    3. ``_SIMPLE_QUERY_PATTERNS`` 에 맞으면 LLM 호출 없이 고정 응답한다.
    """

    name = "rule"

    async def decide(self, node: Any, instruction: str) -> RouteDecision:
        return self.decide_sync(node, instruction)

    def decide_sync(self, node: Any, instruction: str) -> RouteDecision:
        # 테스트에서 monkeypatch 할 수 있도록 호출 시점에 import 한다.
        from .direct_skills import check_direct_skill
        from .task_planner import looks_compound

        if looks_compound(instruction):
            node.get_logger().info(
                f"Compound instruction — skipping direct skill routing: '{instruction}'"
            )
        else:
            direct_skill = check_direct_skill(node, instruction)
            if direct_skill is not None:
                return RouteDecision(
                    kind=ROUTE_DIRECT_SKILL,
                    skill=direct_skill["skill"],
                    params=dict(direct_skill.get("params") or {}),
                    background=bool(direct_skill.get("background")),
                    source=self.name,
                )

        if _SIMPLE_QUERY_PATTERNS.match(instruction.strip()):
            node.get_logger().info(
                f"Simple conversational query detected, skipping LLM: '{instruction}'"
            )
            return RouteDecision(
                kind=ROUTE_SIMPLE_REPLY, reply=SIMPLE_REPLY_MESSAGE, source=self.name
            )

        return RouteDecision.to_llm(source=self.name)


DEFAULT_RULE_ROUTER = RuleRouter()
