"""CLOiD 표현 모션 자동 재생 훅의 상황 판정 테스트.

태스크 완료 훅이 (1) 기능 비활성 시 아무것도 하지 않고, (2) 베이스 주행이 있었던
태스크에서는 재생하지 않으며, (3) 나머지는 상황에 맞는 표현을 고르는지 고정한다.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.agent_node.execution import resolve_cloid_expression_situation
from robo_claw_agent.skill_manager import SkillResult

pytestmark = pytest.mark.unit


class _FakeSkills:
    def __init__(self, *, express_registered: bool = True) -> None:
        self.express_registered = express_registered

    def has_skill(self, name: str) -> bool:
        return name == "cloid_express" and self.express_registered

    def get_skill(self, name: str) -> Any:
        return None


def _result(name: str, success: bool = True) -> SkillResult:
    return SkillResult(name, success, "ok", 0.0, {})


def test_returns_none_when_expression_skill_is_not_registered() -> None:
    assert (
        resolve_cloid_expression_situation(_FakeSkills(express_registered=False), "안녕", [], True)
        is None
    )


def test_classifies_greeting_task() -> None:
    situation = resolve_cloid_expression_situation(_FakeSkills(), "안녕하세요", [], True)
    assert situation == "greeting"


def test_classifies_failure_task() -> None:
    situation = resolve_cloid_expression_situation(
        _FakeSkills(), "청소해줘", [_result("tidy_home", success=False)], False
    )
    assert situation == "fail"


def test_skips_tasks_that_used_base_motion_skill() -> None:
    # move_relative 는 BASE_MOTION_SKILLS 에 속하므로 주행 후 표현은 재생하지 않는다.
    situation = resolve_cloid_expression_situation(
        _FakeSkills(), "안녕", [_result("move_relative")], True
    )
    assert situation is None


def test_skips_navigation_instruction_without_executed_skill() -> None:
    situation = resolve_cloid_expression_situation(_FakeSkills(), "주방으로 이동해", [], True)
    assert situation is None


def test_returns_none_without_skill_manager() -> None:
    assert resolve_cloid_expression_situation(None, "안녕", [], True) is None
