"""SkillManager 성공 후처리 훅 회귀 테스트.

주행 전 상체 자세 준비 상태(CLOiD boxing prelude)처럼 스킬 실행 결과에 따라 달라지는
런타임 상태는 SkillManager 가 주행 안전 규칙을 알지 않도록 주입식 훅으로 관리한다.
훅은 성공 시에만 호출되고, 훅 자체의 예외가 스킬 결과를 바꾸면 안 된다.
"""

from __future__ import annotations

from typing import Any

import pytest
from robo_claw_agent.skill_manager import BaseSkill, SkillManager

pytestmark = pytest.mark.unit


class _HookSkill(BaseSkill):
    name = "hook_probe_skill"

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if params.get("fail"):
            return self.fail_result("boom")
        return self.success_result("ok")


def _manager_with_probe() -> SkillManager:
    manager = SkillManager(node=None)
    manager.register(_HookSkill())
    return manager


def test_hook_receives_skill_object_after_success():
    manager = _manager_with_probe()
    seen: list[str] = []
    manager.set_post_skill_success_hook(lambda skill: seen.append(skill.name))

    result = manager.execute("hook_probe_skill", {})

    assert result.success is True
    assert seen == ["hook_probe_skill"]


def test_hook_is_not_called_on_failure():
    manager = _manager_with_probe()
    seen: list[str] = []
    manager.set_post_skill_success_hook(lambda skill: seen.append(skill.name))

    result = manager.execute("hook_probe_skill", {"fail": True})

    assert result.success is False
    assert seen == []


def test_hook_exception_does_not_change_skill_result():
    manager = _manager_with_probe()

    def _boom(_skill):
        raise RuntimeError("hook failure")

    manager.set_post_skill_success_hook(_boom)

    result = manager.execute("hook_probe_skill", {})

    assert result.success is True
    assert result.message == "ok"


def test_hook_can_be_cleared():
    manager = _manager_with_probe()
    seen: list[str] = []
    manager.set_post_skill_success_hook(lambda skill: seen.append(skill.name))
    manager.set_post_skill_success_hook(None)

    manager.execute("hook_probe_skill", {})

    assert seen == []


def test_unknown_skill_does_not_call_hook():
    manager = _manager_with_probe()
    seen: list[str] = []
    manager.set_post_skill_success_hook(lambda skill: seen.append(skill.name))

    result = manager.execute("does_not_exist", {})

    assert result.success is False
    assert seen == []
