"""CLOiD 상황별 표현 모션 정책/스킬 테스트.

정책은 ROS 와 무관한 순수 함수이고, 스킬은 검증된 ``list_cloid_motions`` /
``execute_cloid_motion`` 경로에 위임하는지만 fake 스킬 매니저로 고정한다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.skill_manager import SkillResult
from robo_claw_agent.skills.cloid_express_skill import CloidExpressSkill
from robo_claw_agent.skills.cloid_expression_policy import (
    SITUATION_FAIL,
    SITUATION_GREETING,
    SITUATION_SCANNING,
    SITUATION_TALKING,
    classify_situation,
    select_expression_motion,
)

pytestmark = pytest.mark.unit


def _motion(motion_id: int, name: str, *, pre_id: Any = None) -> dict[str, Any]:
    return {
        "motion_id": motion_id,
        "motion_name": name,
        "intent_id": "인사",
        "description": "표현 모션",
        "mask": "WLRH",
        "pre_id": pre_id,
    }


# ── 정책 ──


def test_classify_greeting_scanning_and_fallback() -> None:
    assert classify_situation("안녕하세요", task_success=True) == SITUATION_GREETING
    assert classify_situation("hello there", task_success=True) == SITUATION_GREETING
    assert classify_situation("주변을 탐색해줘", task_success=True) == SITUATION_SCANNING
    assert classify_situation("오늘 뭐 할 수 있어?", task_success=True) == SITUATION_TALKING


def test_classify_failure_and_talking() -> None:
    assert classify_situation("청소해줘", task_success=False) == SITUATION_FAIL
    assert classify_situation("청소해줘", task_success=True) == SITUATION_TALKING


def test_classify_skips_base_motion_tasks() -> None:
    assert classify_situation("주방으로 이동해", task_success=True) is None
    assert classify_situation("앞으로 2미터 가줘", task_success=True) is None
    # 실행된 스킬에 베이스 주행이 있었으면 명령 문구와 무관하게 재생하지 않는다.
    assert classify_situation("안녕", task_success=True, used_base_motion=True) is None


def test_select_respects_allowlist_and_requires_no_prerequisite() -> None:
    motions = [
        _motion(14, "wave"),  # greeting allowlist, pre_id 없음
        _motion(20, "greet2", pre_id=19),  # allowlist 밖
        _motion(99999, "custom"),  # allowlist 밖
    ]
    selected = select_expression_motion(motions, SITUATION_GREETING)
    assert selected is not None
    assert selected["motion_id"] == 14


def test_select_skips_prerequisite_in_allowlist() -> None:
    motions = [_motion(14, "wave", pre_id=1)]
    assert select_expression_motion(motions, SITUATION_GREETING) is None


def test_select_unknown_situation_returns_none() -> None:
    assert select_expression_motion([_motion(14, "wave")], "unknown") is None


def test_select_avoids_previous_motion() -> None:
    motions = [_motion(14, "wave"), _motion(15, "wave1")]
    selected = select_expression_motion(motions, SITUATION_GREETING, previous_motion_id=14)
    assert selected is not None
    assert selected["motion_id"] == 15


# ── 스킬 ──


class _FakeSkills:
    def __init__(self, motions: list[dict[str, Any]], *, execute_success: bool = True) -> None:
        self.motions = motions
        self.execute_success = execute_success
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def has_skill(self, name: str) -> bool:
        return name in {"list_cloid_motions", "execute_cloid_motion"}

    def execute(self, name: str, params: dict[str, Any], **kwargs: Any) -> SkillResult:
        self.calls.append((name, params))
        if name == "list_cloid_motions":
            return SkillResult(name, True, "ok", 0.0, {"motions": self.motions})
        if not self.execute_success:
            return SkillResult(
                name, False, "publish failed", 0.0, {"failure_reason": "motion_failed"}
            )
        return SkillResult(name, True, "START command published", 0.0, {"status": "sent"})


def _skill_with(motions: list[dict[str, Any]], *, execute_success: bool = True):
    skills = _FakeSkills(motions, execute_success=execute_success)
    skill = CloidExpressSkill()
    skill.set_node(SimpleNamespace(_skills=skills))
    return skill, skills


def test_execute_selects_and_delegates_to_execute_cloid_motion() -> None:
    skill, skills = _skill_with([_motion(14, "wave"), _motion(15, "wave1")])

    result = skill.execute({"situation": SITUATION_GREETING})

    assert result["success"] is True
    assert result["status"] == "sent"
    assert result["situation"] == SITUATION_GREETING
    assert result["motion_id"] in {14, 15}
    assert result["completion_confirmed"] is False
    assert [name for name, _ in skills.calls] == ["list_cloid_motions", "execute_cloid_motion"]
    _, execute_params = skills.calls[1]
    assert execute_params["confirm"] is True
    assert execute_params["motion_id"] == result["motion_id"]
    assert execute_params["motion_name"] in {"wave", "wave1"}


def test_execute_rejects_unknown_situation() -> None:
    skill, skills = _skill_with([_motion(14, "wave")])
    result = skill.execute({"situation": "nope"})
    assert result["success"] is False
    assert result["failure_reason"] == "invalid_situation"
    assert skills.calls == []


def test_execute_fails_closed_without_catalog_skills() -> None:
    skill = CloidExpressSkill()

    class _Empty:
        def has_skill(self, name: str) -> bool:
            return False

    skill.set_node(SimpleNamespace(_skills=_Empty()))
    result = skill.execute({"situation": SITUATION_TALKING})
    assert result["success"] is False
    assert result["failure_reason"] == "motion_catalog_unavailable"


def test_execute_fails_when_no_approved_motion() -> None:
    skill, _ = _skill_with([_motion(99999, "custom")])
    result = skill.execute({"situation": SITUATION_TALKING})
    assert result["success"] is False
    assert result["failure_reason"] == "no_approved_expression_motion"


def test_execute_reports_execution_failure() -> None:
    skill, _ = _skill_with([_motion(14, "wave")], execute_success=False)
    result = skill.execute({"situation": SITUATION_GREETING})
    assert result["success"] is False
    assert result["failure_reason"] == "motion_failed"


def test_execute_avoids_repeating_same_motion() -> None:
    skill, _ = _skill_with([_motion(14, "wave"), _motion(15, "wave1")])

    first = skill.execute({"situation": SITUATION_GREETING})
    second = skill.execute({"situation": SITUATION_GREETING})

    assert first["motion_id"] != second["motion_id"]
