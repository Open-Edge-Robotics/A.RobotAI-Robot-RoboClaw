from __future__ import annotations

import random

import pytest
from robo_claw_agent.skills.cleanup_policy import (
    _cleanup_target_category,
    _safe_cleanup_storage_targets,
    _select_cleanup_demo_motion,
)
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


def test_cleanup_detection_includes_household_items_without_treating_them_as_waste():
    assert _has_cleanup_indication([{"name": "wrapper"}], "테이블 아래 포장지가 있습니다.") is True
    assert _has_cleanup_indication([{"name": "towel"}], "바닥에 수건이 있습니다.") is True
    assert _has_cleanup_indication([{"name": "cushion"}], "소파 옆 쿠션이 있습니다.") is True
    assert _has_cleanup_indication([{"name": "laundry"}], "바닥에 빨래가 있습니다.") is True
    assert (
        _has_cleanup_indication([{"name": "쓰레기통"}], "주방 한쪽에 쓰레기통이 있습니다.") is False
    )
    assert _has_cleanup_indication([], "쓰레기통 옆에 포장지가 있습니다.") is True
    assert _cleanup_target_category("수건") == "towel"
    assert _cleanup_target_category("쿠션") == "cushion"
    assert _cleanup_target_category("빨랫감") == "laundry"
    assert _cleanup_target_category("wrapper") == "waste"
    assert _cleanup_target_category("옷") is None
    assert _cleanup_target_category("shirt") is None
    assert _has_cleanup_indication([], "정리해야 할 물건이 보입니다.") is False
    assert _has_cleanup_indication([], "정리할 대상이 보이지 않습니다.") is False
    assert _has_cleanup_indication([], "쓰레기는 없고 바닥에 수건이 보입니다.") is True
    assert _has_cleanup_indication([], "바닥에 수건이 보이지 않습니다.") is False
    assert _has_cleanup_indication(["person"], "사람이 서 있습니다.") is False


def test_ambiguous_or_hazardous_items_require_user_clarification():
    assert _has_ambiguous_cleanup_indication([{"name": "cup"}], "테이블 위 컵이 보입니다.") is True
    assert _has_ambiguous_cleanup_indication([{"name": "clothes"}], "바닥에 옷이 있습니다.") is True
    assert (
        _has_ambiguous_cleanup_indication([{"name": "laundry"}], "바닥에 빨래가 있습니다.") is False
    )
    assert _has_ambiguous_cleanup_indication([{"name": "shirt"}], "세탁물로 보입니다.") is True
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

    assert _safe_cleanup_disposal_targets(memory) == [
        {"name": "쓰레기통", "target_pose": pose, "categories": ["waste"]}
    ]


def test_cleanup_storage_targets_require_explicit_category_and_curated_pose():
    from types import SimpleNamespace

    pose = {
        "frame_id": "map",
        "position": {"x": 0.2, "y": 0.3, "z": 0.4},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    }
    memory = SimpleNamespace(
        get_all_objects=lambda: [
            {
                "name": "빨래 바구니",
                "metadata": {
                    "cleanup_storage_target": True,
                    "cleanup_return_categories": ["laundry"],
                    "target_pose": pose,
                },
            },
            {
                "name": "추정 바구니",
                "metadata": {
                    "cleanup_storage_target": True,
                    "cleanup_return_categories": ["laundry"],
                    "estimated": True,
                    "target_pose": pose,
                },
            },
            {
                "name": "미지정 바구니",
                "metadata": {"cleanup_storage_target": True, "target_pose": pose},
            },
        ]
    )

    assert _safe_cleanup_storage_targets(memory) == [
        {"name": "빨래 바구니", "target_pose": pose, "categories": ["laundry"]}
    ]


def test_cleanup_plan_rejects_waste_destination_for_household_item():
    pose = {
        "frame_id": "map",
        "position": {"x": 0.4, "y": -0.2, "z": 0.3},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    }
    actions = [
        {"skill": "adaptive_pick_object", "params": {"target_object": "towel"}},
        {"skill": "place", "params": {"target_object": "쓰레기통", "target_pose": {"x": 999}}},
    ]
    destinations = [{"name": "쓰레기통", "target_pose": pose, "categories": ["waste"]}]

    assert _resolve_cleanup_plan_destinations(actions, destinations) is False
    assert actions[1]["params"]["target_pose"] == {"x": 999}


def test_cleanup_plan_does_not_infer_household_category_for_ambiguous_object():
    pose = {
        "frame_id": "map",
        "position": {"x": 0.4, "y": -0.2, "z": 0.3},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    }
    actions = [
        {"skill": "adaptive_pick_object", "params": {"target_object": "cup"}},
        {"skill": "place", "params": {"target_object": "빨래 바구니"}},
    ]

    assert (
        _resolve_cleanup_plan_destinations(
            actions,
            [{"name": "빨래 바구니", "target_pose": pose, "categories": ["laundry"]}],
            detected_categories={"laundry"},
        )
        is False
    )


def test_cleanup_plan_uses_category_matched_storage_destination():
    pose = {
        "frame_id": "map",
        "position": {"x": 0.4, "y": -0.2, "z": 0.3},
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    }
    actions = [
        {"skill": "adaptive_pick_object", "params": {"target_object": "laundry"}},
        {"skill": "place", "params": {"target_object": "빨래 바구니", "target_pose": {"x": 999}}},
    ]

    assert (
        _resolve_cleanup_plan_destinations(
            actions,
            [{"name": "빨래 바구니", "target_pose": pose, "categories": ["laundry"]}],
        )
        is True
    )
    assert actions[1]["params"]["target_pose"] == pose


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


