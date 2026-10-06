import pytest

pytest.importorskip("rclpy")

import threading
from unittest.mock import MagicMock, patch

import pytest
from robo_claw_agent.skills.autonomous_skill.action_nodes import SkillChainNode
from robo_claw_agent.skills.autonomous_skill.core import NodeStatus
from robo_claw_agent.skills.autonomous_skill.skill import ConditionReactiveSkill


@pytest.fixture
def mock_skill():
    skill = MagicMock()
    node = MagicMock()
    skill.node = node
    return skill


# ── SkillChainNode 테스트 ──────────────────────────────────────────────────

def test_skill_chain_node_empty_chain(mock_skill):
    node = SkillChainNode(mock_skill, [])
    node._blackboard = {}

    # 빈 체인은 SUCCESS
    _ = node.tick()  # RUNNING (스레드 시작)
    node._done.wait(timeout=2.0)
    result = node.tick()
    assert result == NodeStatus.SUCCESS


def test_skill_chain_node_single_success(mock_skill):
    mock_result = MagicMock()
    mock_result.success = True
    mock_skill.node._skills.execute.return_value = mock_result

    node = SkillChainNode(mock_skill, [{"skill": "approach_object", "params": {}}])
    node._blackboard = {}

    node.tick()
    node._done.wait(timeout=2.0)
    result = node.tick()

    assert result == NodeStatus.SUCCESS
    mock_skill.node._skills.execute.assert_called_once_with(
        "approach_object", {}, timeout_sec=120.0
    )


def test_skill_chain_node_failure_stops_chain(mock_skill):
    fail_result = MagicMock()
    fail_result.success = False
    fail_result.message = "실패"

    mock_skill.node._skills.execute.return_value = fail_result

    chain = [
        {"skill": "approach_object", "params": {}},
        {"skill": "capture_image", "params": {}},
    ]
    node = SkillChainNode(mock_skill, chain)
    node._blackboard = {}

    node.tick()
    node._done.wait(timeout=2.0)
    result = node.tick()

    assert result == NodeStatus.FAILURE
    # 첫 번째 스킬만 호출됐는지 확인
    assert mock_skill.node._skills.execute.call_count == 1


def test_skill_chain_node_sequential_execution(mock_skill):
    execution_order = []

    def fake_execute(skill_name, params, timeout_sec=120.0):
        execution_order.append(skill_name)
        r = MagicMock()
        r.success = True
        return r

    mock_skill.node._skills.execute.side_effect = fake_execute

    chain = [
        {"skill": "approach_object", "params": {}},
        {"skill": "capture_image", "params": {}},
        {"skill": "send_message", "params": {"message": "이미지입니다"}},
    ]
    node = SkillChainNode(mock_skill, chain)
    node._blackboard = {}

    node.tick()
    node._done.wait(timeout=3.0)
    node.tick()

    assert execution_order == ["approach_object", "capture_image", "send_message"]


def test_skill_chain_node_running_before_done(mock_skill):
    ready = threading.Event()
    proceed = threading.Event()

    def slow_execute(skill_name, params, timeout_sec=120.0):
        ready.set()
        proceed.wait(timeout=2.0)
        r = MagicMock()
        r.success = True
        return r

    mock_skill.node._skills.execute.side_effect = slow_execute

    node = SkillChainNode(mock_skill, [{"skill": "slow_skill", "params": {}}])
    node._blackboard = {}

    # 첫 tick: 스레드 시작, RUNNING 반환
    status = node.tick()
    assert status == NodeStatus.RUNNING

    ready.wait(timeout=1.0)

    # 실행 중에도 RUNNING
    status = node.tick()
    assert status == NodeStatus.RUNNING

    proceed.set()
    node._done.wait(timeout=2.0)

    # 완료 후 SUCCESS
    status = node.tick()
    assert status == NodeStatus.SUCCESS


# ── ConditionReactiveSkill 테스트 ─────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_autonomous_active():
    from robo_claw_agent.skills.autonomous_skill import globals as ag
    ag._AUTONOMOUS_ACTIVE.clear()
    yield
    ag._AUTONOMOUS_ACTIVE.clear()


@pytest.fixture
def mock_node():
    node = MagicMock()
    node.has_parameter.return_value = False
    return node


