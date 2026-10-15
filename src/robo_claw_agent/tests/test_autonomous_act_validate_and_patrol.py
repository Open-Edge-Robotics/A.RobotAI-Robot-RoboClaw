"""autonomous act validate and patrol"""

import pytest

pytest.importorskip("rclpy")

from _autonomous_act_helpers import (  # noqa: F401
    EnsurePlacesRegistered,
    MagicMock,
    NodeStatus,
    PatrolNextPlaceNode,
    ValidateDecidedAction,
    mock_node,
    patch,
)


def test_validate_decided_action_rejects_unregistered_skill():
    node = MagicMock()
    node._skills.has_skill.return_value = False
    skill = MagicMock()
    skill.node = node

    validate = ValidateDecidedAction(skill)
    blackboard = {"decided_skill": "no_such_skill", "decided_params": {}}
    validate._blackboard = blackboard

    status = validate.tick()

    assert status == NodeStatus.FAILURE
    assert blackboard["decided_skill"] == ""
    assert "decision_invalid_reason" in blackboard
def test_validate_decided_action_rejects_unresolvable_target():
    node = MagicMock()
    node._skills.has_skill.return_value = True
    node._memory = MagicMock()
    skill = MagicMock()
    skill.node = node

    validate = ValidateDecidedAction(skill)
    blackboard = {
        "decided_skill": "navigate_to",
        "decided_params": {"target_name": "존재하지않는장소"},
    }
    validate._blackboard = blackboard

    with patch(
        "robo_claw_agent.skills.navigation_skill.core._resolve_target_coordinates",
        return_value=None,
    ):
        status = validate.tick()

    assert status == NodeStatus.FAILURE
    assert blackboard["decided_skill"] == ""
def test_validate_decided_action_accepts_resolvable_target():
    node = MagicMock()
    node._skills.has_skill.return_value = True
    node._skills.get_skill.return_value.side_effects = ("base_motion",)
    node._memory = MagicMock()
    node._memory.get_all_objects.return_value = [
        {
            "name": "주방",
            "position": {"x": 1.0, "y": 2.0},
            "metadata": {"kind": "place"},
        }
    ]
    skill = MagicMock()
    skill.node = node

    validate = ValidateDecidedAction(skill)
    blackboard = {
        "decided_skill": "navigate_to",
        "decided_params": {"target_name": "주방"},
    }
    validate._blackboard = blackboard

    with patch(
        "robo_claw_agent.skills.navigation_skill.core._resolve_target_coordinates",
        return_value={"position": {"x": 1.0, "y": 2.0}, "metadata": {}},
    ):
        status = validate.tick()

    assert status == NodeStatus.SUCCESS
    assert blackboard["decided_skill"] == "navigate_to"
def test_validate_decided_action_rejects_resolvable_but_unremembered_target():
    node = MagicMock()
    node._skills.has_skill.return_value = True
    node._skills.get_skill.return_value.side_effects = ("base_motion",)
    node._memory = MagicMock()
    node._memory.get_all_objects.return_value = []
    skill = MagicMock()
    skill.node = node

    validate = ValidateDecidedAction(skill)
    blackboard = {
        "decided_skill": "navigate_to",
        "decided_params": {"target_name": "가상장애물"},
    }
    validate._blackboard = blackboard

    with patch(
        "robo_claw_agent.skills.navigation_skill.core._resolve_target_coordinates",
        return_value={"position": {"x": 8.0, "y": 8.0}, "metadata": {}},
    ):
        status = validate.tick()

    assert status == NodeStatus.FAILURE
    assert "기억된 순찰 장소" in blackboard["decision_invalid_reason"]
