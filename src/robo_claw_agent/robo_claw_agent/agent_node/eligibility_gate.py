"""분해된 하위 단계의 System 1 라우팅 적격성 판정 (Eligibility Gate).

TaskDecomposer 가 복합 명령을 나눈 각 단계가 **혼자서 완결되는지** 판정한다. 완결되는 단계만
사전 라우터(Rule / System 1)로 보내고, 그 외는 이전 단계 요약을 참고할 수 있는 LLM 이 처리한다.

탈락 사유(보수적으로 판정한다 — 애매하면 LLM):

* ``depends_on_previous``: 이전 단계 결과를 가리킨다("촬영한 이미지를 전송해", "찾은 컵을 집어").
* ``conditional``: 조건·판단이 필요하다("배터리가 20% 이하면 충전대로 가").
* ``compound``: 분해 후에도 여러 동작이 남아 있다("찍어서 보내").
* ``empty``: 빈 단계.

인자(숫자·자유 텍스트)가 필요한지, 직접 실행 범위(scope)·확신도·동작 요청(needs_motion)은
라우터가 판단하므로 여기서 다루지 않는다. rclpy 의존이 없는 순수 모듈이다(AGENTS.md §3).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

STEP_ROUTING_OFF = "off"
STEP_ROUTING_SHADOW = "shadow"
STEP_ROUTING_ON = "on"
_VALID_MODES = (STEP_ROUTING_OFF, STEP_ROUTING_SHADOW, STEP_ROUTING_ON)

REASON_EMPTY = "empty"
REASON_DEPENDS = "depends_on_previous"
REASON_CONDITIONAL = "conditional"
REASON_COMPOUND = "compound"

# 이전 단계의 결과물을 가리키는 표현(관형형 + 지시어).
_REFERENCE_RE = re.compile(
    r"(촬영한|찍은|캡처한|찾은|발견한|감지한|인식한|탐지한|측정한|전송한|보낸|분석한|"
    r"확인한|조회한|검색한|저장한|기록한|생성한|만든|받은|가져온|집은|잡은|들고\s*있는|"
    r"방금|아까|앞에서|이전\s*단계|위\s*결과|해당|그것|그거|그걸|그곳|거기|그\s*결과)"
)

# 조건·판단 표현. "화면/정면" 같은 명사의 '면'은 잡지 않도록 어간을 한정한다.
_CONDITIONAL_RE = re.compile(
    r"(만약|만일|경우|아니면|않으면|(?:이|다|라|으|하|되|있|없|보이|오|끝나|이하|이상|미만|초과|넘으|크|작)면(?:\s|$|,)|"
    r"할\s*때|일\s*때|인지\s*확인|따라\s*(?:다르게|결정))"
)


@dataclass
class GateDecision:
    eligible: bool
    reasons: list[str] = field(default_factory=list)


def check_step_eligibility(step: str) -> GateDecision:
    """하위 단계 하나가 사전 라우터로 가도 되는지 판정한다."""
    # 순환 import 를 피하려고 호출 시점에 가져온다(task_planner 가 이 모듈을 import 한다).
    from .task_planner import looks_compound

    text = str(step or "").strip()
    if not text:
        return GateDecision(False, [REASON_EMPTY])
    reasons: list[str] = []
    if _REFERENCE_RE.search(text):
        reasons.append(REASON_DEPENDS)
    if _CONDITIONAL_RE.search(text + " "):
        reasons.append(REASON_CONDITIONAL)
    if looks_compound(text):
        reasons.append(REASON_COMPOUND)
    return GateDecision(not reasons, reasons)


def step_routing_mode(env: Mapping[str, str]) -> str:
    """``SYSTEM1_STEP_ROUTING``: off | shadow(기본, 판정만 기록) | on(적격 단계를 라우터로 처리)."""
    raw = (env.get("SYSTEM1_STEP_ROUTING") or STEP_ROUTING_SHADOW).strip().lower()
    if raw not in _VALID_MODES:
        logger.warning("Unknown SYSTEM1_STEP_ROUTING=%r — using 'shadow'", raw)
        return STEP_ROUTING_SHADOW
    return raw
