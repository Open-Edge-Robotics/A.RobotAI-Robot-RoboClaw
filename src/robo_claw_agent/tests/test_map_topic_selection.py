"""Map topic precedence without ROS nodes, transport, LLMs, or robot motion."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills import explore_skill
from robo_claw_agent.skills.explore_skill import ExploreSkill
from robo_claw_agent.skills.map_skill.analyze import AnalyzeMapSkill
from robo_claw_agent.skills.map_skill.annotate import AnnotateMapSkill
from robo_claw_agent.skills.map_skill.capture import CaptureMapSkill
from robo_claw_agent.skills.map_skill.find_places import FindReachablePlacesSkill
from robo_claw_agent.skills.map_skill.visual import GetMapVisualSkill

pytestmark = pytest.mark.unit

SKILLS = (
    CaptureMapSkill,
    GetMapVisualSkill,
    AnalyzeMapSkill,
    AnnotateMapSkill,
    FindReachablePlacesSkill,
    ExploreSkill,
)


class FakeNode:
    _map_frame = "map"

    def __init__(self, map_topic):
        self.map_topic = map_topic

    def has_parameter(self, name):
        return name == "map_topic" and self.map_topic is not None

    def get_parameter(self, name):
        assert name == "map_topic"
        return SimpleNamespace(
            get_parameter_value=lambda: SimpleNamespace(string_value=self.map_topic)
        )


class InlineThread:
    def __init__(self, *, target, daemon):
        assert daemon is True
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def make_skill(monkeypatch):
    grid = SimpleNamespace(
        header=SimpleNamespace(frame_id="map"),
        info=SimpleNamespace(
            width=30,
            height=30,
            resolution=0.1,
            origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0)),
        ),
        data=[0] * 900,
    )
    receiver = Mock(return_value=grid)
    monkeypatch.setattr(BaseSkill, "wait_for_message", receiver)
    monkeypatch.setattr(BaseSkill, "get_map_pose", lambda self: None)
    monkeypatch.setattr(BaseSkill, "send_user_message", Mock())
    monkeypatch.setattr(explore_skill.threading, "Thread", InlineThread)
    navigation = Mock(side_effect=AssertionError("Robot motion must not be requested"))
    monkeypatch.setattr(explore_skill, "_send_navigation_goal", navigation)
    monkeypatch.setattr("cv2.imwrite", Mock(return_value=True))
    explore_skill._EXPLORE_ACTIVE.clear()

    def create(skill_type, node_topic, *, received=True):
        skill = skill_type()
        skill.set_node(FakeNode(node_topic))
        receiver.return_value = grid if received else None
        return skill, receiver, navigation

    yield create
    explore_skill._EXPLORE_ACTIVE.clear()


def execution_params(topic_params):
    return {
        **topic_params,
        "analyze": False,
        "markers": [{"name": "Point", "x": 1.0, "y": 1.0}],
        "max_iterations": 1,
    }


@pytest.mark.parametrize("skill_type", SKILLS, ids=lambda skill: skill.name)
@pytest.mark.parametrize(
    ("node_topic", "topic_params", "expected"),
    [
        (None, {}, "/map"),
        ("/map", {}, "/map"),
        ("/lio/grid_map", {}, "/lio/grid_map"),
        ("/lio/grid_map", {"map_topic": "/custom/grid"}, "/custom/grid"),
        ("/lio/grid_map", {"map_topic": ""}, "/lio/grid_map"),
        ("/lio/grid_map", {"map_topic": " \t "}, "/lio/grid_map"),
        ("", {}, "/map"),
        (" \t ", {}, "/map"),
        ("/lio/grid_map", {"map_topic": None}, "/lio/grid_map"),
    ],
)
def test_map_topic_precedence(make_skill, skill_type, node_topic, topic_params, expected):
    skill, receiver, navigation = make_skill(skill_type, node_topic)

    result = skill.execute(execution_params(topic_params))

    assert result["success"] is True
    assert receiver.call_count >= 1
    assert all(call.args[1] == expected for call in receiver.call_args_list)
    assert all(call.kwargs["timeout_sec"] > 0 for call in receiver.call_args_list)
    assert all(call.kwargs["use_transient_local"] for call in receiver.call_args_list)
    assert all(call.kwargs["fresh_volatile"] for call in receiver.call_args_list)
    assert all(call.kwargs["max_age_sec"] == 10.0 for call in receiver.call_args_list)
    navigation.assert_not_called()
    if skill_type is ExploreSkill:
        assert not explore_skill._EXPLORE_ACTIVE.is_set()


@pytest.mark.parametrize("skill_type", SKILLS, ids=lambda skill: skill.name)
def test_receive_failure_does_not_retry_another_topic(make_skill, skill_type, caplog):
    skill, receiver, navigation = make_skill(skill_type, "/lio/grid_map", received=False)

    result = skill.execute(execution_params({}))

    receiver.assert_called_once()
    assert receiver.call_args.args[1] == "/lio/grid_map"
    navigation.assert_not_called()
    if skill_type is ExploreSkill:
        assert result["success"] is True  # The public result acknowledges background startup.
        assert not explore_skill._EXPLORE_ACTIVE.is_set()
        assert "Failed to receive map" in caplog.text
    else:
        assert result["success"] is False
        assert "/lio/grid_map" in result["message"]


@pytest.mark.parametrize("skill_type", SKILLS, ids=lambda skill: skill.name)
def test_map_topic_schema_uses_robot_configuration(skill_type):
    schema = skill_type.input_schema["properties"]["map_topic"]

    assert "default" not in schema
    assert "생략" in schema["description"]
    assert "로봇 설정" in schema["description"]
    assert "/map" not in skill_type.description


@pytest.mark.parametrize("skill_type", (CaptureMapSkill, GetMapVisualSkill))
def test_map_metadata_identifies_received_topic(make_skill, skill_type):
    skill, _, _ = make_skill(skill_type, "/lio/grid_map")

    result = skill.execute(execution_params({}))

    metadata = result["map_metadata"]
    assert metadata["map_topic"] == "/lio/grid_map"
    assert metadata["width"] == 30
    assert metadata["height"] == 30
    assert metadata["resolution"] == 0.1
    assert metadata["origin"] == {"x": 0.0, "y": 0.0}


def test_explore_rejects_wrong_map_frame_before_computing_frontiers(make_skill, caplog):
    skill, receiver, navigation = make_skill(ExploreSkill, "/lio/grid_map")
    receiver.return_value.header.frame_id = "odom"

    result = skill.execute(execution_params({}))

    assert result["success"] is True  # Acknowledges background startup only.
    assert "frame_id" in caplog.text
    assert not explore_skill._EXPLORE_ACTIVE.is_set()
    navigation.assert_not_called()
