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


def test_cleanup_mode_routes_to_demo_capable_peer_when_no_manipulator_exists(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid1", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 1.25, "y": -0.5}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "포장지"}],
        "scene_analysis": "안방 바닥에 포장지가 놓여 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid1"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload == {
        "request_id": payload["request_id"],
        "target_location": "안방",
        "target_object": "포장지",
        "scene_summary": "안방 바닥에 포장지가 놓여 있습니다.",
    }
    assert not {"x", "y", "target_pose"}.intersection(payload)


def test_cleanup_mode_routes_to_demo_peer_when_cushion_is_also_detected(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid1", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 1.25, "y": -0.5}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "포장지"}, {"name": "cushion"}],
        "scene_analysis": "안방 바닥에 포장지와 쿠션이 놓여 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid1"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload["target_location"] == "안방"
    assert payload["target_object"] == "포장지"


def test_cleanup_mode_routes_to_demo_peer_when_cushion_in_objects_and_waste_in_scene(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid1", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = None

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "쿠션"}],
        "scene_analysis": "바닥에 쿠션과 포장지가 보입니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid1"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload["target_location"] == "안방"
    assert payload["target_object"] == "포장지"


def test_household_item_handoff_requires_manipulation_not_cleanup_demo(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    find_peer = MagicMock(
        side_effect=lambda _node, capability: (
            {"peer_name": "cloid1", "transport": "grpc"} if capability == "cleanup_demo" else None
        )
    )
    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "laundry"}],
        "scene_analysis": "바닥에 빨래가 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_blocked"] is True
    assert handoff._blackboard.get("cleanup_handoff_prepared") is not True
    assert "보관 위치를 지정해 주세요" in handoff._blackboard["cleanup_handoff_status"]
    find_peer.assert_called_once_with(node, "manipulation")


def test_household_item_handoff_to_manipulator_preserves_category_safety(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, capability: (
            {"peer_name": "butler", "transport": "grpc"} if capability == "manipulation" else None
        ),
    )
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = None

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [],
        "scene_analysis": "바닥에 수건이 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    instruction = handoff._blackboard["decided_params"]["instruction"]
    assert "수건" in instruction
    assert "쓰레기통" in instruction
    assert "추측하지 마세요" in instruction


