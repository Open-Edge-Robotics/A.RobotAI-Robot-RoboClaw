"""시연 cue 전송 wrapper — 모션 요청/실행 직후 1회 전송하고 결과를 관측에 남긴다.

``demo_signal`` 은 설정·전송만 담당하고, 이 모듈은 시연 스킬/자율행동 경로가 같은
형식으로 결과와 관측(``phase="demo_signal"``)을 남기도록 묶는다. cue 실패는 시연
결과를 바꾸지 않는다(best-effort).
"""

from __future__ import annotations

import logging
from typing import Any

from ..demo_signal import send_demo_cue
from .observation import safe_observe

logger = logging.getLogger(__name__)


def emit_demo_signal(
    node: Any,
    observer: Any,
    *,
    place: str = "",
    target: str = "",
    logger_obj: Any = None,
) -> dict[str, Any]:
    """설정된 cue 를 1회 전송하고 결과를 관측/로그에 남긴다.

    반환값은 스킬 결과에 그대로 싣는 dict 다(``status``/``reason``/``bytes``,
    전송을 시도한 경우 ``protocol``/``host``/``port``). payload 원문은 담지 않는다.
    """
    log = logger_obj if logger_obj is not None else logger
    try:
        outcome = send_demo_cue(node)
    except Exception as exc:  # noqa: BLE001 - cue 오류가 시연을 막지 않는다
        log.warning("[DemoSignal] cue failed: %s", type(exc).__name__)
        outcome = {"status": "failed", "reason": "send_failed", "bytes": 0}

    status = str(outcome.get("status") or "")
    if status == "sent":
        content = (
            f"시연 cue {outcome.get('bytes', 0)}바이트를 "
            f"{outcome.get('protocol', '')}://{outcome.get('host', '')}:"
            f"{outcome.get('port', '')} 로 전송했습니다."
        )
    else:
        reason = str(outcome.get("reason") or "")
        content = f"시연 cue 를 전송하지 않았습니다({status or 'unknown'}/{reason})."

    safe_observe(
        observer,
        "phase",
        phase="demo_signal",
        content=content,
        place=place,
        target=target,
        observed=status,
    )

    # 비활성/미설정 배포에서 로그를 늘리지 않는다(전송을 시도한 경우만 남긴다).
    if status in ("sent", "failed"):
        log.info(
            "[DemoSignal] result: status=%s reason=%s endpoint=%s://%s:%s bytes=%s",
            status,
            outcome.get("reason", ""),
            outcome.get("protocol", ""),
            outcome.get("host", ""),
            outcome.get("port", ""),
            outcome.get("bytes", 0),
        )
    return outcome


__all__ = ["emit_demo_signal"]
