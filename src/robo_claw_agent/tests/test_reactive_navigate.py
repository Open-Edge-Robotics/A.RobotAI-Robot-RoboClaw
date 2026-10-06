import pytest

pytest.importorskip("rclpy")

from unittest.mock import MagicMock, patch

import pytest
from robo_claw_agent.skills.autonomous_skill.core import NodeStatus
from robo_claw_agent.skills.autonomous_skill.skill import ReactiveNavigateSkill


@pytest.fixture
def mock_node():
    node = MagicMock()
    node.get_clock().now().to_msg.return_value = MagicMock()
    return node

def test_reactive_navigate_skill_requires_target_location(mock_node):
    skill = ReactiveNavigateSkill()
    skill.set_node(mock_node)

    result = skill.execute({"interrupt_object": "person"})

    assert result["success"] is False
    assert "target_location 파라미터가 필요합니다" in result["message"]

def test_reactive_navigate_skill_starts_successfully(mock_node, monkeypatch):
    skill = ReactiveNavigateSkill()
    skill.set_node(mock_node)

    # Mocking _AUTONOMOUS_ACTIVE to return False for is_set()
    mock_active = MagicMock()
    mock_active.is_set.return_value = False
    monkeypatch.setattr("robo_claw_agent.skills.autonomous_skill.skill._AUTONOMOUS_ACTIVE", mock_active)

    # Mocking thread to not actually run the loop in this unit test
    with patch("threading.Thread") as mock_thread:
        result = skill.execute({"target_location": "kitchen", "interrupt_object": "person"})

        assert result["success"] is True
        assert "kitchen" in result["message"]
        mock_thread.assert_called_once()

@patch("robo_claw_agent.skills.autonomous_skill.skill.MonitorObjectNode")
@patch("robo_claw_agent.skills.autonomous_skill.skill.NavigateNode")
def test_reactive_navigate_bt_structure(mock_nav, mock_monitor, mock_node, monkeypatch):
    skill = ReactiveNavigateSkill()
    skill.set_node(mock_node)

    # Mocking send_user_message
    skill.send_user_message = MagicMock()

    # Mocking globals
    mock_active = MagicMock()
    mock_active.is_set.side_effect = [False, True, False] # False for check at start, True for loop, False to exit
    monkeypatch.setattr("robo_claw_agent.skills.autonomous_skill.skill._AUTONOMOUS_ACTIVE", mock_active)

    # We want to check if it tries to build the tree.
    with patch("threading.Thread") as mock_thread:
        skill.execute({"target_location": "kitchen", "interrupt_object": "person"})

        # Get the target function (_run_bt_loop)
        target_func = mock_thread.call_args[1]["target"]

        # To prevent actual execution of root.tick(), we can patch SequenceNode.tick
        with patch("robo_claw_agent.skills.autonomous_skill.skill.SequenceNode.tick", return_value=NodeStatus.SUCCESS):
            target_func()

        # Verify nodes were instantiated
        mock_nav.assert_called()
        mock_monitor.assert_called()
