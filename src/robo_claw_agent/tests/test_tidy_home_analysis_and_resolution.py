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
