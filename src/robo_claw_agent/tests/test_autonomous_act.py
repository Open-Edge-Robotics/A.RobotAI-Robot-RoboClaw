import json
import threading
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
        {"name": "Place 1", "position": {"x": 1.0, "y": 2.0}, "metadata": {"alias_of": "이동가능지점1"}},
    ]
    node._memory.rag_status.return_value = {"enabled": False}
    # Skills Mock
    node._skills = MagicMock()
    node._skills.list_skills.return_value = [{"name": "navigate", "description": "이동"}]
    node._skills.execute.return_value = MagicMock(success=True, result_data={})
    return node


def test_initialize_map_status_param():
    skill = MagicMock()

    # 1. has_map_param이 True인 경우
    node_true = InitializeMapStatus(skill, param_has_map=True)
    blackboard = {}
    node_true._blackboard = blackboard
    status = node_true.tick()
    assert status == NodeStatus.SUCCESS
    assert blackboard["has_map"] is True

    # 2. has_map_param이 False인 경우
    node_false = InitializeMapStatus(skill, param_has_map=False)
    blackboard = {}
    node_false._blackboard = blackboard
    status = node_false.tick()
    assert status == NodeStatus.SUCCESS
    assert blackboard["has_map"] is False


def test_initialize_map_status_auto_no_map():
    skill = MagicMock()
    skill.wait_for_message.return_value = None  # /map 수신 실패 모사

    node = InitializeMapStatus(skill, param_has_map=None)
    blackboard = {}
    node._blackboard = blackboard
    status = node.tick()

    assert status == NodeStatus.SUCCESS
    assert blackboard["has_map"] is False
    skill.wait_for_message.assert_called_once()


def test_check_has_map():
    node = CheckHasMap()

    # blackboard has_map=True
    blackboard = {"has_map": True}
    node._blackboard = blackboard
    assert node.tick() == NodeStatus.SUCCESS

    # blackboard has_map=False
    blackboard = {"has_map": False}
    node._blackboard = blackboard
    assert node.tick() == NodeStatus.FAILURE


def test_ensure_places_registered_skips_when_no_map():
    skill = MagicMock()
    node = EnsurePlacesRegistered(skill)
    blackboard = {"has_map": False}
    node._blackboard = blackboard

    status = node.tick()
    assert status == NodeStatus.SUCCESS
    assert blackboard["places_registered"] is False


def test_ensure_places_registered_skips_when_already_exists(mock_node):
    skill = MagicMock()
    skill.node = mock_node

    node = EnsurePlacesRegistered(skill)
    blackboard = {"has_map": True, "places_registered": False}
    node._blackboard = blackboard

    status = node.tick()
    assert status == NodeStatus.SUCCESS
    assert blackboard["places_registered"] is True
    assert blackboard["place_source_priority"] == "semantic_map"
    mock_node._memory.get_all_objects.assert_called_once()


def test_ensure_places_registered_uses_rag_before_map():
    memory = MagicMock()
    memory.get_all_objects.return_value = []
    memory._vector_store.list_entries.return_value = [
        {
            "text": "창고 위치 x=-1.0 y=-4.0",
            "metadata": {"place_name": "창고", "x": -1.0, "y": -4.0},
        }
    ]

    node_mock = MagicMock()
    node_mock._memory = memory
    skill = MagicMock()
    skill.node = node_mock

    node = EnsurePlacesRegistered(skill)
    blackboard = {"has_map": True, "places_registered": False}
    node._blackboard = blackboard

    status = node.tick()

    assert status == NodeStatus.SUCCESS
    assert blackboard["places_registered"] is True
    assert blackboard["place_source_priority"] == "rag"
    memory.add_object_location.assert_called_once()
    skill.wait_for_message.assert_not_called()


def test_ensure_places_registered_uses_rag_without_map():
    memory = MagicMock()
    memory.get_all_objects.return_value = []
    memory._vector_store.list_entries.return_value = [
        {
            "text": "회의실 위치 x=2.0 y=3.0",
            "metadata": {"place_name": "회의실", "x": 2.0, "y": 3.0},
        }
    ]

    node_mock = MagicMock()
    node_mock._memory = memory
    skill = MagicMock()
    skill.node = node_mock

    node = EnsurePlacesRegistered(skill)
    blackboard = {"has_map": False, "places_registered": False}
    node._blackboard = blackboard

    status = node.tick()

    assert status == NodeStatus.SUCCESS
    assert blackboard["place_source_priority"] == "rag"
    memory.add_object_location.assert_called_once()


