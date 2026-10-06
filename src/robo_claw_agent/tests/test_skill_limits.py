"""ROBOT_LIMITS 하드 검증 로직 단위 테스트"""

import json
import math

from robo_claw_agent.skills.limits import (
    check_joint_targets,
    check_relative_move,
    check_rotation,
    check_target_in_forbidden_zone,
    get_forbidden_zones,
    get_joint_limits_deg,
    get_max_relative_distance,
    get_max_rotation_deg,
    load_limits_dict,
)

SAMPLE_LIMITS = {
    "navigation": {
        "max_relative_distance_m": 3.0,
        "max_rotation_deg": 90.0,
        "forbidden_zones": [
            {
                "name": "stairs_zone",
                "x_min": 1.5,
                "x_max": 2.5,
                "y_min": -1.0,
                "y_max": 1.0,
                "reason": "낙하 위험 구역",
            }
        ],
    },
    "manipulation": {
        "joint_limits_deg": {
            "joint_1_base": [-150.0, 150.0],
            "joint_2_shoulder": [-30.0, 90.0],
        },
    },
}


# ── load_limits_dict ──


def test_load_limits_dict_valid(tmp_path):
    f = tmp_path / "ROBOT_LIMITS.json"
    f.write_text(json.dumps(SAMPLE_LIMITS), encoding="utf-8")
    result = load_limits_dict(str(f))
    assert result is not None
    assert "navigation" in result


def test_load_limits_dict_missing_file():
    assert load_limits_dict(None) is None
    assert load_limits_dict("") is None
    assert load_limits_dict("/nonexistent/path.json") is None


def test_load_limits_dict_invalid_json(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("{invalid json", encoding="utf-8")
    assert load_limits_dict(str(f)) is None


def test_load_limits_dict_non_object(tmp_path):
    f = tmp_path / "array.json"
    f.write_text("[1, 2, 3]", encoding="utf-8")
    assert load_limits_dict(str(f)) is None


# ── check_relative_move ──


def test_check_relative_move_within_limit():
    assert check_relative_move(1.0, 0.5, SAMPLE_LIMITS) is None


def test_check_relative_move_exceeds_limit():
    err = check_relative_move(5.0, 0.0, SAMPLE_LIMITS)
    assert err is not None
    assert "최대 한계" in err


def test_check_relative_move_diagonal_distance():
    # sqrt(2^2 + 2^2) = 2.83 < 3.0 -> OK
    assert check_relative_move(2.0, 2.0, SAMPLE_LIMITS) is None
    # sqrt(2.5^2 + 2.5^2) = 3.54 > 3.0 -> 위반
    err = check_relative_move(2.5, 2.5, SAMPLE_LIMITS)
    assert err is not None


def test_check_relative_move_default_when_no_limits():
    # limits=None이면 기본값 5.0m 사용
    assert check_relative_move(4.0, 0.0, None) is None
    err = check_relative_move(6.0, 0.0, None)
    assert err is not None


# ── check_rotation ──


def test_check_rotation_within_limit():
    assert check_rotation(45.0, SAMPLE_LIMITS) is None
    assert check_rotation(-90.0, SAMPLE_LIMITS) is None


def test_check_rotation_exceeds_limit():
    err = check_rotation(120.0, SAMPLE_LIMITS)
    assert err is not None
    assert "최대 한계" in err


def test_check_rotation_default_when_no_limits():
    assert check_rotation(180.0, None) is None
    err = check_rotation(200.0, None)
    assert err is not None


# ── check_target_in_forbidden_zone ──


def test_check_forbidden_zone_outside():
    assert check_target_in_forbidden_zone(0.0, 0.0, SAMPLE_LIMITS) is None


def test_check_forbidden_zone_inside():
    err = check_target_in_forbidden_zone(2.0, 0.0, SAMPLE_LIMITS)
    assert err is not None
    assert "stairs_zone" in err


def test_check_forbidden_zone_boundary():
    # 경계선(x=1.5)은 포함
    err = check_target_in_forbidden_zone(1.5, 0.0, SAMPLE_LIMITS)
    assert err is not None


def test_check_forbidden_zone_no_limits():
    assert check_target_in_forbidden_zone(2.0, 0.0, None) is None


# ── check_joint_targets ──


def test_check_joints_within_range():
    joints = {"joint_1_base": 0.0, "joint_2_shoulder": 45.0}
    assert check_joint_targets(joints, SAMPLE_LIMITS) is None


def test_check_joints_exceeds_range():
    joints = {"joint_1_base": 200.0}
    err = check_joint_targets(joints, SAMPLE_LIMITS)
    assert err is not None
    assert "joint_1_base" in err


def test_check_joints_below_range():
    joints = {"joint_2_shoulder": -50.0}
    err = check_joint_targets(joints, SAMPLE_LIMITS)
    assert err is not None
    assert "joint_2_shoulder" in err


def test_check_joints_unknown_joint_ignored():
    joints = {"unknown_joint": 999.0}
    assert check_joint_targets(joints, SAMPLE_LIMITS) is None


def test_check_joints_non_numeric_value():
    joints = {"joint_1_base": "not_a_number"}
    err = check_joint_targets(joints, SAMPLE_LIMITS)
    assert err is not None
    assert "숫자가 아닙니다" in err


def test_check_joints_non_finite_value():
    assert check_joint_targets({"joint_1_base": math.nan}, SAMPLE_LIMITS) is not None
    assert check_joint_targets({"joint_1_base": math.inf}, SAMPLE_LIMITS) is not None


def test_check_joints_no_limits():
    joints = {"joint_1_base": 999.0}
    assert check_joint_targets(joints, None) is None


# ── getter 함수 ──


def test_get_max_relative_distance():
    assert get_max_relative_distance(SAMPLE_LIMITS) == 3.0
    assert get_max_relative_distance(None) == 5.0


def test_get_max_rotation_deg():
    assert get_max_rotation_deg(SAMPLE_LIMITS) == 90.0
    assert get_max_rotation_deg(None) == 180.0


def test_get_forbidden_zones():
    zones = get_forbidden_zones(SAMPLE_LIMITS)
    assert len(zones) == 1
    assert zones[0]["name"] == "stairs_zone"
    assert get_forbidden_zones(None) == []


def test_get_joint_limits_deg():
    limits = get_joint_limits_deg(SAMPLE_LIMITS)
    assert "joint_1_base" in limits
    assert limits["joint_1_base"] == (-150.0, 150.0)
    assert get_joint_limits_deg(None) == {}
