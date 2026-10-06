"""파라미터 파싱 및 grasp/gripper preset 관련 소규모 유틸리티.

이 모듈의 함수들은 부작용이 없는 순수 헬퍼로, 다른 sequence 하위 모듈에서
공통으로 재사용된다.
"""

from __future__ import annotations

from typing import Any


def _float_param(params: dict[str, Any], name: str, default: float) -> float:
    try:
        return float(params.get(name, default))
    except (TypeError, ValueError):
        return default


def _node_float_param(skill: Any, params: dict[str, Any], name: str, default: float) -> float:
    """실행 params 우선, 없으면 노드 파라미터, 없으면 default.

    ``BaseSkill.get_string_param`` 의 float 판이다(문자열용만 있고 숫자용이 없어
    YAML 로 설정한 튜닝값이 스킬까지 도달하지 못하는 문제를 막는다).
    ``perception_skill.core._head_rotate_deg`` 와 같은 조회 순서를 쓴다.
    """
    if name in params:
        return _float_param(params, name, default)

    node = getattr(skill, "node", None)
    if node is None:
        return default
    try:
        if not node.has_parameter(name):
            return default
        value = node.get_parameter(name).get_parameter_value()
        if hasattr(value, "double_value"):
            return float(value.double_value)
    except Exception:  # noqa: BLE001 - 파라미터 조회 실패는 default 로 흡수
        pass
    return default


def _number_tuple_param(value: Any, default: tuple[float, ...]) -> tuple[float, ...]:
    """길이가 고정된 숫자 튜플 파라미터를 파싱한다.

    JSON 리스트/튜플 또는 콤마 구분 문자열("35,18,70")을 지원한다. 항목 개수가
    default와 다르거나 숫자 변환에 실패하면 안전하게 default를 그대로 반환한다
    (예: HSV 하한/상한, ROI 비율 범위처럼 개수가 고정된 튜닝값에 사용).
    """
    raw = value
    if isinstance(raw, str):
        raw = [item.strip() for item in raw.split(",")]
    if not isinstance(raw, (list, tuple)) or len(raw) != len(default):
        return default
    try:
        return tuple(float(v) for v in raw)
    except (TypeError, ValueError):
        return default


def _bool_param(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _is_cup_target(target_object: str) -> bool:
    return target_object.strip().lower() in {"cup", "컵", "잔", "물컵"}


def _resolve_close_preset(params: dict[str, Any], target_object: str, runtime: Any) -> str:
    explicit = str(params.get("close_preset") or "").strip()
    if explicit:
        return explicit
    if _is_cup_target(target_object) and "close_loose" in runtime.config.gripper_presets:
        return "close_loose"
    return "close"


def _ensure_gripper_open(runtime: Any, open_preset: str, gripper_group: str | None = None) -> None:
    """close 직전에 그리퍼를 최대로 연다. preset이 없으면 스킵한다."""
    if open_preset in runtime.config.gripper_presets:
        runtime.execute_gripper_preset(open_preset, group_name=gripper_group)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _angle_sequence_deg(value: Any, default: list[float]) -> list[float]:
    raw_items = value
    if raw_items in (None, ""):
        raw_items = default
    if isinstance(raw_items, str):
        raw_items = [item.strip() for item in raw_items.split(",")]
    if not isinstance(raw_items, list):
        raw_items = list(default)

    angles: list[float] = []
    for item in raw_items[:3]:
        try:
            angle = float(item)
        except (TypeError, ValueError):
            continue
        angles.append(_clamp(angle, -180.0, 180.0))
    return angles or list(default)


def _has_target_hint(params: dict[str, Any]) -> bool:
    """target_pose/x,y,z/object_name 중 회전각 계산에 쓸 목표 정보가 있는지 확인."""
    if params.get("target_pose"):
        return True
    if all(key in params for key in ("x", "y", "z")):
        return True
    return bool(str(params.get("object_name") or "").strip())


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        val = int(value)
        return max(lo, min(hi, val))
    except (TypeError, ValueError):
        return default


def _resolve_deadline(
    skill: Any, params: dict[str, Any], default_timeout_sec: float = 30.0
) -> tuple[float, float]:
    """스킬 파라미터 또는 timeout_hint로부터 (deadline, total_timeout_sec)를 반환."""
    import time

    timeout_sec = _float_param(
        params, "timeout_sec", getattr(skill, "timeout_hint_sec", default_timeout_sec)
    )
    if timeout_sec <= 0.0:
        timeout_sec = default_timeout_sec
    deadline = time.monotonic() + timeout_sec
    return deadline, timeout_sec


def _remaining_timeout(deadline: float, min_timeout: float = 0.5) -> float:
    """남은 시간을 반환하며 최소 min_timeout 초를 보장."""
    import time

    return max(min_timeout, deadline - time.monotonic())


def _is_deadline_exceeded(deadline: float) -> bool:
    """deadline 초과 여부 확인."""
    import time

    return time.monotonic() >= deadline
