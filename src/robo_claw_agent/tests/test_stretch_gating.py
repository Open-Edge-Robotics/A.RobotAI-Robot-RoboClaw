"""stretch 전용 정책/스킬이 manipulation_backend 에 따라 게이팅되는지 검증.

Former 처럼 팔 없는 비-stretch 로봇이 stretch3 전용 stow/mode 전환 스킬이나
주행 안전 정책을 받지 않도록 하는 게이팅의 핵심 경로를 단위 테스트한다.
"""

import pytest

pytest.importorskip("rclpy")

from unittest.mock import MagicMock

from robo_claw_agent.agent_node.prompts import (
    _STRETCH_GRIPPER_CAMERA_POLICY,
    _STRETCH_NAVIGATION_SAFETY_POLICY,
    assemble_static_prompt_base,
)
from robo_claw_agent.skill_manager import SkillManager

_STRETCH_SKILL_NAMES = {
    "stow_for_navigation",
    "switch_stretch_mode",
    "stretch_navigation_mode",
    "stretch_position_mode",
}


class _Param:
    def __init__(self, value) -> None:
        self.value = value


class _FakeNode:
    """SkillManager 가 manipulation_backend 파라미터를 읽는 데 필요한 최소 노드."""

    def __init__(self, backend: str) -> None:
        self._backend = backend

    def get_parameter(self, name: str):
        if name == "manipulation_backend":
            return _Param(self._backend)
        raise KeyError(name)


def _registered_stretch_skills(backend: str) -> set[str]:
    mgr = SkillManager(node=_FakeNode(backend))
    mgr.load_from_module("robo_claw_agent.skills.manipulation_skill")
    return {s["name"] for s in mgr.list_skills()} & _STRETCH_SKILL_NAMES


def test_stretch_skills_skipped_on_non_stretch_backend():
    """backend 가 moveit 이면 stretch 전용 4개 스킬이 등록되지 않는다."""
    assert _registered_stretch_skills("moveit") == set()


def test_stretch_skills_registered_on_stretch_backend():
    """backend 가 stretch 이면 stretch 전용 4개 스킬이 모두 등록된다."""
    assert _registered_stretch_skills("stretch") == _STRETCH_SKILL_NAMES


class _PromptNode:
    """assemble_static_prompt_base 호출에 필요한 최소 노드."""

    def __init__(self, backend: str) -> None:
        self._system_prompt_base_cache = None
        self._system_prompt = "Base Prompt {skill_list}"
        self._skills_guide = ""
        self._script_skills_guide = ""
        self._robot_limits = ""
        self._troubleshooting_guide = ""
        self._agent_workspace_dir = ""
        self._startup_knowledge_ctx = ""
        self._skills = MagicMock()
        self._skills.list_skills.return_value = []
        self._backend = backend

    def get_parameter(self, name: str):
        if name == "manipulation_backend":
            return _Param(self._backend)
        raise KeyError(name)


def _build_prompt(backend: str) -> str:
    return assemble_static_prompt_base(_PromptNode(backend))


def test_stretch_navigation_policy_omitted_on_non_stretch_backend():
    """backend 가 moveit 이면 stretch 주행 안전/그리퍼카메라 정책이 주입되지 않는다."""
    prompt = _build_prompt("moveit")
    assert _STRETCH_NAVIGATION_SAFETY_POLICY not in prompt
    assert _STRETCH_GRIPPER_CAMERA_POLICY not in prompt
    assert "[Stretch3 주행 안전 정책]" not in prompt


def test_stretch_navigation_policy_injected_on_stretch_backend():
    """backend 가 stretch 이면 stretch 주행 안전/그리퍼카메라 정책이 주입된다."""
    prompt = _build_prompt("stretch")
    assert _STRETCH_NAVIGATION_SAFETY_POLICY in prompt
    assert _STRETCH_GRIPPER_CAMERA_POLICY in prompt
