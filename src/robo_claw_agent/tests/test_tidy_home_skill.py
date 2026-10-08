from __future__ import annotations

import random

import pytest
from robo_claw_agent.skills.tidy_home_skill import (
    TidyHomeSkill,
    _cleanup_plan_has_complete_placement,
    _has_ambiguous_cleanup_indication,
    _has_cleanup_indication,
    _resolve_cleanup_places,
    _resolve_cleanup_plan_destinations,
    _safe_cleanup_disposal_targets,
    _select_cloid_indicator_motion,
)

pytestmark = pytest.mark.unit


def _place(name, x, y, **metadata):
    return {
        "name": name,
        "position": {"x": x, "y": y},
        "metadata": metadata,
    }


def _motion(motion_id, name, *, pre_id=None, intent="", description="", mask=""):
    return {
        "motion_id": motion_id,
        "motion_name": name,
        "pre_id": pre_id,
        "intent_id": intent,
        "description": description,
        "mask": mask,
    }


def test_home_scope_returns_unique_places_with_coordinates():
    places = [
        _place("안방", 1.0, 2.0),
        _place("주방", 3.0, 4.0),
        _place("안방 별칭", 1.0, 2.0),
        {"name": "좌표 없는 거실", "position": {}},
    ]

    selected, error = _resolve_cleanup_places("home", "", places)

    assert error is None
    assert [place["name"] for place in selected] == ["안방", "주방"]


def test_room_scope_resolves_exact_name_before_aliases():
    places = [_place("침실", 1.0, 2.0), _place("안방", 5.0, 6.0)]

    selected, error = _resolve_cleanup_places("room", "안방", places)

    assert error is None
    assert [place["name"] for place in selected] == ["안방"]


def test_room_scope_reports_missing_or_ambiguous_location():
    missing, missing_error = _resolve_cleanup_places("room", "서재", [_place("안방", 1, 2)])
    ambiguous, ambiguous_error = _resolve_cleanup_places(
        "room", "master bedroom", [_place("안방", 1, 2), _place("침실", 3, 4)]
    )

    assert missing == []
    assert missing_error == "requested_location_not_found"
    assert ambiguous == []
    assert ambiguous_error == "requested_location_ambiguous"


def test_cleanup_detection_requires_clear_disposable_waste():
    assert _has_cleanup_indication([{"name": "wrapper"}], "테이블 아래 포장지가 있습니다.") is True
    assert _has_cleanup_indication([], "정리해야 할 물건이 보입니다.") is False
    assert _has_cleanup_indication([], "정리할 대상이 보이지 않습니다.") is False
    assert _has_cleanup_indication(["person"], "사람이 서 있습니다.") is False


def test_ambiguous_or_hazardous_items_require_user_clarification():
    assert _has_ambiguous_cleanup_indication([{"name": "cup"}], "테이블 위 컵이 보입니다.") is True
    assert _has_ambiguous_cleanup_indication([], "바닥에 유리 조각이 있습니다.") is True
    assert (
        _has_ambiguous_cleanup_indication([{"name": "cup"}], "정리할 대상이 보이지 않습니다.")
        is False
    )


def test_cleanup_disposal_destination_requires_curated_nonestimated_pose():
    from types import SimpleNamespace

    pose = {
        "frame_id": "base_link",
        "position": {"x": 0.4, "y": -0.2, "z": 0.3},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    }
    memory = SimpleNamespace(
        get_all_objects=lambda: [
            {
                "name": "쓰레기통",
                "metadata": {"cleanup_disposal_target": True, "target_pose": pose},
            },
            {
                "name": "휴지통",
                "metadata": {
                    "cleanup_disposal_target": True,
                    "estimated": True,
                    "target_pose": pose,
                },
            },
            {"name": "컵", "metadata": {"cleanup_disposal_target": True, "target_pose": pose}},
        ]
    )

    assert _safe_cleanup_disposal_targets(memory) == [{"name": "쓰레기통", "target_pose": pose}]


def test_cleanup_plan_replaces_planner_pose_with_curated_disposal_pose():
    pose = {
        "frame_id": "map",
        "position": {"x": 3.0, "y": 1.0, "z": 0.4},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    }
    actions = [
        {"skill": "adaptive_pick_object", "params": {"target_object": "wrapper"}},
        {"skill": "place", "params": {"target_object": "쓰레기통", "target_pose": {"x": 999}}},
    ]

    assert (
        _resolve_cleanup_plan_destinations(actions, [{"name": "쓰레기통", "target_pose": pose}])
        is True
    )
    assert actions[1]["params"]["target_pose"] == pose
    assert (
        _resolve_cleanup_plan_destinations(actions, [{"name": "컵", "target_pose": pose}]) is False
    )


