"""autonomous cleanup handoff"""



from _autonomous_cleanup_helpers import (  # noqa: F401
    LLMDecideAction,
    MagicMock,
    NodeStatus,
    ResolveCleanupHandoff,
    SimpleNamespace,
    ValidateDecidedAction,
    _node_with_skills,
    json,
)


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