def test_ensure_places_registered_does_not_generate_waypoints_from_map(mock_node):
    mock_node._memory.get_all_objects.return_value = []
    mock_node._memory._vector_store.list_entries.return_value = []
    skill = MagicMock()
    skill.node = mock_node

    node = EnsurePlacesRegistered(skill)
    blackboard = {"places_registered": False}
    node._blackboard = blackboard

    status = node.tick()

    assert status == NodeStatus.SUCCESS
    assert blackboard["places_registered"] is False
    assert blackboard.get("place_source_priority") == ""
    skill.wait_for_message.assert_not_called()


def test_llm_decide_action_injects_history_and_places(mock_node):
    skill = MagicMock()
    skill.node = mock_node
    skill.get_map_pose.return_value = {"x": 0.5, "y": 0.5, "frame": "map"}

    # 타 에이전트 Mocking
    mock_node.get_node_names.return_value = [
        "/butler/robo_claw_agent_node",
        "/former/robo_claw_agent_node",
        "/other_ns/robo_claw_agent_node",
        "/some_other_ros_node"
    ]
    mock_node.get_name.return_value = "robo_claw_agent_node"
    mock_node.get_namespace.return_value = "/butler"  # 자기 자신은 butler

    node = LLMDecideAction(skill)
    blackboard = {
        "cycle_count": 3,
        "task_queue": [],
        "task_history": [
            {"cycle": 1, "decided_skill": "navigate", "decided_params": {"target_location": "이동가능지점1"}, "decided_reason": "순회 시작", "success": True},
            {"cycle": 2, "decided_skill": "analyze_scene", "decided_params": {}, "decided_reason": "관찰", "success": True},
        ],
        "current_map_coverage": 100.0,
    }
    node._blackboard = blackboard

    status = node.tick()
    assert status == NodeStatus.SUCCESS

    # LLM chat 호출 시 프롬프트 검증
    mock_node._llm.chat.assert_called_once()
    call_args = mock_node._llm.chat.call_args
    system_prompt = call_args[1]["system_prompt"]
    user_content = call_args[0][0][0]["content"]

    # 프롬프트 내에 이력, 장소 우선순위, 그리고 타 에이전트 정보가 들어갔는지 검증
    assert "이동가능지점1" in system_prompt
    assert "Place 1" not in system_prompt  # 별칭(alias)은 제외되어야 함
    assert "시맨틱 맵 등록 장소" in system_prompt
    assert "RAG 위치 후보" in system_prompt
    assert "맵 자동 발굴 지점" in system_prompt
    assert "최후순위" in system_prompt
    assert "사이클 1" in system_prompt
    assert "x=0.50, y=0.50" in user_content
    assert "former" in system_prompt
    assert "other_ns" in system_prompt
    assert "butler" not in system_prompt  # 자기 자신은 프롬프트의 타 에이전트 목록에서 제외되어야 함




def test_llm_decide_action_excludes_explore_and_motion_without_destination(mock_node):
    mock_node._skills.list_skills.return_value = [
        {"name": "explore", "description": "탐험"},
        {"name": "navigate_to", "description": "이동"},
        {"name": "say", "description": "음성 안내"},
    ]
    mock_node._llm.chat.return_value = (
        '{"skill": "none", "params": {}, "reason": "제자리 관찰 유지"}'
    )
    mock_node._robot_soul = ""
    skill = MagicMock()
    skill.node = mock_node

    node = LLMDecideAction(skill)
    node._blackboard = {
        "cycle_count": 1,
        "task_queue": [],
        "task_history": [],
        "patrol_has_destination": False,
        "scene_analysis": "사람이 보입니다.",
        "detected_objects": ["person"],
    }

    assert node.tick() == NodeStatus.SUCCESS
    system_prompt = mock_node._llm.chat.call_args[1]["system_prompt"]
    skill_section = system_prompt.split("[사용 가능한 스킬]", 1)[1].split("[이동 후보", 1)[0]
    assert "explore" not in skill_section
    assert "navigate_to" not in skill_section
    assert "say" in skill_section
    assert node._blackboard["decided_skill"] == "none"


def test_execute_decided_action_records_history(mock_node):
    skill = MagicMock()
    skill.node = mock_node
    skill.send_user_message = MagicMock()

    node = ExecuteDecidedAction(skill)
    blackboard = {
        "cycle_count": 1,
        "decided_skill": "navigate",
        "decided_params": {"target_location": "이동가능지점1"},
        "decided_reason": "이유",
    }
    node._blackboard = blackboard

    status = node.tick()
    assert status == NodeStatus.SUCCESS

    # task_history 검증
    history = blackboard.get("task_history")
    assert history is not None
    assert len(history) == 1
    assert history[0]["cycle"] == 1
    assert history[0]["decided_skill"] == "navigate"
    assert history[0]["success"] is True


