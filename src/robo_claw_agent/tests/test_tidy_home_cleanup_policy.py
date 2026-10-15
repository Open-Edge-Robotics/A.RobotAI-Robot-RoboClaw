"""tidy home cleanup policy"""

import pytest

pytestmark = pytest.mark.unit

from _tidy_home_helpers import (  # noqa: F401
    _cleanup_plan_has_complete_placement,
    _cleanup_target_category,
    _has_ambiguous_cleanup_indication,
    _has_cleanup_indication,
    _motion,
    _place,
    _resolve_cleanup_places,
    _resolve_cleanup_plan_destinations,
    _safe_cleanup_disposal_targets,
    _safe_cleanup_storage_targets,
    _select_cleanup_demo_motion,
    _select_cloid_indicator_motion,
    random,
)


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
