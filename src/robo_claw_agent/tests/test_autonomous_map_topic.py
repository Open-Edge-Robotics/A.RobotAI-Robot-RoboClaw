"""Configured map consumers reject unavailable dynamic maps without motion."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.domain.yield_slot import DENIED, Pose2D, YieldRequest
from robo_claw_agent.skills import explore_skill, navigation_skill, yield_slot_service
from robo_claw_agent.skills.autonomous_skill.conditions import CheckMapCoverage
from robo_claw_agent.skills.autonomous_skill.core import NodeStatus
from robo_claw_agent.skills.autonomous_skill.environment_nodes import InitializeMapStatus
from robo_claw_agent.skills.autonomous_skill.execution_nodes import RunExploreOnce
from robo_claw_agent.skills.autonomous_skill.navigation_nodes import ExploreNode
from robo_claw_agent.skills.base import BaseSkill

pytestmark = pytest.mark.unit

CONSUMERS = (CheckMapCoverage, InitializeMapStatus, RunExploreOnce, ExploreNode)


def _map():
    return SimpleNamespace(
        data=[0, 0, 0, 0],
        info=SimpleNamespace(
            height=2,
            width=2,
            resolution=1.0,
            origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0)),
        ),
    )


def _skill(map_message):
    return SimpleNamespace(
        node=SimpleNamespace(),
        get_string_param=MagicMock(return_value="/robot/local_map"),
        wait_for_map_message=MagicMock(return_value=map_message),
        # Old raw subscriptions must not bypass the shared freshness policy.
        wait_for_message=MagicMock(return_value=_map()),
        validate_message_frame=MagicMock(
            side_effect=lambda message, frame, label: BaseSkill.validate_message_frame(
                None, message, frame, label
            )
        ),
    )


@pytest.fixture
def exploration_dependencies(monkeypatch):
    explore_skill._EXPLORE_ACTIVE.clear()
    pose = MagicMock(return_value=(0.0, 0.0))
    goal = MagicMock()
    monkeypatch.setattr(explore_skill, "_get_robot_pose", pose)
    monkeypatch.setattr(explore_skill, "_extract_frontiers", lambda *args: [])
    monkeypatch.setattr(explore_skill, "_cluster_frontiers", lambda *args, **kwargs: [])
    monkeypatch.setattr(explore_skill, "_filter_frontiers_by_radius", lambda *args: [])
    monkeypatch.setattr(explore_skill, "_select_nearest_frontier", lambda *args: None)
    monkeypatch.setattr(navigation_skill, "_send_navigation_goal", goal)
    return pose, goal


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_autonomous_consumers_use_configured_map_helper(consumer, exploration_dependencies):
    skill = _skill(_map())
    bt_node = consumer(skill)

    assert bt_node.tick() == NodeStatus.SUCCESS
    skill.get_string_param.assert_called_once_with({}, "map_topic", "/map")
    skill.wait_for_map_message.assert_called_once_with(
        "/robot/local_map", timeout_sec=3.0 if consumer is InitializeMapStatus else 5.0
    )
    skill.wait_for_message.assert_not_called()


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_rejected_dynamic_map_is_not_used_for_decisions(consumer, exploration_dependencies):
    skill = _skill(None)
    bt_node = consumer(skill)
    pose, goal = exploration_dependencies

    status = bt_node.tick()

    if consumer is InitializeMapStatus:
        assert status == NodeStatus.SUCCESS
        assert bt_node._blackboard["has_map"] is False
    else:
        assert status == NodeStatus.FAILURE
    assert "current_map_coverage" not in bt_node._blackboard
    skill.wait_for_message.assert_not_called()
    pose.assert_not_called()
    goal.assert_not_called()


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_wrong_map_frame_does_not_promote_state_or_send_goals(consumer, exploration_dependencies):
    message = _map()
    message.header = SimpleNamespace(frame_id="odom")
    skill = _skill(message)
    bt_node = consumer(skill)
    pose, goal = exploration_dependencies

    status = bt_node.tick()

    if consumer is InitializeMapStatus:
        assert status == NodeStatus.SUCCESS
        assert bt_node._blackboard["has_map"] is False
    else:
        assert status == NodeStatus.FAILURE
    assert "current_map_coverage" not in bt_node._blackboard
    assert not bt_node._blackboard.get("has_map", False)
    skill.validate_message_frame.assert_called_once_with(message, "map", "OccupancyGrid")
    pose.assert_not_called()
    goal.assert_not_called()


@pytest.mark.parametrize("consumer", CONSUMERS)
def test_matching_configured_map_frame_is_accepted(consumer, exploration_dependencies):
    message = _map()
    message.header = SimpleNamespace(frame_id="robot_map")
    skill = _skill(message)
    skill.node._map_frame = "robot_map"

    assert consumer(skill).tick() == NodeStatus.SUCCESS
    skill.validate_message_frame.assert_called_once_with(message, "robot_map", "OccupancyGrid")


def _node(topic="/robot/local_map", **kwargs):
    return SimpleNamespace(
        has_parameter=lambda name: name == "map_topic",
        get_parameter=lambda name: SimpleNamespace(
            get_parameter_value=lambda: SimpleNamespace(string_value=topic)
        ),
        **kwargs,
    )


@pytest.mark.parametrize(
    "topic, expected", [("/robot/local_map", "/robot/local_map"), ("", "/map")]
)
def test_yield_node_reader_preserves_map_freshness_policy(topic, expected):
    message = _map()
    reader = MagicMock(return_value=message)

    assert yield_slot_service._wait_for_map(_node(topic, wait_for_message=reader)) is message

    assert reader.call_args.args[1] == expected
    assert reader.call_args.kwargs == {
        "timeout_sec": 2.0,
        "use_transient_local": True,
        "max_age_sec": 10.0,
        "fresh_volatile": True,
        "fresh_transient_local": False,
    }


def test_yield_node_without_parameter_api_uses_default_map():
    reader = MagicMock(return_value=_map())
    yield_slot_service._wait_for_map(SimpleNamespace(wait_for_message=reader))
    assert reader.call_args.args[1] == "/map"


def test_yield_registered_skill_uses_its_configured_map_helper():
    skill = _skill(_map())
    registry = SimpleNamespace(
        list_skills=lambda **kwargs: [{"name": "test_skill"}],
        get_skill=lambda name: skill,
    )
    result = yield_slot_service._wait_for_map(_node(_skills=registry))

    assert result is skill.wait_for_map_message.return_value
    skill.get_string_param.assert_called_once_with({}, "map_topic", "/map")
    skill.wait_for_map_message.assert_called_once_with("/robot/local_map", timeout_sec=2.0)
    skill.wait_for_message.assert_not_called()


def test_yield_stale_dynamic_map_denies_without_candidate_search(monkeypatch):
    def reader(_type, _topic, **kwargs):
        # Model a stale volatile sample retained by an old raw reader.
        if kwargs.get("fresh_volatile") and kwargs.get("max_age_sec") == 10.0:
            return None
        return _map()

    from robo_claw_agent.skills.autonomous_skill import place_discovery

    candidates = MagicMock(return_value=[(2.0, 0.0, 1.0)])
    monkeypatch.setattr(place_discovery, "find_reachable_places", candidates)
    node = _node(wait_for_message=reader)
    pose = Pose2D(0.0, 0.0, 0.0)
    context = yield_slot_service.YieldServerContext(
        agent_id="receiver",
        pose_provider=lambda: pose,
        busy_provider=lambda: False,
        manipulation_provider=lambda: False,
        battery_provider=lambda: None,
        candidates_provider=lambda: yield_slot_service._yield_candidates(node, pose),
    )

    decision = yield_slot_service.decide_yield(
        context,
        YieldRequest(
            requester_id="requester",
            target_place="test_place",
            target=pose,
            requester_at_target=False,
        ),
    )

    assert decision.decision == DENIED
    assert decision.reason == "no_safe_spot"
    assert decision.yield_spot is None
    candidates.assert_not_called()


def test_yield_reader_failure_does_not_fall_back_to_other_map_source():
    fallback = _skill(_map())
    registry = SimpleNamespace(
        list_skills=lambda **kwargs: [{"name": "test_skill"}],
        get_skill=lambda name: fallback,
    )
    reader = MagicMock(side_effect=TimeoutError("map unavailable"))

    assert (
        yield_slot_service._wait_for_map(_node(wait_for_message=reader, _skills=registry)) is None
    )
    fallback.wait_for_map_message.assert_not_called()


@pytest.mark.parametrize("registered_skill", [False, True])
def test_yield_wrong_frame_blocks_candidate_discovery(monkeypatch, registered_skill):
    from robo_claw_agent.skills.autonomous_skill import place_discovery

    message = _map()
    message.header = SimpleNamespace(frame_id="odom")
    skill = _skill(message)
    node = _node(wait_for_message=MagicMock(return_value=message))
    if registered_skill:
        node = _node(
            _skills=SimpleNamespace(
                list_skills=lambda **kwargs: [{"name": "test_skill"}],
                get_skill=lambda name: skill,
            )
        )
    candidates = MagicMock(return_value=[(2.0, 0.0, 1.0)])
    monkeypatch.setattr(place_discovery, "find_reachable_places", candidates)

    assert yield_slot_service._yield_candidates(node, Pose2D(0.0, 0.0, 0.0)) == []
    candidates.assert_not_called()
