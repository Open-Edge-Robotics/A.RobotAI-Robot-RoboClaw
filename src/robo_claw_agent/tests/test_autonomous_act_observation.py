"""autonomous act observation"""

import pytest

pytest.importorskip("rclpy")

from _autonomous_act_helpers import (  # noqa: F401
    InterestingSceneGate,
    LocalObservationNode,
    MagicMock,
    NodeStatus,
    SimpleNamespace,
    mock_node,
    threading,
)


def test_interesting_scene_gate_suppresses_repeated_scene_events():
    node = InterestingSceneGate(MagicMock())
    blackboard = {"detected_objects": ["person"], "scene_analysis": "사람이 보입니다."}
    node._blackboard = blackboard

    assert node.tick() == NodeStatus.SUCCESS
    assert node.tick() == NodeStatus.FAILURE

    blackboard["detected_objects"] = []
    blackboard["scene_analysis"] = "특이사항이 없습니다."
    assert node.tick() == NodeStatus.FAILURE
    blackboard["detected_objects"] = ["trash"]
    assert node.tick() == NodeStatus.SUCCESS
def test_local_observation_scans_once_until_patrol_destination_returns():
    scan_skill = MagicMock()
    scan_skill.execute.return_value = {"success": True, "message": "스캔 완료"}
    skill = MagicMock()
    skill.node = SimpleNamespace()
    node = LocalObservationNode(skill, scan_factory=lambda cancel_event: scan_skill)
    blackboard = {"patrol_has_destination": False}
    node._blackboard = blackboard

    assert node.tick() == NodeStatus.SUCCESS
    assert node.tick() == NodeStatus.SUCCESS
    scan_skill.execute.assert_called_once()
    assert blackboard["stationary_scan_done"] is True
    assert blackboard["stationary_observation_only"] is True

    blackboard["patrol_has_destination"] = True
    assert node.tick() == NodeStatus.SUCCESS
    assert blackboard["stationary_scan_done"] is False
    assert blackboard["stationary_observation_only"] is False
def test_scan_room_does_not_send_goal_when_cancelled_before_start(monkeypatch):
    from robo_claw_agent.skills.perception_skill import scan as scan_module
    from robo_claw_agent.skills.perception_skill.scan import ScanRoomSkill

    monkeypatch.setattr(scan_module.globals, "_VISION_MSGS_AVAILABLE", True)
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill._get_nav2_dependency_error",
        lambda: None,
    )
    sent_goals = []
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill._send_spin_goal",
        lambda *args, **kwargs: sent_goals.append(args) or (False, "should not run", None),
    )

    scanner = ScanRoomSkill(cancel_event=threading.Event())
    scanner.set_node(SimpleNamespace(has_parameter=lambda _name: False))
    scanner.send_user_message = lambda _message: None

    result = scanner.execute({"step_deg": 90.0})

    assert result["success"] is False
    assert "취소" in result["message"]
    assert sent_goals == []
def test_scan_room_stops_after_current_goal_is_cancelled(monkeypatch):
    from robo_claw_agent.skills.perception_skill import scan as scan_module
    from robo_claw_agent.skills.perception_skill.scan import ScanRoomSkill

    monkeypatch.setattr(scan_module.globals, "_VISION_MSGS_AVAILABLE", True)
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill._spin_time_allowance_sec",
        lambda _angle: 1.0,
    )
    cancel_event = threading.Event()
    cancel_event.set()
    sent_goals = []

    def send_goal(*_args, **_kwargs):
        sent_goals.append(True)
        return True, "accepted", object()

    def wait_for_goal(*_args, **_kwargs):
        cancel_event.clear()
        return False, "취소되었습니다."

    monkeypatch.setattr("robo_claw_agent.skills.navigation_skill._send_spin_goal", send_goal)
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill._wait_for_goal_result", wait_for_goal
    )

    scanner = ScanRoomSkill(cancel_event=cancel_event)
    scanner.set_node(SimpleNamespace(has_parameter=lambda _name: False))
    scanner.send_user_message = lambda _message: None

    result = scanner.execute({"step_deg": 90.0})

    assert result["success"] is False
    assert len(sent_goals) == 1
    assert "취소" in result["message"]