def test_cleanup_handoff_records_no_connected_capable_peer(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes, helpers

    # execution_nodes 는 import 시점에 helpers 함수를 자체 바인딩하므로 두 곳 모두 대체한다.
    # (helpers 만 대체하면 실제 gRPC peer capability 조회가 실행돼 테스트가 20초 지연된다.)
    monkeypatch.setattr(helpers, "_find_connected_peer_with_capability", lambda *_args: None)
    monkeypatch.setattr(
        execution_nodes, "_find_connected_peer_with_capability", lambda *_args: None
    )
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


def test_find_connected_peer_skips_grpc_lookup_when_ros_peer_capable(monkeypatch):
    """ROS 그래프에서 능력이 확인되면 ListPeers gRPC 조회(최대 10초)를 지연시켜야 한다."""
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.setenv(
        "GRPC_TARGET_PEERS_JSON",
        json.dumps([{"name": "butler", "capabilities": ["manipulation"]}]),
    )
    called = []

    def _spy(node):
        called.append(node)
        return set()

    monkeypatch.setattr(helpers, "_connected_grpc_peer_names", _spy)

    node = _node_with_skills(["navigate_to"], node_names=["/butler/robo_claw_agent_node"])

    peer = helpers._find_connected_peer_with_capability(node, "manipulation")

    assert peer == {"peer_name": "butler", "transport": "ros2"}
    assert called == []


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


def test_query_grpc_peer_capability_timeout_and_caching(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    helpers._clear_peer_capability_cache()

    calls = []

    class FakeQuerySkill:
        def __init__(self):
            self.node = None

        def set_node(self, node):
            self.node = node

        def execute(self, params):
            calls.append(dict(params))
            return {
                "success": True,
                "capabilities": ["cleanup_demo", "navigate_to"],
            }

    monkeypatch.setattr(
        "robo_claw_agent.skills.cooperate_skill.QueryPeerCapabilitiesSkill",
        FakeQuerySkill,
    )

    node = MagicMock()
    # 1st query: should call QueryPeerCapabilitiesSkill with timeout >= 25.0
    res1 = helpers._query_grpc_peer_capability(node, "cloid 1호", "manipulation")
    assert res1 is False
    assert len(calls) == 1
    assert calls[0]["timeout_sec"] >= 25.0

    # 2nd query for same peer, different capability ("cleanup_demo"): should HIT cache, no new call!
    res2 = helpers._query_grpc_peer_capability(node, "cloid 1호", "cleanup_demo")
    assert res2 is True
    assert len(calls) == 1


def test_query_grpc_peer_capability_negative_caching(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    helpers._clear_peer_capability_cache()

    calls = []

    class FakeFailingSkill:
        def set_node(self, node):
            pass

        def execute(self, params):
            calls.append(dict(params))
            return {"success": False, "capabilities": []}

    monkeypatch.setattr(
        "robo_claw_agent.skills.cooperate_skill.QueryPeerCapabilitiesSkill",
        FakeFailingSkill,
    )

    node = MagicMock()
    res1 = helpers._query_grpc_peer_capability(node, "offline_peer", "manipulation")
    assert res1 is False
    assert len(calls) == 1

    # Second check within cooldown should not execute again
    res2 = helpers._query_grpc_peer_capability(node, "offline_peer", "cleanup_demo")
    assert res2 is False
    assert len(calls) == 1


def test_discover_peer_agents_reads_from_node_parameter(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import helpers

    monkeypatch.delenv("GRPC_TARGET_PEERS_JSON", raising=False)

    node = _node_with_skills([])
    node.has_parameter.return_value = True
    node.get_parameter.return_value = SimpleNamespace(
        value=json.dumps(
            [{"name": "cloid 1호", "role": "humanoid", "capabilities": ["cleanup_demo"]}]
        )
    )

    peers = helpers._discover_peer_agents(node)
    assert "cloid 1호" in peers
    assert peers["cloid 1호"]["capabilities"] == ["cleanup_demo"]


def test_peer_has_capability_matches_cleanup_demo_role():
    from robo_claw_agent.skills.autonomous_skill import helpers

    assert helpers._peer_has_capability({"capabilities": ["cleanup_demo"]}, "cleanup_demo") is True
    assert helpers._peer_has_capability({"role": "청소 로봇"}, "cleanup_demo") is True
    assert helpers._peer_has_capability({"role": "cleanup_demo"}, "cleanup_demo") is True


def test_query_grpc_peer_capability_classifies_cloi_humanoid_as_cleanup_demo_not_manipulation(
    monkeypatch,
):
    from robo_claw_agent.skills.autonomous_skill import helpers

    helpers._clear_peer_capability_cache()

    class FakeQuerySkill:
        def __init__(self):
            self.node = None

        def set_node(self, node):
            self.node = node

        def execute(self, params):
            # CLOi reports grasp/place from common manipulator skill and motion skills,
            # but cleanup_demo is internal so it is NOT listed in capabilities.
            return {
                "success": True,
                "capabilities": [
                    "grasp",
                    "place",
                    "execute_cloid_motion",
                    "list_cloid_motions",
                    "navigate_to",
                ],
            }

    monkeypatch.setattr(
        "robo_claw_agent.skills.cooperate_skill.QueryPeerCapabilitiesSkill",
        FakeQuerySkill,
    )

    node = MagicMock()
    # CLOi peer must NOT be treated as physical manipulation peer even with grasp/place
    assert helpers._query_grpc_peer_capability(node, "cloid 1호", "manipulation") is False
    # CLOi peer must be recognized as cleanup_demo peer
    assert helpers._query_grpc_peer_capability(node, "cloid 1호", "cleanup_demo") is True


def test_cleanup_mode_routes_to_demo_peer_when_single_towel_is_detected(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid 1호", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 6.62, "y": -1.84}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "수건"}],
        "scene_analysis": "안방 바닥에 수건이 놓여 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid 1호"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload["target_location"] == "안방"
    assert payload["target_object"] == "수건"


def test_cleanup_mode_routes_to_demo_peer_when_single_cushion_is_detected(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid 1호", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 1.0, "y": 2.0}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "쿠션"}],
        "scene_analysis": "거실 바닥에 쿠션이 놓여 있습니다.",
        "patrol_last_place": "거실",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid 1호"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload["target_location"] == "거실"
    assert payload["target_object"] == "쿠션"


def test_cleanup_mode_routes_to_demo_peer_when_multiple_household_items_detected(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid 1호", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 6.62, "y": -1.84}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "수건"}, {"name": "쿠션"}, {"name": "방석"}],
        "scene_analysis": "안방 바닥에 수건, 쿠션, 방석이 놓여 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid 1호"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload["target_location"] == "안방"
    assert payload["target_object"] == "수건"


def test_cleanup_mode_routes_to_demo_peer_when_cushion_and_pillow_detected(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid 1호", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 2.0, "y": 3.0}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "쿠션"}, {"name": "방석"}],
        "scene_analysis": "거실 바닥에 쿠션과 방석이 놓여 있습니다.",
        "patrol_last_place": "거실",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid 1호"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload["target_location"] == "거실"
    assert payload["target_object"] == "쿠션"


def test_cleanup_mode_extracts_towel_when_scene_mentions_trash_bin(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid 1호", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)
    node = _node_with_skills(["navigate_to", "call_peer_robot"])
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 6.62, "y": -1.84}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "수건"}],
        "scene_analysis": "중앙 아래쪽에 쓰레기통은 보이지만 바닥에 수건이 놓여 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid 1호"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload["target_location"] == "안방"
    assert payload["target_object"] == "수건"


def test_local_robot_executes_demo_directly_when_capable(monkeypatch):
    """로컬 로봇이 cleanup_demo 권한 및 허용 모션 ID를 보유한 경우(1호) 동료에게 위임하지 않고 로컬에서 모션을 실행한다."""
    node = _node_with_skills(
        ["navigate_to", "execute_cloid_motion", "list_cloid_motions", "cleanup_demo"]
    )

    node.get_parameter = lambda name: SimpleNamespace(value="[22, 128]")
    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 6.62, "y": -1.84}
    skill.send_user_message = MagicMock()

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "수건"}],
        "scene_analysis": "안방 바닥에 수건이 놓여 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    # 동료 위임이 아니므로 cleanup_handoff_prepared는 False(또는 위임 피어가 없음)
    assert handoff._blackboard.get("cleanup_handoff_prepared") is not True
    assert handoff._blackboard.get("decided_skill") == "execute_cloid_motion"
    assert "motion_id" in handoff._blackboard.get("decided_params", {})


def test_cloi2_does_not_execute_local_motion_and_delegates_to_cloi1(monkeypatch):
    """CLOi 2호는 execute_cloid_motion 스킬이 있어도 cleanup_demo 권한이 없으므로 로컬 모션을 실행하지 않고 1호에게 위임한다."""
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup_demo_protocol import decode_cleanup_demo_request

    def find_peer(_node, capability):
        if capability == "manipulation":
            return None
        if capability == "cleanup_demo":
            return {"peer_name": "cloid 1호", "transport": "grpc"}
        return None

    monkeypatch.setattr(execution_nodes, "_find_connected_peer_with_capability", find_peer)

    # 2호: navigate_to, call_peer_robot, execute_cloid_motion, list_cloid_motions가 있지만 cleanup_demo는 없음!
    node = _node_with_skills(
        ["navigate_to", "call_peer_robot", "execute_cloid_motion", "list_cloid_motions"]
    )
    node.get_parameter = lambda name: SimpleNamespace(value="[]")

    skill = MagicMock()
    skill.node = node
    skill.get_map_pose.return_value = {"x": 6.62, "y": -1.84}

    handoff = ResolveCleanupHandoff(skill)
    handoff._blackboard = {
        "cleanup_task_mode": True,
        "detected_objects": [{"name": "쿠션"}, {"name": "방석"}],
        "scene_analysis": "안방 바닥에 쿠션과 방석이 놓여 있습니다.",
        "patrol_last_place": "안방",
    }

    assert handoff.tick() == NodeStatus.SUCCESS
    # 2호는 로컬 모션을 수행하지 않고 1호에게 위임해야 함!
    assert handoff._blackboard.get("decided_skill") != "execute_cloid_motion"
    assert handoff._blackboard["cleanup_handoff_prepared"] is True
    assert handoff._blackboard["cleanup_handoff_kind"] == "cleanup_demo"
    assert handoff._blackboard["decided_params"]["peer_name"] == "cloid 1호"
    payload = decode_cleanup_demo_request(handoff._blackboard["decided_params"]["instruction"])
    assert payload["target_location"] == "안방"
    assert payload["target_object"] == "쿠션"