def test_split_semantic_places_excludes_vlm_detected_objects():
    """analyze_scene(kind='object')가 등록한 소형 물체는 이동 후보에서 제외되어야 한다."""
    objects = [
        {"name": "주방", "position": {"x": 1.0, "y": 2.0}, "metadata": {"source": "annotate_map", "kind": "place"}},
        {"name": "cup", "position": {"x": 1.3, "y": 2.1}, "metadata": {"source": "vlm", "kind": "object", "estimated": True}},
        {"name": "가상장애물", "position": {"x": 2.0, "y": 2.0}, "metadata": {"source": "mark_virtual_obstacle", "type": "virtual_obstacle"}},
        {"name": "이동가능지점1", "position": {"x": 3.0, "y": 4.0}, "metadata": {"source": "autonomous_ensure_places", "kind": "place"}},
    ]

    semantic_places, map_generated_places = _split_semantic_places(objects)

    semantic_names = [o["name"] for o in semantic_places]
    assert "주방" in semantic_names
    assert "cup" not in semantic_names
    assert "가상장애물" not in semantic_names
    assert all(o["name"] not in {"cup", "가상장애물"} for o in map_generated_places)
    assert [o["name"] for o in map_generated_places] == ["이동가능지점1"]


def test_extract_rag_location_candidates_excludes_blocked_coordinates():
    """실패 좌표와 가상 장애물은 RAG 순찰 목적지 후보에서 제외해야 한다."""
    memory = MagicMock()
    memory._vector_store.list_entries.return_value = [
        {
            "text": "이동 성공 좌표",
            "metadata": {"type": "navigated_coordinate", "kind": "place", "x": 1.0, "y": 1.0, "name": "성공지점"},
        },
        {
            "text": "이동 실패 좌표",
            "metadata": {"type": "blocked_coordinate", "kind": "blocked", "x": 9.0, "y": 9.0, "name": "실패지점"},
        },
        {
            "text": "가상 장애물 위치",
            "metadata": {"type": "virtual_obstacle", "source": "mark_virtual_obstacle", "x": 8.0, "y": 8.0, "name": "장애물"},
        },
    ]

    candidates = _extract_rag_location_candidates(memory, limit=5)

    names = [c["name"] for c in candidates]
    assert "성공지점" in names
    assert "실패지점" not in names
    assert "장애물" not in names


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

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill._send_spin_goal", send_goal
    )
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


# ---------------------------------------------------------------------------
# Goal 모드 (GoalPlannerNode / CheckGoalAchieved) 테스트
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
    mock_node._robot_soul = ""
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


def test_llm_decide_action_includes_capabilities_in_prompt(mock_node):
    """LLMDecideAction 프롬프트에 capabilities와 위임 강제 가이드가 포함된다."""
    mock_node._skills.list_skills.return_value = [
        {"name": "navigate_to", "description": "이동"},
    ]
    mock_node._llm.chat.return_value = '{"skill": "get_status", "params": {}, "reason": "테스트"}'
    mock_node._robot_soul = ""

    skill = MagicMock()
    skill.node = mock_node
    skill.get_map_pose.return_value = None

    # GRPC_TARGET_PEERS_JSON에 manipulation 동료 설정
    import os
    old_env = os.environ.get("GRPC_TARGET_PEERS_JSON")
    try:
        os.environ["GRPC_TARGET_PEERS_JSON"] = json.dumps([
            {"name": "butler", "description": "팔", "role": "매니퓰레이션", "capabilities": ["manipulation"]},
        ])
        node = LLMDecideAction(skill)
        blackboard = {
            "cycle_count": 1,
            "task_queue": [],
            "task_history": [],
            "current_map_coverage": 100.0,
        }
        node._blackboard = blackboard

        status = node.tick()
        assert status == NodeStatus.SUCCESS

        system_prompt = mock_node._llm.chat.call_args[1]["system_prompt"]
        # capabilities가 프롬프트에 포함되어야 함
        assert "manipulation" in system_prompt
        # 자신에게 manipulation이 없으므로 위임 필수 가이드가 있어야 함
        assert "위임 필수" in system_prompt
    finally:
        if old_env is not None:
            os.environ["GRPC_TARGET_PEERS_JSON"] = old_env
        elif "GRPC_TARGET_PEERS_JSON" in os.environ:
            del os.environ["GRPC_TARGET_PEERS_JSON"]
