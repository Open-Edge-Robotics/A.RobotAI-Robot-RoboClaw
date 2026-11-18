"""시연 전 자세 정렬 — 신발/신발장 시연은 모션 전에 지정한 절대 방위로 회전한다.

시연 모션은 로봇의 현재 방향에서 시작하므로, 진열대를 향하도록 몸을 맞춰야 하는
요청(신발·신발장)은 회전을 먼저 수행한다. 목표는 **map 프레임의 절대 방위**(예:
64.2°)이며, 현재 yaw 와의 최단 차이를 계산해 Nav2 `/spin` 으로 회전한다(CLOiD 는
직접 `/cmd_vel` Twist 제어가 적용되지 않는다). 안전 게이트를 통과해야 하고, 회전이
실패하면 모션을 실행하지 않는다(fail closed).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .observation import safe_observe
from .policy import is_shoe_related

logger = logging.getLogger(__name__)

#: 신발/신발장 시연 전 목표 절대 방위(도). 빈 값이면 회전하지 않는다.
PARAM_HEADING_DEG = "cloid_demo_shoe_heading_deg"
DEFAULT_HEADING_DEG = 64.2
MIN_HEADING_DEG = -180.0
MAX_HEADING_DEG = 180.0
#: 시연 전 회전 결과 대기 상한(초).
TURN_TIMEOUT_SEC = 60.0
TURN_LABEL = "시연 전 자세 회전"

#: (node, delta_rad) -> (ok, message, goal_handle)
SpinFn = Callable[[Any, float], "tuple[bool, str, Any]"]
#: (goal_handle, timeout_sec, label) -> (ok, message)
WaitFn = Callable[[Any, float, str], "tuple[bool, str]"]


@dataclass(frozen=True)
class DemoTurn:
    """시연 전 회전 계획 — 목표 절대 방위와 현재 자세 기준 회전량."""

    heading_deg: float
    delta_deg: float


def _param(node: Any, name: str, default: Any) -> Any:
    try:
        value = getattr(node.get_parameter(name), "value", default)
    except Exception:  # noqa: BLE001 - 미선언 배포는 기본값
        return default
    return default if value is None else value


def heading_deg_for(node: Any, *texts: Any) -> float | None:
    """신발·신발장 관련 요청이면 목표 절대 방위(도), 아니면 ``None``.

    파라미터가 빈 문자열/``None`` 이면 회전하지 않는다(``None``).
    """
    if not is_shoe_related(*texts):
        return None
    raw = _param(node, PARAM_HEADING_DEG, DEFAULT_HEADING_DEG)
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if isinstance(raw, bool):
        return None
    try:
        heading = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HEADING_DEG
    if not math.isfinite(heading) or heading < MIN_HEADING_DEG or heading > MAX_HEADING_DEG:
        return DEFAULT_HEADING_DEG
    return heading


def pose_yaw_rad(pose: Any) -> float | None:
    """pose dict 에서 yaw(rad)를 읽는다(없거나 map 프레임이 아니면 ``None``)."""
    if not isinstance(pose, dict):
        return None
    frame = str(pose.get("frame") or "")
    if frame and frame != "map":
        return None
    try:
        yaw = float(pose["yaw"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(yaw):
        return None
    return yaw


def heading_delta_deg(current_yaw_rad: float, target_heading_deg: float) -> float:
    """현재 yaw 에서 목표 절대 방위로 가는 최단 회전량(도, -180~180)."""
    target = math.radians(float(target_heading_deg))
    delta = math.atan2(math.sin(target - current_yaw_rad), math.cos(target - current_yaw_rad))
    return math.degrees(delta)


def plan_demo_turn(node: Any, *texts: Any, pose: Any = None) -> DemoTurn | None:
    """시연 전 회전 계획. 신발 관련이 아니거나 현재 자세를 읽을 수 없으면 ``None``."""
    heading = heading_deg_for(node, *texts)
    if heading is None:
        return None
    yaw = pose_yaw_rad(pose)
    if yaw is None:
        logger.warning("[DemoPose] 현재 자세를 확인할 수 없어 시연 전 회전을 생략합니다")
        return None
    return DemoTurn(heading_deg=heading, delta_deg=heading_delta_deg(yaw, heading))


def turn_before_demo(
    node: Any,
    turn: DemoTurn,
    *,
    spin: SpinFn | None = None,
    wait: WaitFn | None = None,
    timeout_sec: float = TURN_TIMEOUT_SEC,
    logger_obj: Any = None,
) -> tuple[bool, str]:
    """계획한 만큼 제자리 회전한다. 성공하면 ``(True, "")``, 실패는 ``(False, 사유)``."""
    log = logger_obj if logger_obj is not None else logger
    if abs(turn.delta_deg) < 0.5:
        log.info("[DemoPose] 이미 목표 방위(%.1f도)에 있어 회전을 생략합니다", turn.heading_deg)
        return True, ""

    from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

    gate_ok, gate_error = check_navigation_safety_gate(node, rotation_deg=turn.delta_deg)
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
        started, message, handle = spin(node, math.radians(turn.delta_deg))
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
    log.info(
        "[DemoPose] 시연 전 %.1f도 방향 정렬 완료(회전 %.1f도)",
        turn.heading_deg,
        turn.delta_deg,
    )
    return True, ""


def apply_before_demo(
    node: Any,
    observer: Any,
    *,
    place: str,
    target: str = "",
    pose: Any = None,
    spin: SpinFn | None = None,
    wait: WaitFn | None = None,
    logger_obj: Any = None,
) -> tuple[bool, str, DemoTurn | None]:
    """시연 전 자세 정렬(관측 + 회전)을 한 곳에서 수행한다.

    세 시연 경로(`cleanup_demo`, `tidy_home` 로컬 시연 주경로·LLM 결정 분기)가 같은
    규칙·같은 관측 문구를 쓰도록 묶은 진입점이다. 반환값은
    ``(성공 여부, 실패 사유, 회전 계획)`` 이며, 회전 대상이 아니면
    ``(True, "", None)`` 이다. 실패 시 관측에 ``demo_pose`` 실패를 남긴다.
    """
    turn = plan_demo_turn(node, place, target, pose=pose)
    if turn is None:
        return True, "", None

    safe_observe(
        observer,
        "phase",
        phase="demo_pose",
        content=(
            f"'{place}' 시연 전에 {turn.heading_deg:.1f}° 방향으로 회전합니다"
            f"(현재 기준 {turn.delta_deg:+.1f}°)."
        ),
        place=place,
        target=target,
    )
    ok, error = turn_before_demo(node, turn, spin=spin, wait=wait, logger_obj=logger_obj)
    if not ok:
        safe_observe(
            observer,
            "phase",
            phase="demo_pose",
            content=f"시연 전 자세 회전에 실패해 모션을 실행하지 않았습니다: {error}",
            place=place,
            target=target,
            observed="failed",
            failure_reason="demo_turn_failed",
        )
    return ok, error, turn


__all__ = [
    "DEFAULT_HEADING_DEG",
    "DemoTurn",
    "PARAM_HEADING_DEG",
    "TURN_LABEL",
    "TURN_TIMEOUT_SEC",
    "apply_before_demo",
    "heading_deg_for",
    "heading_delta_deg",
    "plan_demo_turn",
    "pose_yaw_rad",
    "turn_before_demo",
]
