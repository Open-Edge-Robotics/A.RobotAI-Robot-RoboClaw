"""지도 목표 요청의 실행 경계 표식(ROS 비의존).

``SkillManager`` 같은 저수준 실행기는 ``agent_node``(rclpy 의존)를 임포트하지
않으면서도 "지금 실행이 지도 목표 요청 안인가"를 알아야 한다. 그래서 요청 스코프는
이 모듈의 ContextVar 에 등록하고, 저수준 경계는 여기의 함수만 호출한다.

- ``current_map_goal_guard()``: 활성 지도 목표 요청(없으면 None). 실행 여부 판정은
  등록된 객체의 ``denied_reason`` 에 위임한다.
- ``current_trusted_prelude()``: 엔진이 삽입한 안전 prelude 를 실행하는 동안 그
  스킬 이름. 모델이 만든 항목에는 이 컨텍스트가 절대 열리지 않는다.
"""

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_GUARD: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "robo_claw_map_goal_guard", default=None
)
_TRUSTED_PRELUDE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "robo_claw_map_goal_trusted_prelude", default=None
)

# 베이스 주행/상체를 움직이는 스킬 이름(메타데이터가 없을 때의 호환 fallback).
BASE_MOTION_SKILLS: frozenset[str] = frozenset(
    {
        "navigate_to",
        "move_relative",
        "approach_object",
        "rotate",
        "face_direction",
        "follow_waypoints",
        "patrol",
        "explore",
        "reactive_navigate",
        "condition_reactive",
    }
)
_PHYSICAL_EFFECTS: frozenset[str] = frozenset(
    {"base_motion", "base_rotation", "arm_motion", "gripper_motion"}
)


def current_map_goal_guard() -> Any | None:
    """현재 실행이 속한 지도 목표 요청(없으면 None)."""
    return _GUARD.get()


def current_trusted_prelude() -> str | None:
    """엔진이 직접 실행 중인 안전 prelude 스킬 이름(없으면 None)."""
    return _TRUSTED_PRELUDE.get()


def set_map_goal_guard(guard: Any) -> contextvars.Token:
    return _GUARD.set(guard)


def reset_map_goal_guard(token: contextvars.Token) -> None:
    _GUARD.reset(token)


@contextmanager
def trusted_prelude(skill_name: Any) -> Iterator[None]:
    """엔진이 삽입한 안전 prelude 실행 구간을 표시한다."""
    token = _TRUSTED_PRELUDE.set(str(skill_name or ""))
    try:
        yield
    finally:
        _TRUSTED_PRELUDE.reset(token)


def is_physical_skill(
    skill_name: Any,
    *,
    side_effects: Any = (),
    exclusive_resources: Any = (),
) -> bool:
    """베이스/상체를 실제로 움직이는 스킬인지 판정한다."""
    try:
        effects = set(side_effects or ())
        resources = set(exclusive_resources or ())
    except TypeError:
        return True
    if effects & _PHYSICAL_EFFECTS:
        return True
    if "base_control" in resources:
        return True
    return str(skill_name or "") in BASE_MOTION_SKILLS


def denied_skill_reason(
    skill_name: Any,
    *,
    risk_level: Any = "action",
    side_effects: Any = (),
    exclusive_resources: Any = (),
) -> str:
    """저수준 실행 경계에서 이 스킬을 거부할 사유(허용이면 빈 문자열).

    지도 목표 요청이 아니면 항상 허용한다(일반 작업 영향 없음). 지도 목표 요청
    안에서는 등록된 스코프의 판정을 따르며, 판정 자체가 실패하면 폐쇄(fail closed)
    한다.
    """
    guard = _GUARD.get()
    if guard is None:
        return ""
    try:
        return str(
            guard.denied_reason(
                skill_name,
                risk_level=risk_level,
                side_effects=side_effects,
                exclusive_resources=exclusive_resources,
            )
            or ""
        )
    except Exception:  # noqa: BLE001
        return "지도 목표 요청의 실행 검증에 실패해 물리 동작을 실행하지 않습니다."
