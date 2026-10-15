"""autonomous cleanup demo routing"""



from _autonomous_cleanup_helpers import (  # noqa: F401
    MagicMock,
    NodeStatus,
    ResolveCleanupHandoff,
    SimpleNamespace,
    _node_with_skills,
)


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