def test_cleanup_demo_uses_only_explicit_prerequisite_free_ids():
    motions = [
        _motion(77, "cleanup_demo"),
        _motion(78, "cleanup_demo_with_prerequisite", pre_id=76),
        _motion(59, "wipe_1"),
        _motion(60, "wipe_2", pre_id=59),
    ]

    selected = _select_cleanup_demo_motion(motions, [77, 78, 59, 60], rng=random.Random(1))

    assert selected["motion_id"] in {77, 59}
    assert selected["pre_id"] is None
    assert _select_cleanup_demo_motion(motions, [78, 60]) is None


def test_cleanup_demo_allows_operator_allowlisted_wipe_ids_without_prerequisite():
    motions = [_motion(59, "wipe_1"), _motion(60, "wipe_2")]

    selected_59 = _select_cleanup_demo_motion(motions, [59], rng=random.Random(1))
    selected_60 = _select_cleanup_demo_motion(motions, [60], rng=random.Random(1))

    assert selected_59 is not None and selected_59["motion_id"] == 59
    assert selected_60 is not None and selected_60["motion_id"] == 60


def test_cleanup_demo_still_rejects_motion_with_catalog_prerequisite():
    motions = [_motion(60, "wipe_2", pre_id=59)]

    assert _select_cleanup_demo_motion(motions, [60]) is None


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


def test_cleanup_demo_handoff_stages_requester_and_skips_local_pick_place_guard(monkeypatch):
    import threading
    from types import SimpleNamespace

    from robo_claw_agent.skills import tidy_home_skill as tidy_module
    from robo_claw_agent.skills.autonomous_skill import core, decision_nodes, execution_nodes

    calls = []

    class FakeSkills:
        def has_skill(self, name):
            return name in {"move_relative", "call_peer_robot"}

        def execute(self, name, params, **_kwargs):
            calls.append((name, params))
            return SimpleNamespace(success=True, message="accepted")

    node = SimpleNamespace(_memory=None, _skills=FakeSkills())
    skill = TidyHomeSkill()
    skill.node = node
    skill.send_user_message = lambda _message: None
    active = threading.Event()
    active.set()
    monkeypatch.setattr(tidy_module, "_AUTONOMOUS_ACTIVE", active)

    def prepare_handoff(handoff):
        handoff._blackboard.update(
            {
                "cleanup_handoff_prepared": True,
                "cleanup_handoff_kind": "cleanup_demo",
                "decided_skill": "call_peer_robot",
                "decided_params": {"peer_name": "cloid1", "instruction": "demo request"},
            }
        )
        return core.NodeStatus.SUCCESS

    executed_skills = []

    def execute_peer(execute):
        executed_skills.append(execute._blackboard["decided_skill"])
        execute._blackboard["last_action_success"] = True
        return core.NodeStatus.SUCCESS

    monkeypatch.setattr(execution_nodes.ResolveCleanupHandoff, "tick", prepare_handoff)
    monkeypatch.setattr(
        decision_nodes.ValidateDecidedAction, "tick", lambda _self: core.NodeStatus.SUCCESS
    )
    monkeypatch.setattr(execution_nodes.ExecuteDecidedAction, "tick", execute_peer)

    status = skill._handle_cleanup_target(
        place_name="안방",
        scene="바닥에 포장지가 있습니다.",
        objects=[{"name": "포장지"}],
        cycle=1,
    )

    assert status == "delegated_demo"
    assert [name for name, _params in calls] == ["move_relative"]
    assert executed_skills == ["call_peer_robot"]
    assert calls[0][1]["forward"] == 0.0
    assert calls[0][1]["lateral"] > 0.0


def test_cleanup_demo_handoff_is_not_sent_if_requester_cannot_stage_safely(monkeypatch):
    import threading
    from types import SimpleNamespace

    from robo_claw_agent.skills import tidy_home_skill as tidy_module
    from robo_claw_agent.skills.autonomous_skill import core, decision_nodes, execution_nodes

    calls = []
    executed = []

    class FakeSkills:
        def has_skill(self, name):
            return name in {"move_relative", "call_peer_robot"}

        def execute(self, name, params, **_kwargs):
            calls.append((name, params))
            return SimpleNamespace(success=False, message="target blocked")

    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(_memory=None, _skills=FakeSkills())
    skill.send_user_message = lambda _message: None
    active = threading.Event()
    active.set()
    monkeypatch.setattr(tidy_module, "_AUTONOMOUS_ACTIVE", active)

    def prepare_handoff(handoff):
        handoff._blackboard.update(
            {
                "cleanup_handoff_prepared": True,
                "cleanup_handoff_kind": "cleanup_demo",
                "decided_skill": "call_peer_robot",
                "decided_params": {"peer_name": "cloid1", "instruction": "demo request"},
            }
        )
        return core.NodeStatus.SUCCESS

    def execute_peer(execute):
        executed.append(execute._blackboard["decided_skill"])
        execute._blackboard["last_action_success"] = True
        return core.NodeStatus.SUCCESS

    monkeypatch.setattr(execution_nodes.ResolveCleanupHandoff, "tick", prepare_handoff)
    monkeypatch.setattr(
        decision_nodes.ValidateDecidedAction, "tick", lambda _self: core.NodeStatus.SUCCESS
    )
    monkeypatch.setattr(execution_nodes.ExecuteDecidedAction, "tick", execute_peer)

    status = skill._handle_cleanup_target(
        place_name="안방",
        scene="바닥에 포장지가 있습니다.",
        objects=[{"name": "포장지"}],
        cycle=1,
    )

    assert status == "handoff_staging_failed"
    assert [name for name, _params in calls] == ["move_relative"]
    assert executed == []


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
