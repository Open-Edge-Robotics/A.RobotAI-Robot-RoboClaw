import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from robo_claw_agent.skills.autonomous_skill.core import NodeStatus
from robo_claw_agent.skills.autonomous_skill.decision_nodes import (
    LLMDecideAction,
    ValidateDecidedAction,
)
from robo_claw_agent.skills.autonomous_skill.execution_nodes import ResolveCleanupHandoff
from robo_claw_agent.skills.autonomous_skill.helpers import (
    _find_connected_peer_with_capability,
    _has_cleanup_capability,
)


def _node_with_skills(skill_names, node_names=None):
    node = MagicMock()
    node.get_node_names.return_value = node_names or []
    node.get_name.return_value = "robo_claw_agent_node"
    node.get_namespace.return_value = "/former"
    node._skills = MagicMock()
    node._skills.list_skills.return_value = [
        {"name": name, "description": name} for name in skill_names
    ]
    node._skills.has_skill.side_effect = lambda name: name in skill_names
    node._skills.get_skill.side_effect = lambda name: SimpleNamespace(
        terminal_behavior="sync", side_effects=()
    )
    return node


def test_connected_peer_selection_ignores_offline_manipulator(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setenv(
        "GRPC_TARGET_PEERS_JSON",
        json.dumps(
            [
                {
                    "agent_name": "butler",
                    "role": "manipulator",
                    "capabilities": ["manipulation"],
                }
            ]
        ),
    )
    monkeypatch.setattr(helpers, "_connected_grpc_peer_names", lambda _node: set())
    node = _node_with_skills(["navigate_to"])

    assert _find_connected_peer_with_capability(node, "manipulation") is None


def test_connected_peer_selection_uses_live_grpc_peer(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setenv(
        "GRPC_TARGET_PEERS_JSON",
        json.dumps(
            [
                {
                    "agent_name": "butler",
                    "role": "manipulator",
                    "capabilities": ["manipulation"],
                }
            ]
        ),
    )
    monkeypatch.setattr(helpers, "_connected_grpc_peer_names", lambda _node: {"butler"})
    node = _node_with_skills(["navigate_to"])

    assert _find_connected_peer_with_capability(node, "manipulation") == {
        "peer_name": "butler",
        "transport": "grpc",
    }


def test_connected_peer_selection_queries_capabilities_when_configured_unknown(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setenv(
        "GRPC_TARGET_PEERS_JSON",
        json.dumps([{"agent_name": "butler", "description": "연결된 동료"}]),
    )
    monkeypatch.setattr(helpers, "_connected_grpc_peer_names", lambda _node: {"butler"})
    monkeypatch.setattr(
        helpers, "_query_grpc_peer_capability", lambda _node, _peer, cap: cap == "manipulation"
    )
    node = _node_with_skills(["navigate_to"])

    assert _find_connected_peer_with_capability(node, "manipulation") == {
        "peer_name": "butler",
        "transport": "grpc",
    }


def test_connected_peer_selection_detects_ros_peer(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setenv(
        "GRPC_TARGET_PEERS_JSON",
        json.dumps(
            [
                {
                    "agent_name": "butler",
                    "role": "manipulator",
                    "capabilities": ["manipulation"],
                }
            ]
        ),
    )
    monkeypatch.setattr(helpers, "_connected_grpc_peer_names", lambda _node: set())
    node = _node_with_skills(["navigate_to"], node_names=["/butler/robo_claw_agent_node"])

    assert _find_connected_peer_with_capability(node, "manipulation") == {
        "peer_name": "butler",
        "transport": "ros2",
    }


def test_cleanup_handoff_routes_for_manipulation_peer_and_includes_observation(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setenv(
        "GRPC_TARGET_PEERS_JSON",
        json.dumps(
            [
                {
                    "agent_name": "butler",
                    "role": "manipulator",
                    "capabilities": ["manipulation"],
                }
            ]
        ),
    )
    monkeypatch.setattr(helpers, "_connected_grpc_peer_names", lambda _node: {"butler"})
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 1.25, "y": -0.5}
    skill.send_user_message = MagicMock()

    handoff = ResolveCleanupHandoff(skill)
    blackboard = {
        "detected_objects": ["cup"],
        "scene_analysis": "주방 바닥에 컵과 포장지가 놓여 있어 정리가 필요합니다.",
        "patrol_last_place": "주방",
    }
    handoff._blackboard = blackboard

    assert handoff.tick() == NodeStatus.SUCCESS
    assert blackboard["cleanup_handoff_prepared"] is True
    assert blackboard["decided_skill"] == "call_peer_robot"
    assert blackboard["decided_params"]["peer_name"] == "butler"
    instruction = blackboard["decided_params"]["instruction"]
    assert "포장지" in instruction
    assert "주방" in instruction
    assert "x=1.25" in instruction


def test_cleanup_capability_requires_pick_and_place_or_pick_sequence():
    assert _has_cleanup_capability(_node_with_skills(["grasp", "place"])) is True
    assert _has_cleanup_capability(_node_with_skills(["adaptive_pick_object"])) is True
    assert _has_cleanup_capability(_node_with_skills(["arm_pose", "open_gripper"])) is False


def test_cleanup_handoff_uses_ros_action_for_same_network_peer(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setenv(
        "GRPC_TARGET_PEERS_JSON",
        json.dumps(
            [
                {
                    "agent_name": "butler",
                    "role": "manipulator",
                    "capabilities": ["manipulation"],
                }
            ]
        ),
    )
    monkeypatch.setattr(helpers, "_connected_grpc_peer_names", lambda _node: set())
    node = _node_with_skills(
        ["navigate_to", "delegate_task"], node_names=["/butler/robo_claw_agent_node"]
    )
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = None

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "detected_objects": ["trash"],
        "scene_analysis": "주방 바닥에 버려진 쓰레기가 있습니다.",
        "patrol_last_place": "주방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["decided_skill"] == "delegate_task"
    assert handoff._blackboard["decided_params"]["agent_id"] == "butler"


def test_cleanup_handoff_does_not_delegate_when_self_can_manipulate(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setattr(
        helpers,
        "_find_connected_peer_with_capability",
        lambda *_args, **_kwargs: {"peer_name": "butler", "transport": "grpc"},
    )
    node = _node_with_skills(["navigate_to", "grasp", "place"])
    skill = MagicMock()
    skill.node = node

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "detected_objects": ["cup"],
        "scene_analysis": "정리 대상 컵이 보입니다.",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard.get("cleanup_handoff_prepared") is not True
    assert handoff._blackboard.get("decided_skill") is None


def test_cleanup_handoff_does_not_delegate_ambiguous_household_item(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    find_peer = MagicMock(return_value={"peer_name": "butler", "transport": "grpc"})
    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "detected_objects": ["cup"],
        "scene_analysis": "테이블 위에 개인 컵이 있습니다.",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard.get("cleanup_handoff_prepared") is not True
    find_peer.assert_not_called()


def test_cleanup_mode_delegates_when_local_disposal_pose_is_not_curated(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda *_args, **_kwargs: {"peer_name": "butler", "transport": "grpc"},
    )
    node = _node_with_skills(["adaptive_pick_object", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = None

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": ["wrapper"],
        "scene_analysis": "바닥에 포장지가 있습니다.",
        "patrol_last_place": "거실",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["decided_params"]["peer_name"] == "butler"
    assert "안전한 지정 폐기 장소" in handoff._blackboard["decided_params"]["instruction"]


def test_cleanup_handoff_records_no_connected_capable_peer(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setattr(helpers, "_find_connected_peer_with_capability", lambda *_args: None)
    node = _node_with_skills(["navigate_to"])
    skill = MagicMock()
    skill.node = node

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "detected_objects": ["trash"],
        "scene_analysis": "정리 대상 쓰레기가 보입니다.",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard.get("cleanup_handoff_prepared") is not True
    assert handoff._blackboard["cleanup_handoff_blocked"] is True
    assert handoff._blackboard["decided_skill"] == "none"
    assert "동료" in handoff._blackboard["cleanup_handoff_status"]


def test_blocked_cleanup_handoff_skips_llm_replanning():
    llm = MagicMock()
    skill = MagicMock()
    skill.node = SimpleNamespace(_llm=llm)
    decide = LLMDecideAction(skill)
    decide._blackboard = {"cleanup_handoff_blocked": True, "decided_skill": "none"}

    assert decide.tick() == NodeStatus.SUCCESS
    llm.chat.assert_not_called()


def test_cleanup_mode_allows_only_registered_cleanup_skills():
    from robo_claw_agent.skills.autonomous_skill.decision_nodes import _CLEANUP_ACTION_SKILLS

    assert "adaptive_pick_object" in _CLEANUP_ACTION_SKILLS
    assert "call_peer_robot" not in _CLEANUP_ACTION_SKILLS
    assert "navigate_to" not in _CLEANUP_ACTION_SKILLS


def test_cleanup_validator_allows_local_composite_pick_without_navigation_target():
    pick_skill = SimpleNamespace(
        terminal_behavior="sync",
        side_effects=("base_motion", "arm_motion", "gripper_motion"),
        validate_input_schema=lambda _params: (True, ""),
        validate_params=lambda _params: True,
    )
    skills = MagicMock()
    skills.has_skill.side_effect = lambda name: name == "adaptive_pick_object"
    skills.get_skill.return_value = pick_skill
    node = SimpleNamespace(_skills=skills)
    validator = ValidateDecidedAction(SimpleNamespace(node=node))
    validator._blackboard = {
        "cleanup_task_mode": True,
        "patrol_has_destination": True,
        "decided_skill": "adaptive_pick_object",
        "decided_params": {"target_object": "cup"},
    }

    assert validator.tick() == NodeStatus.SUCCESS


def test_stationary_observation_allows_only_prepared_cleanup_handoff():
    peer_skill = SimpleNamespace(
        terminal_behavior="sync",
        side_effects=(),
        validate_input_schema=lambda _params: (True, ""),
        validate_params=lambda _params: True,
    )
    node = MagicMock()
    node._skills.has_skill.return_value = True
    node._skills.get_skill.return_value = peer_skill
    validator = ValidateDecidedAction(MagicMock(node=node))

    blackboard = {
        "stationary_observation_only": True,
        "decided_skill": "call_peer_robot",
        "decided_params": {"peer_name": "butler", "instruction": "컵을 정리해 주세요."},
        "cleanup_handoff_prepared": True,
    }
    validator._blackboard = blackboard

    assert validator.tick() == NodeStatus.SUCCESS

    blackboard["cleanup_handoff_prepared"] = False
    assert validator.tick() == NodeStatus.FAILURE