def test_condition_reactive_requires_trigger_object(mock_node):
    skill = ConditionReactiveSkill()
    skill.set_node(mock_node)

    result = skill.execute(
        {
            "foreground_task": "navigate",
            "foreground_params": {"target_location": "거실"},
            "on_trigger_skills": [{"skill": "approach_object", "params": {}}],
        }
    )

    assert result["success"] is False
    assert "trigger_object" in result["message"]


def test_condition_reactive_requires_on_trigger_skills(mock_node):
    skill = ConditionReactiveSkill()
    skill.set_node(mock_node)

    result = skill.execute(
        {
            "foreground_task": "navigate",
            "foreground_params": {"target_location": "거실"},
            "trigger_object": "person",
        }
    )

    assert result["success"] is False
    assert "on_trigger_skills" in result["message"]


def test_condition_reactive_invalid_foreground_task(mock_node):
    skill = ConditionReactiveSkill()
    skill.set_node(mock_node)

    result = skill.execute(
        {
            "foreground_task": "patrol",
            "foreground_params": {},
            "trigger_object": "person",
            "on_trigger_skills": [{"skill": "approach_object", "params": {}}],
        }
    )

    assert result["success"] is False
    assert "foreground_task" in result["message"]


def test_condition_reactive_navigate_starts_successfully(mock_node, monkeypatch):
    skill = ConditionReactiveSkill()
    skill.set_node(mock_node)

    mock_active = MagicMock()
    mock_active.is_set.return_value = False
    monkeypatch.setattr(
        "robo_claw_agent.skills.autonomous_skill.skill._AUTONOMOUS_ACTIVE", mock_active
    )

    with patch("threading.Thread") as mock_thread:
        result = skill.execute(
            {
                "foreground_task": "navigate",
                "foreground_params": {"target_location": "거실"},
                "trigger_object": "person",
                "on_trigger_skills": [{"skill": "approach_object", "params": {}}],
            }
        )

        assert result["success"] is True
        assert result["foreground_task"] == "navigate"
        assert result["trigger_object"] == "person"
        mock_thread.assert_called_once()


def test_condition_reactive_explore_starts_successfully(mock_node, monkeypatch):
    skill = ConditionReactiveSkill()
    skill.set_node(mock_node)

    mock_active = MagicMock()
    mock_active.is_set.return_value = False
    monkeypatch.setattr(
        "robo_claw_agent.skills.autonomous_skill.skill._AUTONOMOUS_ACTIVE", mock_active
    )

    with patch("threading.Thread") as mock_thread:
        result = skill.execute(
            {
                "foreground_task": "explore",
                "foreground_params": {},
                "trigger_object": "potted plant",
                "on_trigger_skills": [
                    {"skill": "approach_object", "params": {}},
                    {"skill": "capture_image", "params": {}},
                ],
            }
        )

        assert result["success"] is True
        assert result["foreground_task"] == "explore"
        assert result["trigger_object"] == "potted plant"
        mock_thread.assert_called_once()


@patch("robo_claw_agent.skills.autonomous_skill.skill.ExploreNode")
@patch("robo_claw_agent.skills.autonomous_skill.skill.MonitorObjectNode")
@patch("robo_claw_agent.skills.autonomous_skill.skill.SkillChainNode")
def test_condition_reactive_explore_uses_explore_node(
    mock_chain, mock_monitor, mock_explore, mock_node, monkeypatch
):
    skill = ConditionReactiveSkill()
    skill.set_node(mock_node)
    skill.send_user_message = MagicMock()

    mock_active = MagicMock()
    mock_active.is_set.side_effect = [False, True, False]
    monkeypatch.setattr(
        "robo_claw_agent.skills.autonomous_skill.skill._AUTONOMOUS_ACTIVE", mock_active
    )

    with patch("threading.Thread") as mock_thread:
        skill.execute(
            {
                "foreground_task": "explore",
                "foreground_params": {"exploration_radius": 4.0},
                "trigger_object": "potted plant",
                "on_trigger_skills": [{"skill": "approach_object", "params": {}}],
            }
        )

        target_func = mock_thread.call_args[1]["target"]
        with patch(
            "robo_claw_agent.skills.autonomous_skill.skill.SequenceNode.tick",
            return_value=NodeStatus.SUCCESS,
        ):
            target_func()

    mock_explore.assert_called_once()
    mock_monitor.assert_called_once()
    mock_chain.assert_called_once()
