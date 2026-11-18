"""데모 cue 신호 — 모션 요청 직후 설정된 TCP/UDP 목적지로 UTF-8 바이트를 1회 전송.

``protocol`` 은 순수 설정/바이트 계약이고, ``sender`` 는 소켓 전송 어댑터다.
시연 스킬은 ``send_demo_cue(node)`` 만 호출하고, 결과(``status``/``reason``/
``bytes``)를 스킬 결과에 싣는다. payload 원문은 결과·로그에 남기지 않는다.
"""

from __future__ import annotations

from .protocol import (
    DEFAULT_PAYLOAD,
    MAX_PAYLOAD_BYTES,
    REASON_SEND_FAILED,
    STATUS_FAILED,
    STATUS_SENT,
    STATUS_SKIPPED,
    CueConfig,
    build_config,
    result,
)
from .sender import config_from_node, send_cue, send_demo_cue

__all__ = [
    "CueConfig",
    "DEFAULT_PAYLOAD",
    "MAX_PAYLOAD_BYTES",
    "REASON_SEND_FAILED",
    "STATUS_FAILED",
    "STATUS_SENT",
    "STATUS_SKIPPED",
    "build_config",
    "config_from_node",
    "result",
    "send_cue",
    "send_demo_cue",
]
