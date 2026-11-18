"""시연 전 자세 정렬 — 신발/신발장 시연은 모션 전에 제자리 회전을 먼저 한다.

시연 모션은 로봇의 현재 방향에서 시작하므로, 진열대를 향하도록 몸을 돌려야 하는
요청(신발·신발장)은 회전을 먼저 수행한다. 회전은 Nav2 `/spin` 액션으로 하고
(CLOiD 는 직접 `/cmd_vel` Twist 제어가 적용되지 않는다), 안전 게이트를 통과해야
한다. 회전이 실패하면 모션을 실행하지 않는다(fail closed).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import Any

from .policy import is_shoe_related

logger = logging.getLogger(__name__)

#: 신발/신발장 시연 전 회전 각도(도). 음수면 반대 방향, 0 이면 회전하지 않는다.
PARAM_TURN_DEG = "cloid_demo_shoe_turn_deg"
DEFAULT_TURN_DEG = 90.0
MIN_TURN_DEG = -180.0
MAX_TURN_DEG = 180.0
#: 시연 전 회전 결과 대기 상한(초).
TURN_TIMEOUT_SEC = 60.0
TURN_LABEL = "시연 전 자세 회전"

#: (node, delta_rad) -> (ok, message, goal_handle)
SpinFn = Callable[[Any, float], "tuple[bool, str, Any]"]
#: (goal_handle, timeout_sec, label) -> (ok, message)
WaitFn = Callable[[Any, float, str], "tuple[bool, str]"]


def _param(node: Any, name: str, default: Any) -> Any:
    try:
        value = getattr(node.get_parameter(name), "value", default)
    except Exception:  # noqa: BLE001 - 미선언 배포는 기본값
        return default
    return default if value is None else value


def turn_deg_for(node: Any, *texts: Any) -> float:
    """신발·신발장 관련 요청이면 시연 전 회전 각도(도), 아니면 0.0."""
    if not is_shoe_related(*texts):
        return 0.0
    raw = _param(node, PARAM_TURN_DEG, DEFAULT_TURN_DEG)
    if isinstance(raw, bool):
        return 0.0
    try:
        angle = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TURN_DEG
    if not math.isfinite(angle) or angle < MIN_TURN_DEG or angle > MAX_TURN_DEG:
        return DEFAULT_TURN_DEG
    return angle


def turn_before_demo(
    node: Any,
    angle_deg: float,
    *,
    spin: SpinFn | None = None,
    wait: WaitFn | None = None,
    timeout_sec: float = TURN_TIMEOUT_SEC,
    logger_obj: Any = None,
) -> tuple[bool, str]:
    """시연 전 제자리 회전. 성공하면 ``(True, "")``, 실패는 ``(False, 사유)``."""
    if angle_deg == 0.0:
        return True, ""
    log = logger_obj if logger_obj is not None else logger

    from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

    gate_ok, gate_error = check_navigation_safety_gate(node, rotation_deg=angle_deg)
    if not gate_ok:
        log.warning("[DemoPose] 회전 안전 게이트 거부: %s", gate_error)
        return False, gate_error

    try:
        if spin is None or wait is None:
            from ..navigation_skill.core import (  # noqa: PLC0415 - 순환 import 회피
                _send_spin_goal,
                _wait_for_goal_result,
            )

            spin = spin or _send_spin_goal
            wait = wait or _wait_for_goal_result
        started, message, handle = spin(node, math.radians(angle_deg))
    except Exception as exc:  # noqa: BLE001 - 회전 실패가 예외로 새지 않게 한다
        log.warning("[DemoPose] 회전 요청 실패: %s", type(exc).__name__)
        return False, f"rotation_failed: {type(exc).__name__}"

    if not started or handle is None:
        log.warning("[DemoPose] 회전 목표를 보내지 못했습니다: %s", message)
        return False, str(message or "rotation_failed")

    try:
        finished = wait(handle, timeout_sec, TURN_LABEL)
    except Exception as exc:  # noqa: BLE001
        log.warning("[DemoPose] 회전 결과 확인 실패: %s", type(exc).__name__)
        return False, f"rotation_result_unconfirmed: {type(exc).__name__}"
    ok, result_message = finished[0], finished[1]
    if not ok:
        return False, str(result_message or "rotation_failed")
    log.info("[DemoPose] 시연 전 %.0f도 회전 완료", angle_deg)
    return True, ""


__all__ = [
    "DEFAULT_TURN_DEG",
    "PARAM_TURN_DEG",
    "TURN_LABEL",
    "TURN_TIMEOUT_SEC",
    "turn_before_demo",
    "turn_deg_for",
]