def test_patrol_next_place_node_picks_oldest_last_seen():
    node = MagicMock()
    memory = MagicMock()
    memory.get_all_objects.return_value = [
        {
            "name": "최근방문",
            "position": {"x": 1.0, "y": 1.0},
            "last_seen": "2026-07-10T12:00:00",
            "metadata": {"source": "annotate_map", "kind": "place"},
        },
        {
            "name": "오래된곳",
            "position": {"x": 2.0, "y": 2.0},
            "last_seen": "2026-01-01T00:00:00",
            "metadata": {"source": "annotate_map", "kind": "place"},
        },
    ]
    node._memory = memory
    skill = MagicMock()
    skill.node = node

    patrol_node = PatrolNextPlaceNode(skill)
    blackboard = {}
    patrol_node._blackboard = blackboard

    with patch(
        "robo_claw_agent.skills.navigation_skill.patrol.visit_place",
        return_value=(True, "도착"),
    ) as mock_visit:
        status = patrol_node.tick()

    assert status == NodeStatus.SUCCESS
    mock_visit.assert_called_once_with(node, "오래된곳")
    assert blackboard["patrol_last_place"] == "오래된곳"
    assert blackboard["patrol_last_success"] is True
    # last_seen 갱신을 위해 재등록되었는지 확인
    memory.add_object_location.assert_called_once()
    assert memory.add_object_location.call_args[0][0] == "오래된곳"
def test_patrol_next_place_node_no_candidates_returns_success():
    node = MagicMock()
    memory = MagicMock()
    memory.get_all_objects.return_value = []
    node._memory = memory
    skill = MagicMock()
    skill.node = node

    patrol_node = PatrolNextPlaceNode(skill)
    blackboard = {}
    patrol_node._blackboard = blackboard

    status = patrol_node.tick()

    assert status == NodeStatus.SUCCESS
    assert blackboard["patrol_last_place"] == ""
    assert blackboard["patrol_last_success"] is False
    assert blackboard["patrol_has_destination"] is False
    assert blackboard["stationary_observation_only"] is True
def test_patrol_next_place_node_uses_remembered_rag_coordinates():
    node = MagicMock()
    memory = MagicMock()
    places = []
    memory.get_all_objects.side_effect = lambda: places
    memory._vector_store.list_entries.return_value = [
        {
            "text": "거실 위치",
            "metadata": {
                "name": "거실",
                "x": 1.5,
                "y": -2.0,
                "kind": "place",
                "frame_id": "map",
            },
        }
    ]

    def save_place(name, x, y, metadata=None, **_kwargs):
        places.append(
            {
                "name": name,
                "position": {"x": x, "y": y},
                "metadata": metadata or {},
            }
        )

    memory.add_object_location.side_effect = save_place
    node._memory = memory
    skill = MagicMock()
    skill.node = node
    blackboard = {"places_registered": False}

    ensure_places = EnsurePlacesRegistered(skill)
    ensure_places._blackboard = blackboard
    assert ensure_places.tick() == NodeStatus.SUCCESS

    patrol_node = PatrolNextPlaceNode(skill)
    patrol_node._blackboard = blackboard
    with patch(
        "robo_claw_agent.skills.navigation_skill.patrol.visit_place",
        return_value=(True, "도착"),
    ) as mock_visit:
        status = patrol_node.tick()

    assert status == NodeStatus.SUCCESS
    mock_visit.assert_called_once_with(node, "거실")
    assert blackboard["patrol_has_destination"] is True
    assert blackboard["stationary_observation_only"] is False
def test_patrol_next_place_node_cools_down_failed_destination():
    node = MagicMock()
    memory = MagicMock()
    memory.get_all_objects.return_value = [
        {
            "name": "거실",
            "position": {"x": 1.0, "y": 2.0},
            "metadata": {"kind": "place", "source": "annotate_map"},
        }
    ]
    node._memory = memory
    skill = MagicMock()
    skill.node = node

    patrol_node = PatrolNextPlaceNode(skill)
    blackboard = {}
    patrol_node._blackboard = blackboard

    with patch(
        "robo_claw_agent.skills.navigation_skill.patrol.visit_place",
        return_value=(False, "Nav2 goal rejected"),
    ) as mock_visit:
        assert patrol_node.tick() == NodeStatus.SUCCESS
        assert patrol_node.tick() == NodeStatus.SUCCESS

    mock_visit.assert_called_once_with(node, "거실")
    assert blackboard["patrol_has_destination"] is False
    assert blackboard["stationary_observation_only"] is True
