import json
import threading
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("rclpy")
from robo_claw_agent.skills.autonomous_skill.actions import (
    EnsurePlacesRegistered,
    ExecuteDecidedAction,
    GoalPlannerNode,
    InitializeMapStatus,
    LLMDecideAction,
    ValidateDecidedAction,
    _discover_peer_agents,
    _extract_rag_location_candidates,
    _find_peer_with_capability,
    _has_manipulation_capability,
    _self_capabilities,
    _split_semantic_places,
)
from robo_claw_agent.skills.autonomous_skill.conditions import CheckHasMap
from robo_claw_agent.skills.autonomous_skill.core import NodeStatus
from robo_claw_agent.skills.autonomous_skill.execution_nodes import LocalObservationNode
from robo_claw_agent.skills.autonomous_skill.patrol_nodes import (
    InterestingSceneGate,
    PatrolNextPlaceNode,
)


def _ready_failure_future():
    """ListPeers 호출이 즉시 실패 응답을 돌려주는 future를 만든다(테스트 지연 방지)."""
    future = Future()
    future.set_result(SimpleNamespace(success=False, error_message="no peers", peers=[]))
    return future
@pytest.fixture
def mock_node():
    node = MagicMock()
    # LLM Mock
    node._llm = MagicMock()
    node._llm.chat.return_value = '{"skill": "get_status", "params": {}, "reason": "테스트"}'
    # Memory Mock
    node._memory = MagicMock()
    node._memory.get_all_objects.return_value = [
        {"name": "이동가능지점1", "position": {"x": 1.0, "y": 2.0}, "metadata": {}},
        {
            "name": "Place 1",
            "position": {"x": 1.0, "y": 2.0},
            "metadata": {"alias_of": "이동가능지점1"},
        },
    ]
    node._memory.rag_status.return_value = {"enabled": False}
    # Skills Mock
    node._skills = MagicMock()
    node._skills.list_skills.return_value = [{"name": "navigate", "description": "이동"}]
    node._skills.execute.return_value = MagicMock(success=True, result_data={})
    # peer 탐색이 실제 ListPeers gRPC 타임아웃(최대 10초)을 기다리지 않도록
    # 즉시 실패 응답을 돌려주는 클라이언트를 준비한다.
    node._list_peers_client = MagicMock()
    node._list_peers_client.wait_for_service.return_value = True
    node._list_peers_client.call_async.return_value = _ready_failure_future()
    return node


__all__ = [
    "CheckHasMap",
    "EnsurePlacesRegistered",
    "ExecuteDecidedAction",
    "Future",
    "GoalPlannerNode",
    "InitializeMapStatus",
    "InterestingSceneGate",
    "LLMDecideAction",
    "LocalObservationNode",
    "MagicMock",
    "NodeStatus",
    "PatrolNextPlaceNode",
    "SimpleNamespace",
    "ValidateDecidedAction",
    "_discover_peer_agents",
    "_extract_rag_location_candidates",
    "_find_peer_with_capability",
    "_has_manipulation_capability",
    "_ready_failure_future",
    "_self_capabilities",
    "_split_semantic_places",
    "json",
    "mock_node",
    "patch",
    "pytest",
    "threading",
]
