"""autonomous cleanup peer selection"""



from _autonomous_cleanup_helpers import (  # noqa: F401
    MagicMock,
    NodeStatus,
    ResolveCleanupHandoff,
    _find_connected_peer_with_capability,
    _has_cleanup_capability,
    _node_with_skills,
    json,
)


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
