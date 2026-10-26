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


# ── boxing 자세를 만든 태스크 직후 제외 ──


def test_skips_right_after_packing_pose_task() -> None:
    """포장 자세를 만든 태스크가 끝나면 표현 모션을 재생하지 않는다.

    자세를 만든 직후의 팔 모션이 그 자세를 바로 풀어 버리기 때문이다.
    """
    situation = resolve_cloid_expression_situation(
        _FakeSkills(), "박싱 자세로 만들어줘", [_result("set_cloid_packing_pose")], True
    )
    assert situation is None


def test_skips_right_after_navigation_pose_prelude() -> None:
    """주행 전 자세 prelude 로 boxing 자세를 만든 태스크도 같다."""
    situation = resolve_cloid_expression_situation(
        _FakeSkills(), "박싱 자세 준비해줘", [_result("cloid_navigation_pose")], True
    )
    assert situation is None


def test_plays_motion_in_later_conversation() -> None:
    """자세를 만든 태스크가 지난 뒤의 대화에서는 표현 모션을 다시 재생한다.

    이때 팔이 움직이면 자세는 풀리지만, 자세 유지 여부는 다음 요청의 판단이다.
    """
    situation = resolve_cloid_expression_situation(
        _FakeSkills(), "안녕하세요", [_result("get_status")], True
    )
    assert situation == "greeting"


def test_failed_pose_skill_does_not_suppress_expression() -> None:
    """자세 요청이 실패했으면 자세가 만들어진 것이 아니므로 실패 표현을 허용한다."""
    situation = resolve_cloid_expression_situation(
        _FakeSkills(),
        "박싱 자세로 만들어줘",
        [_result("set_cloid_packing_pose", success=False)],
        False,
    )
    assert situation == "fail"