def test_cleanup_plan_requires_a_placement_after_every_pick():
    assert (
        _cleanup_plan_has_complete_placement(
            [
                {"skill": "adaptive_pick_object", "params": {"target_object": "wrapper"}},
                {"skill": "place", "params": {"target_pose": {}}},
            ]
        )
        is True
    )
    assert (
        _cleanup_plan_has_complete_placement(
            [{"skill": "adaptive_pick_object", "params": {"target_object": "wrapper"}}]
        )
        is False
    )
    assert (
        _cleanup_plan_has_complete_placement([{"skill": "place", "params": {"target_pose": {}}}])
        is False
    )


def test_cloid_indicator_selects_only_approved_prerequisite_free_motion():
    motions = [
        _motion(22, "scan", intent="탐색 모션", description="좌우 탐색", mask="WH"),
        _motion(59, "wipe_1", intent="식탁청소", description="행주 잡기", mask="WLRH"),
        _motion(60, "wipe_2", pre_id=59, intent="식탁청소", description="식탁 닦기"),
        _motion(147, "grab box", intent="VS", description="박스 잡기"),
        _motion(128, "task ready pose", intent="VS", description="작업준비자세"),
    ]

    selected = _select_cloid_indicator_motion(motions, [22, 59, 60, 147, 128], rng=random.Random(2))

    assert selected["motion_id"] in {22, 128}
    assert selected["pre_id"] is None
    assert selected["motion_id"] not in {59, 60, 147}


def test_cloid_indicator_avoids_immediate_repeat_when_an_alternative_exists():
    motions = [
        _motion(22, "scan"),
        _motion(128, "task ready pose"),
    ]

    selected = _select_cloid_indicator_motion(
        motions, [22, 128], rng=random.Random(1), previous_motion_id=22
    )

    assert selected["motion_id"] == 128


def test_cloid_indicator_returns_none_when_allowlist_has_no_current_safe_motion():
    motions = [_motion(60, "wipe_2", pre_id=59)]

    assert _select_cloid_indicator_motion(motions, [60], rng=random.Random(0)) is None


def test_home_cleanup_visits_each_remembered_place_once_and_stops_on_cancel(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import globals as autonomous_globals

    skill = TidyHomeSkill()
    skill.node = object()
    messages = []
    visits = []
    monkeypatch.setattr(skill, "send_user_message", messages.append)
    monkeypatch.setattr(
        skill,
        "_analyze_current_scene",
        lambda _place: {
            "success": True,
            "analysis": "정리할 대상이 보이지 않습니다.",
            "detected_objects": [],
        },
    )
    monkeypatch.setattr(skill, "_cloid_indicator_enabled", lambda: False)
    original_event = autonomous_globals._AUTONOMOUS_ACTIVE
    original_event.set()

    def visit(place_name):
        visits.append(place_name)
        if place_name == "주방":
            original_event.clear()
        return True, "도착했습니다."

    monkeypatch.setattr(skill, "_visit_place", visit)
    skill._run_cleanup(
        "home",
        [
            _place("주방", 1, 2),
            _place("거실", 3, 4),
        ],
    )

    assert visits == ["주방"]
    assert any("정리 작업이 종료됐습니다" in message for message in messages)
    assert any("완료 여부를 확정할 수 없습니다" in message for message in messages)
    assert not original_event.is_set()


def test_cloid_activity_indicator_executes_one_allowlisted_motion_and_avoids_repeat():
    from types import SimpleNamespace

    skill = TidyHomeSkill()
    messages = []
    calls = []
    motions = [_motion(22, "scan"), _motion(128, "task ready pose")]

    class FakeSkills:
        def has_skill(self, name):
            return name in {"list_cloid_motions", "execute_cloid_motion"}

        def execute(self, name, params, **kwargs):
            calls.append((name, params, kwargs))
            if name == "list_cloid_motions":
                return SimpleNamespace(success=True, message="ok", result_data={"motions": motions})
            return SimpleNamespace(success=True, message="started")

    node = SimpleNamespace(
        _skills=FakeSkills(),
        get_parameter=lambda _name: SimpleNamespace(value="[22, 128]"),
    )
    skill.node = node
    skill.send_user_message = messages.append

    skill._run_cloid_activity_indicator()
    skill._run_cloid_activity_indicator()

    executed = [call for call in calls if call[0] == "execute_cloid_motion"]
    assert len(executed) == 2
    assert executed[0][1]["motion_id"] != executed[1][1]["motion_id"]
    assert all(call[1]["confirm"] is True for call in executed)
    assert all("실제 정리 완료는 확인되지 않았습니다" in message for message in messages)


def test_tidy_home_skill_requires_a_target_for_room_scope():
    result = TidyHomeSkill().execute({"scope": "room"})

    assert result["success"] is False
    assert result["failure_reason"] == "target_location_required"
