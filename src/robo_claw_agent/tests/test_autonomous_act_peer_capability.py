"""autonomous act peer capability"""

import pytest

pytest.importorskip("rclpy")

from _autonomous_act_helpers import (  # noqa: F401
    LLMDecideAction,
    MagicMock,
    NodeStatus,
    _discover_peer_agents,
    _find_peer_with_capability,
    _has_manipulation_capability,
    _self_capabilities,
    json,
    mock_node,
)


def test_self_capabilities_detects_manipulation(mock_node):
    """grasp/place 스킬이 있으면 manipulation 능력이 감지된다."""
    mock_node._skills.list_skills.return_value = [
        {"name": "navigate_to", "description": "이동"},
        {"name": "grasp", "description": "파지"},
        {"name": "place", "description": "배치"},
    ]
    caps = _self_capabilities(mock_node)
    assert "manipulation" in caps
    assert "navigation" in caps
    assert _has_manipulation_capability(mock_node) is True
def test_self_capabilities_no_manipulation(mock_node):
    """grasp/place 스킬이 없으면 manipulation 능력이 없다."""
    mock_node._skills.list_skills.return_value = [
        {"name": "navigate_to", "description": "이동"},
        {"name": "analyze_scene", "description": "장면 분석"},
    ]
    caps = _self_capabilities(mock_node)
    assert "manipulation" not in caps
    assert _has_manipulation_capability(mock_node) is False
def test_discover_peer_agents_reads_capabilities(monkeypatch):
    """GRPC_TARGET_PEERS_JSON의 capabilities 필드가 파싱된다."""

    peers_json = json.dumps(
        [
            {
                "name": "butler",
                "description": "팔 로봇",
                "role": "매니퓰레이션",
                "capabilities": ["manipulation", "navigation"],
            },
            {
                "name": "former",
                "description": "이동 로봇",
                "role": "탐색",
                "capabilities": ["navigation"],
            },
        ]
    )
    monkeypatch.setenv("GRPC_TARGET_PEERS_JSON", peers_json)

    node = MagicMock()
    node.get_node_names.return_value = []
    node.get_name.return_value = "robo_claw_agent_node"
    node.get_namespace.return_value = "/myrobot"

    agents = _discover_peer_agents(node)
    assert "butler" in agents
    assert "former" in agents
    assert "manipulation" in agents["butler"]["capabilities"]
    assert "navigation" in agents["former"]["capabilities"]
def test_find_peer_with_capability_finds_manipulation(monkeypatch):
    """manipulation 능력을 가진 동료를 찾는다."""

    peers_json = json.dumps(
        [
            {
                "name": "former",
                "description": "이동 로봇",
                "role": "탐색",
                "capabilities": ["navigation"],
            },
            {
                "name": "butler",
                "description": "팔 로봇",
                "role": "매니퓰레이션",
                "capabilities": ["manipulation"],
            },
        ]
    )
    monkeypatch.setenv("GRPC_TARGET_PEERS_JSON", peers_json)

    node = MagicMock()
    node.get_node_names.return_value = []
    node.get_name.return_value = "robo_claw_agent_node"
    node.get_namespace.return_value = "/myrobot"

    peer = _find_peer_with_capability(node, "manipulation")
    assert peer == "butler"
def test_find_peer_with_capability_returns_none_when_no_match(monkeypatch):
    """매칭되는 동료가 없으면 None을 반환한다."""
    peers_json = json.dumps(
        [
            {
                "name": "former",
                "description": "이동 로봇",
                "role": "탐색",
                "capabilities": ["navigation"],
            },
        ]
    )
    monkeypatch.setenv("GRPC_TARGET_PEERS_JSON", peers_json)

    node = MagicMock()
    node.get_node_names.return_value = []
    node.get_name.return_value = "robo_claw_agent_node"
    node.get_namespace.return_value = "/myrobot"

    peer = _find_peer_with_capability(node, "manipulation")
    assert peer is None
def test_llm_decide_action_includes_capabilities_in_prompt(mock_node):
    """LLMDecideAction 프롬프트에 capabilities와 위임 강제 가이드가 포함된다."""
    mock_node._skills.list_skills.return_value = [
        {"name": "navigate_to", "description": "이동"},
    ]
    mock_node._llm.chat.return_value = '{"skill": "get_status", "params": {}, "reason": "테스트"}'
    mock_node._robot_soul = ""
    mock_node.get_node_names.return_value = ["/butler/robo_claw_agent_node"]

    skill = MagicMock()
    skill.node = mock_node
    skill.get_map_pose.return_value = None

    # GRPC_TARGET_PEERS_JSON에 manipulation 동료 설정
    import os

    old_env = os.environ.get("GRPC_TARGET_PEERS_JSON")
    try:
        os.environ["GRPC_TARGET_PEERS_JSON"] = json.dumps(
            [
                {
                    "name": "butler",
                    "description": "팔",
                    "role": "매니퓰레이션",
                    "capabilities": ["manipulation"],
                },
            ]
        )
        node = LLMDecideAction(skill)
        blackboard = {
            "cycle_count": 1,
            "task_queue": [],
            "task_history": [],
            "current_map_coverage": 100.0,
        }
        node._blackboard = blackboard

        status = node.tick()
        assert status == NodeStatus.SUCCESS

        system_prompt = mock_node._llm.chat.call_args[1]["system_prompt"]
        # capabilities가 프롬프트에 포함되어야 함
        assert "manipulation" in system_prompt
        # 자신에게 manipulation이 없으므로 위임 필수 가이드가 있어야 함
        assert "위임 필수" in system_prompt
    finally:
        if old_env is not None:
            os.environ["GRPC_TARGET_PEERS_JSON"] = old_env
        elif "GRPC_TARGET_PEERS_JSON" in os.environ:
            del os.environ["GRPC_TARGET_PEERS_JSON"]
