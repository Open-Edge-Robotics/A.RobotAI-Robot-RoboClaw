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


__all__ = [
    "LLMDecideAction",
    "MagicMock",
    "NodeStatus",
    "ResolveCleanupHandoff",
    "SimpleNamespace",
    "ValidateDecidedAction",
    "_find_connected_peer_with_capability",
    "_has_cleanup_capability",
    "_node_with_skills",
    "json",
]
