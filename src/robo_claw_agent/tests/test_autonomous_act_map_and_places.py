"""autonomous act map and places"""

import pytest

pytest.importorskip("rclpy")

from _autonomous_act_helpers import (  # noqa: F401
    CheckHasMap,
    EnsurePlacesRegistered,
    ExecuteDecidedAction,
    InitializeMapStatus,
    LLMDecideAction,
    MagicMock,
    NodeStatus,
    _extract_rag_location_candidates,
    _split_semantic_places,
    mock_node,
)


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
        "/some_other_ros_node",
    ]
    mock_node.get_name.return_value = "robo_claw_agent_node"
    mock_node.get_namespace.return_value = "/butler"  # 자기 자신은 butler

    node = LLMDecideAction(skill)
    blackboard = {
        "cycle_count": 3,
        "task_queue": [],
        "task_history": [
            {
                "cycle": 1,
                "decided_skill": "navigate",
                "decided_params": {"target_location": "이동가능지점1"},
                "decided_reason": "순회 시작",
                "success": True,
            },
            {
                "cycle": 2,
                "decided_skill": "analyze_scene",
                "decided_params": {},
                "decided_reason": "관찰",
                "success": True,
            },
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
    assert (
        "butler" not in system_prompt
    )  # 자기 자신은 프롬프트의 타 에이전트 목록에서 제외되어야 함
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
        {
            "name": "주방",
            "position": {"x": 1.0, "y": 2.0},
            "metadata": {"source": "annotate_map", "kind": "place"},
        },
        {
            "name": "cup",
            "position": {"x": 1.3, "y": 2.1},
            "metadata": {"source": "vlm", "kind": "object", "estimated": True},
        },
        {
            "name": "가상장애물",
            "position": {"x": 2.0, "y": 2.0},
            "metadata": {"source": "mark_virtual_obstacle", "type": "virtual_obstacle"},
        },
        {
            "name": "이동가능지점1",
            "position": {"x": 3.0, "y": 4.0},
            "metadata": {"source": "autonomous_ensure_places", "kind": "place"},
        },
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
            "metadata": {
                "type": "navigated_coordinate",
                "kind": "place",
                "x": 1.0,
                "y": 1.0,
                "name": "성공지점",
            },
        },
        {
            "text": "이동 실패 좌표",
            "metadata": {
                "type": "blocked_coordinate",
                "kind": "blocked",
                "x": 9.0,
                "y": 9.0,
                "name": "실패지점",
            },
        },
        {
            "text": "가상 장애물 위치",
            "metadata": {
                "type": "virtual_obstacle",
                "source": "mark_virtual_obstacle",
                "x": 8.0,
                "y": 8.0,
                "name": "장애물",
            },
        },
    ]

    candidates = _extract_rag_location_candidates(memory, limit=5)

    names = [c["name"] for c in candidates]
    assert "성공지점" in names
    assert "실패지점" not in names
    assert "장애물" not in names
def test_extract_rag_location_candidates_parses_name_and_coords_from_text():
    """메타데이터에 이름이나 x,y가 없어도 RAG 텍스트에서 장소명과 좌표를 복원해야 한다."""
    memory = MagicMock()
    memory._vector_store.list_entries.return_value = [
        {
            "text": "이동 성공 좌표: x=1.50, y=2.00 (주방)",
            "metadata": {
                "type": "navigated_coordinate",
                "kind": "place",
                "x": 1.5,
                "y": 2.0,
                "source": "navigate_to",
            },
        },
        {
            "text": "거실의 위치는 x: -2.3, y: 4.1 입니다.",
            "metadata": {"type": "manual_note"},
        },
    ]

    candidates = _extract_rag_location_candidates(memory, limit=5)

    assert len(candidates) == 2
    names = [c["name"] for c in candidates]
    assert "주방" in names
    assert "거실" in names
    living_room = next(c for c in candidates if c["name"] == "거실")
    assert living_room["position"] == {"x": -2.3, "y": 4.1}
def test_patrol_gather_place_candidates_includes_rag():
    """순찰 지점 수집(_gather_place_candidates)에 시맨틱 맵뿐만 아니라 RAG 후보도 포함되어야 한다."""
    from robo_claw_agent.skills.autonomous_skill.patrol_nodes import _gather_place_candidates

    memory = MagicMock()
    memory.get_all_objects.return_value = []
    memory._vector_store.list_entries.return_value = [
        {
            "text": "이동 성공 좌표: x=3.0, y=1.0 (충전소)",
            "metadata": {
                "type": "navigated_coordinate",
                "kind": "place",
                "x": 3.0,
                "y": 1.0,
                "source": "navigate_to",
            },
        }
    ]

    candidates = _gather_place_candidates(memory)

    names = [c["name"] for c in candidates]
    assert "충전소" in names
