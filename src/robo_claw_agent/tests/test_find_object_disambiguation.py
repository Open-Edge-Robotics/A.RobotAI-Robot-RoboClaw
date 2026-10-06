"""FindObjectSkill의 다중 후보 disambiguation(_select_candidate) 단위 테스트."""

import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.skills.perception_skill.core import (
    _select_candidate,
    resolve_target_variants,
    target_class_variants,
    target_is_coco,
)


def test_select_candidate_default_score_picks_highest_score():
    candidates = [
        {"score": 0.6, "distance_m": 3.0},
        {"score": 0.9, "distance_m": 1.0},
        {"score": 0.7, "distance_m": 0.5},
    ]
    best = _select_candidate(candidates, selection="score")
    assert best["score"] == 0.9


def test_select_candidate_score_tie_keeps_first_match():
    first = {"score": 0.8, "distance_m": 5.0}
    second = {"score": 0.8, "distance_m": 1.0}
    best = _select_candidate([first, second], selection="score")
    assert best is first


def test_select_candidate_nearest_picks_closest_distance():
    candidates = [
        {"score": 0.9, "distance_m": 3.0},
        {"score": 0.5, "distance_m": 0.8},
        {"score": 0.6, "distance_m": 1.5},
    ]
    best = _select_candidate(candidates, selection="nearest")
    assert best["distance_m"] == 0.8


def test_select_candidate_nearest_ignores_candidates_without_distance():
    with_distance = {"score": 0.5, "distance_m": 2.0}
    without_distance = {"score": 0.99, "distance_m": None}
    best = _select_candidate([without_distance, with_distance], selection="nearest")
    assert best is with_distance


def test_select_candidate_nearest_falls_back_to_score_when_no_distance_available():
    candidates = [
        {"score": 0.4, "distance_m": None},
        {"score": 0.95, "distance_m": None},
    ]
    best = _select_candidate(candidates, selection="nearest")
    assert best["score"] == 0.95


def test_select_candidate_unknown_selection_falls_back_to_score():
    candidates = [
        {"score": 0.4, "distance_m": 0.1},
        {"score": 0.95, "distance_m": 5.0},
    ]
    best = _select_candidate(candidates, selection="unknown_strategy")
    assert best["score"] == 0.95


def test_select_candidate_single_candidate_returned_regardless_of_selection():
    only = {"score": 0.5, "distance_m": None}
    assert _select_candidate([only], selection="nearest") is only
    assert _select_candidate([only], selection="score") is only


def test_target_class_variants_normalizes_descriptive_korean_bottle():
    assert target_class_variants("작은 투명병") == ("bottle",)


def test_target_class_variants_accepts_coco_bag_classes():
    assert target_class_variants("가방") == ("backpack", "handbag", "suitcase")


def test_target_is_coco_true_for_cup():
    assert target_is_coco("컵") is True
    assert target_is_coco("빨간 물컵") is True


def test_target_is_coco_true_for_book_and_phone():
    assert target_is_coco("책") is True
    assert target_is_coco("스마트폰") is True
    assert target_class_variants("책") == ("book",)
    assert target_class_variants("핸드폰") == ("cell phone",)


def test_target_is_coco_false_for_bucket():
    """'물통'(bucket)은 COCO 80 클래스에 없다 → 오픈어휘 라우팅 신호."""
    assert target_is_coco("물통") is False
    assert target_is_coco("빨간 물통") is False
    assert resolve_target_variants("물통") == ()


def test_target_is_coco_false_for_english_unknown_word():
    assert target_is_coco("stapler") is False
    assert resolve_target_variants("stapler") == ()


def test_target_class_variants_fallback_keeps_raw_target_for_unmapped():
    assert target_class_variants("물통") == ("물통",)
    assert target_class_variants("stapler") == ("stapler",)
