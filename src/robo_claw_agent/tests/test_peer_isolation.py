"""Maestro 관제 중 로봇 간 협업 차단.

Maestro가 명령의 단일 경로일 때 peer 협업 스킬은 등록 해제되고, 실행 정책과
수신 경로에서도 차단되어야 한다. 자리 양보(yield)는 안전 프로토콜이므로 제외한다.
"""

from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node import prompts
from robo_claw_agent.skill_manager import BaseSkill, SkillManager
from robo_claw_agent.skills.fleet_handoff import (
    PEER_COLLAB_SKILLS,
    maestro_control_active,
)

pytestmark = pytest.mark.unit


class _NavigateSkill(BaseSkill):
    name = "navigate_to"
    description = "테스트용 이동 스킬"

    def execute(self, params):
        return {"success": True, "message": self.name}


class _PeerSkill(BaseSkill):
    name = "delegate_task"
    description = "테스트용 동료 위임 스킬"

    def execute(self, params):
        return {"success": True, "message": self.name}


class _CooperateSkill(BaseSkill):
    name = "autonomous_cooperate"
    description = "테스트용 자율협동 스킬"

    def execute(self, params):
        return {"success": True, "message": self.name}


def test_maestro_control_active_reads_the_enable_switch(monkeypatch):
    assert maestro_control_active() is False
    monkeypatch.setenv("MAESTRO_ENABLED", "TRUE")
    assert maestro_control_active() is True
    monkeypatch.setenv("MAESTRO_ENABLED", "false")
    assert maestro_control_active() is False


def test_peer_collaboration_skills_exclude_yield_protocol():
    assert "request_yield_slot" not in PEER_COLLAB_SKILLS
    assert {"delegate_task", "call_peer_robot", "autonomous_cooperate"} <= PEER_COLLAB_SKILLS


def test_remove_skills_unregisters_peer_skills():
    manager = SkillManager()
    manager.register(_NavigateSkill())
    manager.register(_PeerSkill())
    manager.register(_CooperateSkill())

    removed = manager.remove_skills(PEER_COLLAB_SKILLS)

    assert sorted(removed) == ["autonomous_cooperate", "delegate_task"]
    assert [skill["name"] for skill in manager.list_skills()] == ["navigate_to"]
    assert manager.has_skill("delegate_task") is False


def test_policy_blocks_peer_skills_even_if_allowed(monkeypatch):
    manager = SkillManager()
    manager.register(_PeerSkill())

    # Maestro 활성 시 정책은 allowed와 무관하게 peer 스킬을 차단한다.
    monkeypatch.setenv("MAESTRO_ENABLED", "true")
    node = _node_stub(manager)
    node._apply_skill_policy(SimpleNamespace(skill_allowed_json="", skill_blocked_json=""))

    result = manager.execute("delegate_task", {})
    assert result.success is False
    assert "차단" in result.message


def test_peer_message_is_rejected_under_maestro(monkeypatch):
    node_cls = _node_class()
    node = node_cls.__new__(node_cls)
    node.get_logger = lambda: _Logger()

    monkeypatch.setenv("MAESTRO_ENABLED", "true")
    response = SimpleNamespace(success=False, error_message="", result_message="")
    result = node._handle_peer_message(
        SimpleNamespace(peer_name="cloid01", message="같이 정리하자"), response
    )

    assert result.success is False
    assert "Maestro" in result.error_message


def test_auto_cooperation_is_not_started_under_maestro(monkeypatch):
    node_cls = _node_class()
    node = node_cls.__new__(node_cls)

    class _ExplodingSkills:
        def has_skill(self, name):
            raise AssertionError("협동 스킬을 조회하면 안 됩니다")

        def execute(self, *args, **kwargs):
            raise AssertionError("협동 스킬을 실행하면 안 됩니다")

    node._skills = _ExplodingSkills()
    monkeypatch.setenv("MAESTRO_ENABLED", "true")

    node._ensure_autonomous_cooperate_for_peer_message("같이 정리하자")


def test_prompt_guidance_drops_peer_collaboration_lines():
    text = "\n".join(
        [
            " 11. **협업/진단**",
            "    - 다른 에이전트에 위임 -> `delegate_task`",
            "    - 동료 상태 확인 -> `query_peer_status`",
            "    - ROS 명령 실행 -> `ros_command`",
        ]
    )

    stripped = prompts.strip_peer_collaboration_guidance(text)

    assert "`delegate_task`" not in stripped
    assert "`query_peer_status`" not in stripped
    assert "`ros_command`" in stripped
    assert "peer_requests" in stripped


def _node_stub(manager):
    pytest.importorskip("robo_claw_msgs")
    from robo_claw_agent.agent_node.node import AgentNode

    node = AgentNode.__new__(AgentNode)
    node._skills = manager
    node.get_logger = lambda: _Logger()
    return node


def _node_class():
    pytest.importorskip("robo_claw_msgs")
    from robo_claw_agent.agent_node.node import AgentNode

    return AgentNode


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def debug(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass
