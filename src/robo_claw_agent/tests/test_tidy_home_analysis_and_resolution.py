"""tidy home analysis and resolution"""

import pytest

pytestmark = pytest.mark.unit

from _tidy_home_helpers import (  # noqa: F401
    TidyHomeSkill,
    _place,
)


def test_tidy_home_summary_does_not_claim_unobserved_area_was_clear():
    messages = []
    skill = TidyHomeSkill()
    skill.send_user_message = messages.append

    skill._report_summary(
        "room",
        [{"place": "안방", "status": "partial_observation", "partial_observation": True}],
        False,
    )

    assert "정리 대상 유무" in messages[0]
    assert "확정할 수 없습니다" in messages[0]
def test_tidy_home_withholds_actions_when_start_orientation_is_not_restored():
    from robo_claw_agent.skills.autonomous_skill.globals import _AUTONOMOUS_ACTIVE

    messages = []
    cleanup_calls = []
    skill = TidyHomeSkill()
    skill.send_user_message = messages.append
    skill._visit_place = lambda _place: (True, "arrived")
    skill._analyze_current_scene = lambda _place: {
        "success": True,
        "partial": False,
        "orientation_restored": False,
        "analysis": "정면 방향 바닥에 수건이 있습니다.",
        "detected_objects": ["towel"],
    }
    skill._handle_cleanup_target = lambda **kwargs: cleanup_calls.append(kwargs) or "handled"
    _AUTONOMOUS_ACTIVE.set()

    skill._run_cleanup("room", [_place("안방", 1.0, 2.0)])

    assert cleanup_calls == []
    assert "안방: orientation_unrestored" in messages[-1]
    assert any("후속 정리 동작은 실행하지 않았습니다" in message for message in messages)
def test_tidy_home_marks_no_candidate_as_partial_when_some_views_failed():
    from robo_claw_agent.skills.autonomous_skill.globals import _AUTONOMOUS_ACTIVE

    messages = []
    skill = TidyHomeSkill()
    skill.send_user_message = messages.append
    skill._visit_place = lambda _place: (True, "arrived")
    skill._analyze_current_scene = lambda _place: {
        "success": True,
        "partial": True,
        "failed_directions": ["좌측"],
        "analysis": "정리 대상이 보이지 않습니다.",
        "detected_objects": [],
    }
    _AUTONOMOUS_ACTIVE.set()

    skill._run_cleanup("room", [_place("안방", 1.0, 2.0)])

    summary = messages[-1]
    assert "안방: partial_observation" in summary
    assert "미관찰" in summary
    assert "정리 대상 유무" in summary
def test_tidy_home_skill_requires_a_target_for_room_scope():
    result = TidyHomeSkill().execute({"scope": "room"})

    assert result["success"] is False
    assert result["failure_reason"] == "target_location_required"
def test_tidy_home_resolves_room_via_target_coordinates():
    from types import SimpleNamespace

    memory = SimpleNamespace(
        get_all_objects=lambda: [],
        get_object_location=lambda name: (
            {"name": "주방", "position": {"x": 2.5, "y": -1.5}} if name == "주방" else None
        ),
        _vector_store=SimpleNamespace(list_entries=lambda: []),
    )
    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=memory, _skills=None)

    places, error = skill._collect_places("room", "주방")

    assert error is None
    assert len(places) == 1
    assert places[0]["name"] == "주방"
    assert places[0]["position"] == {"x": 2.5, "y": -1.5}
def test_tidy_home_resolves_room_via_rag_navigated_coordinate():
    from types import SimpleNamespace

    entries = [
        {
            "text": "이동 성공 좌표: x=1.50, y=2.00 (주방)",
            "metadata": {
                "type": "navigated_coordinate",
                "kind": "place",
                "x": 1.5,
                "y": 2.0,
                "source": "navigate_to",
            },
        }
    ]
    memory = SimpleNamespace(
        get_all_objects=lambda: [],
        get_object_location=lambda _name: None,
        search_knowledge=lambda _q, **_kw: entries,
        _vector_store=SimpleNamespace(list_entries=lambda: entries),
    )
    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=memory, _skills=None)

    places, error = skill._collect_places("room", "주방")

    assert error is None
    assert len(places) == 1
    assert places[0]["name"] == "주방"
    assert places[0]["position"]["x"] == 1.5
    assert places[0]["position"]["y"] == 2.0
def test_tidy_home_executes_demo_motion_locally_when_capable(monkeypatch):
    """시연 가능한 로컬 로봇(1호)은 정리 대상 확인 시 no_action으로 끝나지 않고 demo_motion_executed를 반환한다."""
    from types import SimpleNamespace

    executed_motions = []

    class _Skills:
        def has_skill(self, name):
            return name in {
                "navigate_to",
                "execute_cloid_motion",
                "list_cloid_motions",
                "cleanup_demo",
            }

        def execute(self, name, params, **kwargs):
            if name == "list_cloid_motions":
                return SimpleNamespace(
                    success=True,
                    message="ok",
                    result_data={
                        "motions": [
                            {"motion_id": 60, "motion_name": "scan", "pre_id": None},
                            {"motion_id": 22, "motion_name": "greeting", "pre_id": None},
                        ]
                    },
                )
            if name == "execute_cloid_motion":
                executed_motions.append(params)
                return SimpleNamespace(success=True, message="executed")
            return SimpleNamespace(success=True, message="ok")

    skills = _Skills()
    node = SimpleNamespace(
        _memory=None,
        _skills=skills,
        get_parameter=lambda name: SimpleNamespace(value="[22, 60]"),
    )
    skill = TidyHomeSkill()
    skill.set_node(node)
    skill.send_user_message = lambda _msg: None

    status = skill._handle_cleanup_target(
        place_name="안방",
        scene="안방 바닥에 포장지와 쿠션이 놓여 있습니다.",
        objects=[{"name": "포장지"}, {"name": "쿠션"}],
        cycle=0,
    )

    assert status == "demo_motion_executed"
    assert len(executed_motions) == 1
    assert executed_motions[0]["motion_id"] in [22, 60]


def test_tidy_home_home_scope_collects_only_semantic_map_places():
    """집 범위 정리는 시맨틱 맵 장소만 순회하고 RAG의 중복 후보를 섮지 않는다."""
    from types import SimpleNamespace

    semantic_places = [
        _place("주방", 1.5, 2.0, kind="place"),
        _place("거실", -2.0, 3.0, kind="place"),
    ]
    rag_entries = [
        {
            "text": f"이동 성공 좌표: x={1.5 + index * 0.01:.2f}, y=2.00 (주방)",
            "metadata": {
                "type": "navigated_coordinate",
                "kind": "place",
                "source": "navigate_to",
            },
        }
        for index in range(20)
    ]
    memory = SimpleNamespace(
        get_all_objects=lambda: list(semantic_places),
        get_object_location=lambda _name: None,
        _vector_store=SimpleNamespace(list_entries=lambda: rag_entries),
    )
    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=memory, _skills=None)

    places, error = skill._collect_places("home", "")

    assert error is None
    assert [place["name"] for place in places] == ["주방", "거실"]
    assert skill._places_truncated is False


def test_tidy_home_home_scope_reports_no_places_when_semantic_map_is_empty():
    """RAG에만 장소가 있으면 집 범위 순회 대상으로 쓰지 않고 미기억 상태로 끝난다."""
    from types import SimpleNamespace

    rag_entries = [
        {
            "text": "이동 성공 좌표: x=1.5, y=2.0 (주방)",
            "metadata": {
                "type": "navigated_coordinate",
                "kind": "place",
                "source": "navigate_to",
            },
        }
    ]
    memory = SimpleNamespace(
        get_all_objects=lambda: [],
        get_object_location=lambda _name: None,
        _vector_store=SimpleNamespace(list_entries=lambda: rag_entries),
    )
    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=memory, _skills=None)

    places, error = skill._collect_places("home", "")

    assert places == []
    assert error == "no_remembered_places"


def _home_memory(place_count):
    from types import SimpleNamespace

    semantic_places = [
        _place(f"장소{index}", float(index), float(index * 2), kind="place")
        for index in range(place_count)
    ]
    return SimpleNamespace(
        get_all_objects=lambda: list(semantic_places),
        get_object_location=lambda _name: None,
        _vector_store=SimpleNamespace(list_entries=lambda: []),
    )


def test_tidy_home_home_scope_collects_every_remembered_place_without_cap():
    """기억된 장소가 20곳을 넘어도 집 범위 순회 목록은 전부 포함해야 한다."""
    from types import SimpleNamespace

    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=_home_memory(25), _skills=None)

    places, error = skill._collect_places("home", "", max_places=0)

    assert error is None
    assert len(places) == 25
    assert skill._places_truncated is False


def test_tidy_home_home_scope_honours_explicit_max_places():
    from types import SimpleNamespace

    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=_home_memory(5), _skills=None)
    skill._place_limit = 3

    places, error = skill._collect_places("home", "", max_places=3)

    assert error is None
    assert len(places) == 3
    assert skill._places_truncated is True


def test_tidy_home_rejects_invalid_max_places():
    result = TidyHomeSkill().execute({"scope": "home", "max_places": -1})

    assert result["success"] is False
    assert result["failure_reason"] == "invalid_max_places"


def test_tidy_home_home_sweep_ends_after_last_remembered_place():
    """집 범위 정리는 마지막 장소 확인 뒤 모든 장소를 마쳤다고 알리고 종료해야 한다."""
    from types import SimpleNamespace

    from robo_claw_agent.skills.autonomous_skill.globals import (
        _AUTONOMOUS_ACTIVE,
        is_autonomous_active,
    )

    messages = []
    visited = []
    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=None, _skills=None, _agent_id="test")
    skill.send_user_message = messages.append
    skill._visit_place = lambda name: visited.append(name) or (True, "arrived")
    skill._analyze_current_scene = lambda _name: {
        "success": True,
        "partial": False,
        "orientation_restored": True,
        "analysis": "정리 대상이 보이지 않습니다.",
        "detected_objects": [],
    }
    _AUTONOMOUS_ACTIVE.set()

    skill._run_cleanup(
        "home", [_place("주방", 1.5, 2.0), _place("거실", -2.0, 3.0)], run_id=""
    )

    assert visited == ["주방", "거실"]
    assert is_autonomous_active() is False
    assert any("[1/2] '주방'" in message for message in messages)
    assert any("[2/2] '거실'" in message for message in messages)
    assert any("모두 확인하고 정리 작업을 마쳤습니다" in message for message in messages)
