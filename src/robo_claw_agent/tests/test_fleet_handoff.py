"""Maestro 관제 중 동료 위임 보고(fleet_handoff)"""

import pytest
from _autonomous_cleanup_helpers import (  # noqa: F401
    MagicMock,
    NodeStatus,
    ResolveCleanupHandoff,
    _node_with_skills,
)
from _tidy_home_helpers import TidyHomeSkill, _place
from robo_claw_agent.skills import fleet_handoff

pytestmark = pytest.mark.unit


def test_mode_defaults_to_report_when_maestro_is_configured(monkeypatch):
    assert fleet_handoff.peer_handoff_mode() == "direct"
    monkeypatch.setenv("MAESTRO_IP", "192.168.50.114")
    assert fleet_handoff.peer_handoff_mode() == "report"
    monkeypatch.setenv("RC_PEER_HANDOFF_MODE", "direct")
    assert fleet_handoff.peer_handoff_mode() == "direct"
    monkeypatch.delenv("MAESTRO_IP")
    monkeypatch.setenv("RC_PEER_HANDOFF_MODE", "report")
    assert fleet_handoff.peer_handoff_mode() == "report"


def _demo_handoff(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, capability: (
            {"peer_name": "cloid01", "transport": "grpc"} if capability == "cleanup_demo" else None
        ),
    )
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = None
    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "포장지"}],
        "scene_analysis": "안방 바닥에 포장지가 있습니다.",
        "patrol_last_place": "안방",
    }
    return handoff, skill


def test_under_maestro_the_peer_is_not_called_and_the_need_is_reported(monkeypatch):
    monkeypatch.setenv("MAESTRO_IP", "192.168.50.114")
    handoff, skill = _demo_handoff(monkeypatch)

    assert handoff.tick() == NodeStatus.SUCCESS

    board = handoff._blackboard
    assert board.get("cleanup_handoff_prepared") is not True
    assert board["decided_skill"] == "none"
    report = board["cleanup_handoff_report"]
    assert report["kind"] == "cleanup_demo"
    assert report["required_capability"] == "cleanup_demo"
    assert report["suggested_peer"] == "cloid01"
    assert report["place"] == "안방"
    assert report["instruction"]
    assert "Maestro" in skill.send_user_message.call_args[0][0]


def test_without_maestro_the_existing_direct_handoff_is_kept(monkeypatch):
    handoff, _ = _demo_handoff(monkeypatch)

    assert handoff.tick() == NodeStatus.SUCCESS

    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["decided_skill"] == "call_peer_robot"
    assert "cleanup_handoff_report" not in handoff._blackboard


def test_tidy_home_waits_and_returns_peer_requests_under_maestro(monkeypatch):
    from robo_claw_agent.skills import tidy_home_skill

    monkeypatch.setenv("MAESTRO_IP", "192.168.50.114")
    skill = TidyHomeSkill()
    skill.node = MagicMock()
    skill.node._skills.has_skill.return_value = False
    skill.node._agent_id = "Former0047"
    monkeypatch.setattr(skill, "send_user_message", lambda _message: None)
    monkeypatch.setattr(skill, "_cloid_indicator_enabled", lambda: False)
    monkeypatch.setattr(
        skill, "_collect_places", lambda *_args, **_kwargs: ([_place("안방", 1, 2)], None)
    )
    monkeypatch.setattr(skill, "_visit_place", lambda _name: (True, "도착"))
    monkeypatch.setattr(
        skill,
        "_analyze_current_scene",
        lambda _name: {
            "success": True,
            "analysis": "안방 바닥에 포장지가 있습니다.",
            "detected_objects": [{"name": "포장지"}],
        },
    )

    def handle(**_kwargs):
        skill._run_peer_requests.append({"kind": "cleanup_demo", "suggested_peer": "cloid01"})
        return "reported_to_maestro"

    monkeypatch.setattr(skill, "_handle_cleanup_target", handle)
    monkeypatch.setattr(tidy_home_skill, "record_cleanup_run", lambda **_kwargs: None)

    result = skill.execute({"scope": "room", "target_location": "안방"})

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["results"] == [
        {"place": "안방", "status": "reported_to_maestro", "partial_observation": False}
    ]
    assert result["peer_requests"][0]["suggested_peer"] == "cloid01"
