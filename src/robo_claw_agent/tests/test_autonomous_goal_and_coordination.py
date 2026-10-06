"""자율행동 goal 모드 및 협업 capabilities 단위 테스트 (ROS2 불필요).

이 테스트는 rclpy 없이도 실행 가능합니다. conftest.py 에서 agent_node.utils 를
로드하고, cooperate_skill 은 lazy import 를 사용해 rclpy 없이 import 됩니다.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# robo_claw_msgs는 실제 generated 인터페이스를 사용한다. 메시지 모듈을
# sys.modules에 전역 stub으로 주입하면 같은 pytest 세션의 다른 테스트가
# AgentStatus/SkillResult/Action 타입을 잘못 보게 되므로 stub을 만들지 않는다.
from robo_claw_agent.skills.autonomous_skill.actions import (
    GoalPlannerNode,
    _discover_peer_agents,
    _find_peer_with_capability,
    _has_manipulation_capability,
    _self_capabilities,
)
from robo_claw_agent.skills.autonomous_skill.core import NodeStatus


@pytest.fixture
def mock_node():
    node = MagicMock()
    node._llm = MagicMock()
    node._llm.chat.return_value = '{"skill": "get_status", "params": {}, "reason": "테스트"}'
    node._memory = MagicMock()
    node._memory.get_all_objects.return_value = []
    node._memory.rag_status.return_value = {"enabled": False}
    node._memory._vector_store = MagicMock()
    node._memory._vector_store.list_entries.return_value = []
    node._skills = MagicMock()
    node._skills.list_skills.return_value = [{"name": "navigate_to", "description": "이동"}]
    node._skills.execute.return_value = MagicMock(success=True, result_data={})
    node._robot_soul = ""
    node.get_node_names.return_value = []
    node.get_name.return_value = "robo_claw_agent_node"
    node.get_namespace.return_value = "/myrobot"
    return node


# ---------------------------------------------------------------------------
# GoalPlannerNode 테스트
# ---------------------------------------------------------------------------


def test_goal_planner_node_decomposes_goal(mock_node):
    """GoalPlannerNode가 LLM에서 다단계 plan을 받아 task_queue에 저장한다."""
    mock_node._llm.chat.return_value = json.dumps({
        "plan": [
            {"skill": "navigate_to", "params": {"target_name": "거실"}, "reason": "이동"},
            {"skill": "get_status", "params": {}, "reason": "완료 확인"},
        ],
        "goal_achieved": False,
    })

    skill = MagicMock()
    skill.node = mock_node

    node = GoalPlannerNode(skill, goal="거실 쓰레기 버리기")
    blackboard = {"cycle_count": 0, "task_queue": [], "task_history": [], "goal": "거실 쓰레기 버리기"}
    node._blackboard = blackboard

    status = node.tick()

    assert status == NodeStatus.SUCCESS
    assert len(blackboard["task_queue"]) == 2
    assert blackboard["task_queue"][0]["skill"] == "navigate_to"
    assert blackboard["goal_achieved"] is False


def test_goal_planner_node_empty_plan_marks_achieved(mock_node):
    """goal_achieved=true이면 task_queue를 비우고 goal_achived를 설정한다."""
    mock_node._llm.chat.return_value = json.dumps({"plan": [], "goal_achieved": True})

    skill = MagicMock()
    skill.node = mock_node

    node = GoalPlannerNode(skill, goal="이미 달성된 목표")
    blackboard = {"cycle_count": 0, "task_queue": [], "task_history": []}
    node._blackboard = blackboard

    status = node.tick()

    assert status == NodeStatus.SUCCESS
    assert blackboard["task_queue"] == []
    assert blackboard["goal_achieved"] is True


def test_goal_planner_node_empty_plan_without_completion_is_failure(mock_node):
    """빈 계획은 명시적 goal_achieved 증거가 없으면 목표 성공으로 취급하지 않는다."""
    mock_node._llm.chat.return_value = json.dumps({"plan": [], "goal_achieved": False})

    skill = MagicMock()
    skill.node = mock_node
    node = GoalPlannerNode(skill, goal="아직 달성되지 않은 목표")
    blackboard = {"cycle_count": 0, "task_queue": [], "task_history": []}
    node._blackboard = blackboard

    status = node.tick()

    assert status == NodeStatus.FAILURE
    assert blackboard["goal_achieved"] is not True
    assert blackboard["planning_failure_count"] == 1


def test_goal_planner_node_skips_llm_when_queue_has_items(mock_node):
    """task_queue에 잔여 항목이 있으면 LLM 호출 없이 SUCCESS를 반환한다."""
    skill = MagicMock()
    skill.node = mock_node

    node = GoalPlannerNode(skill, goal="테스트")
    blackboard = {"task_queue": [{"skill": "get_status", "params": {}}]}
    node._blackboard = blackboard

    status = node.tick()
    assert status == NodeStatus.SUCCESS
    mock_node._llm.chat.assert_not_called()


# ---------------------------------------------------------------------------
# capabilities / 동료 위임 매칭 테스트
# ---------------------------------------------------------------------------


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
    peers_json = json.dumps([
        {"name": "butler", "description": "팔 로봇", "role": "매니퓰레이션", "capabilities": ["manipulation", "navigation"]},
        {"name": "former", "description": "이동 로봇", "role": "탐색", "capabilities": ["navigation"]},
    ])
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
    peers_json = json.dumps([
        {"name": "former", "description": "이동 로봇", "role": "탐색", "capabilities": ["navigation"]},
        {"name": "butler", "description": "팔 로봇", "role": "매니퓰레이션", "capabilities": ["manipulation"]},
    ])
    monkeypatch.setenv("GRPC_TARGET_PEERS_JSON", peers_json)

    node = MagicMock()
    node.get_node_names.return_value = []
    node.get_name.return_value = "robo_claw_agent_node"
    node.get_namespace.return_value = "/myrobot"

    peer = _find_peer_with_capability(node, "manipulation")
    assert peer == "butler"


def test_find_peer_with_capability_returns_none_when_no_match(monkeypatch):
    """매칭되는 동료가 없으면 None을 반환한다."""
    peers_json = json.dumps([
        {"name": "former", "description": "이동 로봇", "role": "탐색", "capabilities": ["navigation"]},
    ])
    monkeypatch.setenv("GRPC_TARGET_PEERS_JSON", peers_json)

    node = MagicMock()
    node.get_node_names.return_value = []
    node.get_name.return_value = "robo_claw_agent_node"
    node.get_namespace.return_value = "/myrobot"

    peer = _find_peer_with_capability(node, "manipulation")
    assert peer is None


def test_find_peer_with_capability_role_keyword_fallback(monkeypatch):
    """capabilities 배열이 비어 있어도 role 문자열에서 키워드 매칭으로 찾는다."""
    peers_json = json.dumps([
        {"name": "butler", "description": "팔 로봇", "role": "매니퓰레이션 가능", "capabilities": []},
    ])
    monkeypatch.setenv("GRPC_TARGET_PEERS_JSON", peers_json)

    node = MagicMock()
    node.get_node_names.return_value = []
    node.get_name.return_value = "robo_claw_agent_node"
    node.get_namespace.return_value = "/myrobot"

    peer = _find_peer_with_capability(node, "manipulation")
    assert peer == "butler"


# ---------------------------------------------------------------------------
# CoordinatePeerTaskSkill 테스트
# ---------------------------------------------------------------------------


def _make_coord_node(llm_resp=None):
    """coordinate_peer_task 테스트용 노드 생성."""
    llm = MagicMock()
    if llm_resp is not None:
        llm.chat.return_value = llm_resp

    node = SimpleNamespace()
    node._llm = llm
    node.get_node_names = MagicMock(return_value=[])
    node._call_peer_client = MagicMock()
    node._call_peer_client.wait_for_service.return_value = True

    resp = SimpleNamespace(success=True, result_message="완료", error_message="")
    future = MagicMock()

    def add_done_callback(cb):
        cb(future)

    future.add_done_callback.side_effect = add_done_callback
    future.result.return_value = resp
    node._call_peer_client.call_async.return_value = future

    node._send_msg_client = MagicMock()
    node._send_msg_client.service_is_ready.return_value = False
    return node


def test_coord_peer_task_missing_peer_name():
    from robo_claw_agent.skills.cooperate_skill import CoordinatePeerTaskSkill

    node = _make_coord_node()
    skill = CoordinatePeerTaskSkill()
    skill.set_node(node)
    result = skill.execute({"instruction": "컵 옮겨줘"})

    assert result["success"] is False
    assert "peer_name" in result["message"]


def test_coord_peer_task_missing_instruction():
    from robo_claw_agent.skills.cooperate_skill import CoordinatePeerTaskSkill

    node = _make_coord_node()
    skill = CoordinatePeerTaskSkill()
    skill.set_node(node)
    result = skill.execute({"peer_name": "robot_a"})

    assert result["success"] is False
    assert "instruction" in result["message"]


def test_coord_peer_task_decompose_and_sequential_send():
    from robo_claw_agent.skills.cooperate_skill import CoordinatePeerTaskSkill

    llm_resp = json.dumps({
        "steps": [
            {"instruction": "거실로 이동해줘"},
            {"instruction": "컵을 들어줘"},
            {"instruction": "주방으로 가줘"},
            {"instruction": "싱크대에 놔줘"},
        ]
    })
    node = _make_coord_node(llm_resp=llm_resp)
    skill = CoordinatePeerTaskSkill()
    skill.set_node(node)

    result = skill.execute({"peer_name": "robot_a", "instruction": "거실로 이동해 컵을 들고 주방에 놔줘"})

    assert result["success"] is True
    assert result["steps_completed"] == 4
    assert result["steps_total"] == 4
    assert node._llm.chat.call_count == 1
    assert node._call_peer_client.call_async.call_count == 4


def test_coord_peer_task_decompose_string_steps():
    from robo_claw_agent.skills.cooperate_skill import CoordinatePeerTaskSkill

    llm_resp = json.dumps({"steps": ["거실로 이동해줘", "컵을 들어줘"]})
    node = _make_coord_node(llm_resp=llm_resp)
    skill = CoordinatePeerTaskSkill()
    skill.set_node(node)

    result = skill.execute({"peer_name": "robot_a", "instruction": "컵 옮겨줘"})

    assert result["success"] is True
    assert result["steps_completed"] == 2


def test_coord_peer_task_decompose_fails():
    from robo_claw_agent.skills.cooperate_skill import CoordinatePeerTaskSkill

    node = _make_coord_node(llm_resp="invalid response")
    skill = CoordinatePeerTaskSkill()
    skill.set_node(node)

    result = skill.execute({"peer_name": "robot_a", "instruction": "컵 옮겨줘"})

    assert result["success"] is False
    assert "분해" in result["message"]


def test_coord_peer_task_step_failure_aborts():
    from robo_claw_agent.skills.cooperate_skill import CoordinatePeerTaskSkill

    llm_resp = json.dumps({
        "steps": [
            {"instruction": "거실로 이동해줘"},
            {"instruction": "컵을 들어줘"},
        ]
    })
    node = _make_coord_node(llm_resp=llm_resp)

    # 첫 단계 실패 응답으로 교체
    fail_resp = SimpleNamespace(success=False, result_message="", error_message="이동 실패")
    future = MagicMock()

    def add_done_callback(cb):
        cb(future)

    future.add_done_callback.side_effect = add_done_callback
    future.result.return_value = fail_resp
    node._call_peer_client.call_async.return_value = future

    skill = CoordinatePeerTaskSkill()
    skill.set_node(node)

    result = skill.execute({
        "peer_name": "robot_a",
        "instruction": "거실로 이동해 컵을 들어줘",
        "max_retries": 0,
    })

    assert result["success"] is False
    assert result["steps_completed"] == 0
    assert node._call_peer_client.call_async.call_count == 1


# ---------------------------------------------------------------------------
# QueryPeerCapabilitiesSkill 테스트
# ---------------------------------------------------------------------------


def test_query_peer_capabilities_success():
    from robo_claw_agent.skills.cooperate_skill import QueryPeerCapabilitiesSkill

    client = MagicMock()
    client.wait_for_service.return_value = True

    resp = SimpleNamespace(
        success=True,
        result_message='["navigate_to", "grasp", "place"]',
        error_message="",
    )
    future = MagicMock()

    def add_done_callback(cb):
        cb(future)

    future.add_done_callback.side_effect = add_done_callback
    future.result.return_value = resp
    client.call_async.return_value = future

    node = SimpleNamespace()
    node._call_peer_client = client

    skill = QueryPeerCapabilitiesSkill()
    skill.set_node(node)
    result = skill.execute({"peer_name": "robot_a"})

    assert result["success"] is True
    assert "navigate_to" in result["capabilities"]
    assert "grasp" in result["capabilities"]


def test_query_peer_capabilities_non_json_fallback():
    from robo_claw_agent.skills.cooperate_skill import QueryPeerCapabilitiesSkill

    client = MagicMock()
    client.wait_for_service.return_value = True

    resp = SimpleNamespace(
        success=True,
        result_message="저는 이동과 카메라만 가능합니다.",
        error_message="",
    )
    future = MagicMock()

    def add_done_callback(cb):
        cb(future)

    future.add_done_callback.side_effect = add_done_callback
    future.result.return_value = resp
    client.call_async.return_value = future

    node = SimpleNamespace()
    node._call_peer_client = client

    skill = QueryPeerCapabilitiesSkill()
    skill.set_node(node)
    result = skill.execute({"peer_name": "robot_a"})

    assert result["success"] is True
    assert result["capabilities"] == []
