"""autonomous act goal planner"""

import pytest

pytest.importorskip("rclpy")

from _autonomous_act_helpers import (  # noqa: F401
    GoalPlannerNode,
    MagicMock,
    NodeStatus,
    json,
    mock_node,
)


def test_goal_planner_node_decomposes_goal(mock_node):
    """GoalPlannerNode가 LLM에서 다단계 plan을 받아 task_queue에 저장한다."""
    mock_node._llm.chat.return_value = json.dumps(
        {
            "plan": [
                {"skill": "navigate_to", "params": {"target_name": "거실"}, "reason": "이동"},
                {"skill": "get_status", "params": {}, "reason": "완료 확인"},
            ],
            "goal_achieved": False,
        }
    )
    mock_node._robot_soul = ""
    skill = MagicMock()
    skill.node = mock_node

    node = GoalPlannerNode(skill, goal="거실 쓰레기 버리기")
    blackboard = {
        "cycle_count": 0,
        "task_queue": [],
        "task_history": [],
        "goal": "거실 쓰레기 버리기",
    }
    node._blackboard = blackboard

    status = node.tick()

    assert status == NodeStatus.SUCCESS
    assert len(blackboard["task_queue"]) == 2
    assert blackboard["task_queue"][0]["skill"] == "navigate_to"
    assert blackboard["goal_achieved"] is False
def test_goal_planner_node_empty_plan_marks_achieved(mock_node):
    """goal_achieved=true이면 task_queue를 비우고 goal_achived를 설정한다."""
    mock_node._llm.chat.return_value = json.dumps({"plan": [], "goal_achieved": True})
    mock_node._robot_soul = ""
    skill = MagicMock()
    skill.node = mock_node

    node = GoalPlannerNode(skill, goal="이미 달성된 목표")
    blackboard = {"cycle_count": 0, "task_queue": [], "task_history": []}
    node._blackboard = blackboard

    status = node.tick()

    assert status == NodeStatus.SUCCESS
    assert blackboard["task_queue"] == []
    assert blackboard["goal_achieved"] is True
def test_goal_planner_node_skips_llm_when_queue_has_items(mock_node):
    """task_queue에 잔여 항목이 있으면 LLM 호출 없이 SUCCESS를 반환한다."""
    mock_node._llm.chat.return_value = "should-not-be-called"
    skill = MagicMock()
    skill.node = mock_node

    node = GoalPlannerNode(skill, goal="테스트")
    blackboard = {"task_queue": [{"skill": "get_status", "params": {}}]}
    node._blackboard = blackboard

    status = node.tick()
    assert status == NodeStatus.SUCCESS
    mock_node._llm.chat.assert_not_called()
