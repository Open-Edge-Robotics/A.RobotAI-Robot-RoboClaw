"""ROBOT_LIMITS.json 기반 하드 제한 검증 유틸리티.

프롬프트 주입만으로는 LLM이 한계를 지킨다는 보장이 없으므로,
실제 스킬 실행 직전에 코드 수준에서 거리/각도/조인트 범위/금지 구역을 강제한다.
"""

import json
import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

# 기본 상한값: ROBOT_LIMITS.json에 명시되지 않았을 때 사용하는 보수적 기본값
_DEFAULT_MAX_RELATIVE_DISTANCE_M = 5.0
_DEFAULT_MAX_ROTATION_DEG = 180.0


def load_limits_dict(limits_file: str | None) -> dict[str, Any] | None:
    """ROBOT_LIMITS.json 파일을 파싱된 dict로 로드한다.

    기존 load_robot_limits()는 프롬프트 주입용 문자열을 반환하므로,
    하드 검증용으로는 이 함수를 사용한다.

    Returns:
        파싱된 dict. 파일이 없거나 깨진 경우 None.
    """
    if not limits_file:
        return None
    try:
        with open(limits_file, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning("ROBOT_LIMITS is not a JSON object: %r", type(data))
            return None
        logger.info("Loaded ROBOT_LIMITS for hard validation: %s", limits_file)
        return data
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Failed to read/parse ROBOT_LIMITS file (%s): %s", limits_file, e)
        return None


# ── Navigation 검증 ──


def get_max_relative_distance(limits: dict[str, Any] | None) -> float:
    """상대 이동 최대 거리(m)를 조회한다. 명시 없으면 기본값."""
    if not limits:
        return _DEFAULT_MAX_RELATIVE_DISTANCE_M
    nav = limits.get("navigation", {})
    if not isinstance(nav, dict):
        return _DEFAULT_MAX_RELATIVE_DISTANCE_M
    val = nav.get("max_relative_distance_m")
    if val is None:
        return _DEFAULT_MAX_RELATIVE_DISTANCE_M
    try:
        return float(val)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RELATIVE_DISTANCE_M


def get_max_rotation_deg(limits: dict[str, Any] | None) -> float:
    """최대 회전 각도(deg)를 조회한다. 명시 없으면 기본값."""
    if not limits:
        return _DEFAULT_MAX_ROTATION_DEG
    nav = limits.get("navigation", {})
    if not isinstance(nav, dict):
        return _DEFAULT_MAX_ROTATION_DEG
    val = nav.get("max_rotation_deg")
    if val is None:
        return _DEFAULT_MAX_ROTATION_DEG
    try:
        return float(val)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_ROTATION_DEG


def get_forbidden_zones(
    limits: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """금지 구역 목록을 반환한다."""
    if not limits:
        return []
    nav = limits.get("navigation", {})
    if not isinstance(nav, dict):
        return []
    zones = nav.get("forbidden_zones", [])
    if not isinstance(zones, list):
        return []
    return [z for z in zones if isinstance(z, dict)]


def check_relative_move(
    forward: float,
    lateral: float,
    limits: dict[str, Any] | None = None,
) -> str | None:
    """상대 이동 거리가 한계 내인지 검증한다.

    Returns:
        None if OK, 오류 메시지 문자열 if 위반.
    """
    distance = math.hypot(float(forward), float(lateral))
    max_dist = get_max_relative_distance(limits)
    if distance > max_dist:
        return (
            f"상대 이동 거리({distance:.2f}m)가 최대 한계({max_dist:.1f}m)를 초과했습니다."
        )
    return None


def check_rotation(
    angle_deg: float,
    limits: dict[str, Any] | None = None,
) -> str | None:
    """회전 각도가 한계 내인지 검증한다.

    Returns:
        None if OK, 오류 메시지 문자열 if 위반.
    """
    abs_angle = abs(float(angle_deg))
    max_rot = get_max_rotation_deg(limits)
    if abs_angle > max_rot:
        return (
            f"회전 각도({abs_angle:.1f}°)가 최대 한계({max_rot:.1f}°)를 초과했습니다."
        )
    return None


def check_target_in_forbidden_zone(
    x: float,
    y: float,
    limits: dict[str, Any] | None = None,
) -> str | None:
    """목표 좌표가 금지 구역 내인지 검증한다.

    Returns:
        None if OK, 오류 메시지(구역명+사유) if 위반.
    """
    zones = get_forbidden_zones(limits)
    for zone in zones:
        try:
            x_min = float(zone.get("x_min", -math.inf))
            x_max = float(zone.get("x_max", math.inf))
            y_min = float(zone.get("y_min", -math.inf))
            y_max = float(zone.get("y_max", math.inf))
        except (TypeError, ValueError):
            continue
        if x_min <= x <= x_max and y_min <= y <= y_max:
            name = zone.get("name", "알 수 없는 구역")
            reason = zone.get("reason", "")
            return f"금지 구역 '{name}' 내로 이동할 수 없습니다. {reason}"
    return None


# ── Manipulation 검증 ──


def get_joint_limits_deg(
    limits: dict[str, Any] | None
) -> dict[str, tuple[float, float]]:
    """조인트별 허용 각도 범위(deg)를 반환한다.

    Returns:
        {joint_name: (min_deg, max_deg)}
    """
    if not limits:
        return {}
    manip = limits.get("manipulation", {})
    if not isinstance(manip, dict):
        return {}
    raw = manip.get("joint_limits_deg", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, tuple[float, float]] = {}
    for name, rng in raw.items():
        if (
            isinstance(rng, (list, tuple))
            and len(rng) == 2
            and all(isinstance(v, (int, float)) for v in rng)
        ):
            result[name] = (float(rng[0]), float(rng[1]))
    return result


def get_joint_limits_raw(
    limits: dict[str, Any] | None
) -> dict[str, tuple[float, float]]:
    """조인트별 허용 범위(raw, m/rad 등 deg 아닌 단위)를 반환한다.

    Stretch처럼 deg가 아닌 m/rad 단위를 쓰는 로봇은 ``joint_limits`` 키로
    raw 한계를 명시한다. 단위 변환 없이 값 범위 비교만 수행한다.

    Returns:
        {joint_name: (min, max)} (단위는 호출자가 알아서 해석)
    """
    if not limits:
        return {}
    manip = limits.get("manipulation", {})
    if not isinstance(manip, dict):
        return {}
    raw = manip.get("joint_limits", {})
    if not isinstance(raw, dict):
        return {}
    result: dict[str, tuple[float, float]] = {}
    for name, rng in raw.items():
        if (
            isinstance(rng, (list, tuple))
            and len(rng) == 2
            and all(isinstance(v, (int, float)) for v in rng)
        ):
            result[name] = (float(rng[0]), float(rng[1]))
    return result


def check_joint_targets(
    joints: dict[str, Any],
    limits: dict[str, Any] | None = None,
) -> str | None:
    """조인트 목표 값이 허용 범위 내인지 검증한다.

    ``joint_limits_deg``(deg)와 ``joint_limits``(raw, m/rad) 두 키를 모두
    인식한다. 같은 joint가 두 키에 모두 있으면 각각 독립 검증한다.

    Args:
        joints: {joint_name: target_value}
        limits: ROBOT_LIMITS dict

    Returns:
        None if OK, 오류 메시지 if 위반.
    """
    joint_limits_deg = get_joint_limits_deg(limits)
    joint_limits_raw = get_joint_limits_raw(limits)
    if not joint_limits_deg and not joint_limits_raw:
        return None
    for name, value in joints.items():
        try:
            val = float(value)
        except (TypeError, ValueError):
            return f"조인트 '{name}' 값이 숫자가 아닙니다: {value!r}"
        if not math.isfinite(val):
            return f"조인트 '{name}' 값이 유한하지 않습니다: {value!r}"

        if name in joint_limits_deg:
            low, high = joint_limits_deg[name]
            if not (low <= val <= high):
                return (
                    f"조인트 '{name}' 목표값({val:.1f}°)이 "
                    f"허용 범위({low:.1f}°~{high:.1f}°)를 벗어났습니다."
                )
        if name in joint_limits_raw:
            low, high = joint_limits_raw[name]
            if not (low <= val <= high):
                return (
                    f"조인트 '{name}' 목표값({val:.4f})이 "
                    f"허용 범위({low:.4f}~{high:.4f})를 벗어났습니다."
                )
    return None
