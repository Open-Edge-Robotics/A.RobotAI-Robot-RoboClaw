"""시연 모션 종료 후 Stop — vendor 모션 세션을 닫아 joint 제어를 돌려준다.

시연 모션(`MotionCmd START`)만 보내면 tofu 의 `joint_motion_controller` 가 active 로
남아 다른 프로그램이 joint 를 제어하지 못한다. 모션 길이만큼 기다린 뒤
`MotionCmd STOP` 을 1회 발행해 vendor 가 hold 컨트롤러로 복귀하도록 한다.

STOP 발행 실패는 시연 결과를 바꾸지 않는다(best-effort). 호출부는 결과를 관측/결과에
남기고 cue 전송 같은 후속 동작을 계속한다.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

#: 시연 모션 요청 후 STOP 까지 기다리는 시간(초). 시연 모션 길이보다 길어야 한다.
PARAM_STOP_AFTER_SEC = "cloid_demo_stop_after_sec"
DEFAULT_STOP_AFTER_SEC = 8.0
MIN_STOP_AFTER_SEC = 0.0
MAX_STOP_AFTER_SEC = 60.0

STATUS_SENT = "sent"
STATUS_FAILED = "failed"

REASON_INVALID_WAIT = "invalid_stop_wait"

PublishFn = Callable[[Any], "tuple[bool, str]"]


def _param(node: Any, name: str, default: Any) -> Any:
    try:
        value = getattr(node.get_parameter(name), "value", default)
    except Exception:  # noqa: BLE001 - 미선언 배포는 기본값
        return default
    return default if value is None else value


def stop_after_sec_for(node: Any) -> float | None:
    """시연 후 STOP 대기 시간(초). 범위 밖/비수치면 ``None``(호출부가 중단)."""
    raw = _param(node, PARAM_STOP_AFTER_SEC, DEFAULT_STOP_AFTER_SEC)
    if isinstance(raw, bool):
        return None
    try:
        wait_sec = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(wait_sec) or not MIN_STOP_AFTER_SEC <= wait_sec <= MAX_STOP_AFTER_SEC:
        return None
    return wait_sec


def stop_after_demo(
    node: Any,
    *,
    wait_sec: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    publish: PublishFn | None = None,
    logger_obj: Any = None,
) -> dict[str, Any]:
    """시연 모션 종료를 기다린 뒤 STOP 을 1회 발행한다(실패해도 예외 없음).

    반환값은 결과/관측에 싣는 dict 다: ``status``(``sent``/``failed``),
    ``reason``(실패 사유), ``wait_sec``(대기한 시간).
    """
    log = logger_obj if logger_obj is not None else logger
    resolved_wait = stop_after_sec_for(node) if wait_sec is None else float(wait_sec)
    if resolved_wait is None:
        return {"status": STATUS_FAILED, "reason": REASON_INVALID_WAIT, "wait_sec": 0.0}

    if resolved_wait > 0.0:
        sleep(resolved_wait)

    try:
        if publish is None:
            from ..cloid_motion_skill import (  # noqa: PLC0415 - 순환 import 회피
                publish_motion_stop,
            )

            publish = publish_motion_stop
        sent, message = publish(node)
    except Exception as exc:  # noqa: BLE001 - STOP 실패가 시연 결과를 바꾸지 않는다
        log.warning("[DemoStop] 모션 정지 요청 실패: %s", type(exc).__name__)
        return {
            "status": STATUS_FAILED,
            "reason": f"stop_failed: {type(exc).__name__}",
            "wait_sec": resolved_wait,
        }

    if not sent:
        log.warning("[DemoStop] 모션 정지 명령을 보내지 못했습니다: %s", message)
        return {
            "status": STATUS_FAILED,
            "reason": str(message or "stop_failed")[:120],
            "wait_sec": resolved_wait,
        }
    log.info("[DemoStop] 시연 후 모션 정지 명령을 발행했습니다(대기 %.1f초)", resolved_wait)
    return {"status": STATUS_SENT, "reason": "", "wait_sec": resolved_wait}


__all__ = [
    "DEFAULT_STOP_AFTER_SEC",
    "PARAM_STOP_AFTER_SEC",
    "REASON_INVALID_WAIT",
    "STATUS_FAILED",
    "STATUS_SENT",
    "stop_after_demo",
    "stop_after_sec_for",
]
