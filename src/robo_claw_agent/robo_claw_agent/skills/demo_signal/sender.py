"""데모 cue 전송 어댑터 — 설정된 단일 엔드포인트로 1회 전송한다(재시도 없음).

모션 명령은 이미 발행된 뒤이므로 여기서 실패해도 모션/시연 결과를 바꾸지 않는다.
소켓 예외는 결과 dict 로 정규화하고, 서버 응답 본문은 읽지 않는다.
"""

from __future__ import annotations

import logging
import socket
from typing import Any

from .protocol import (
    DEFAULT_PAYLOAD,
    DEFAULT_TIMEOUT_SEC,
    PROTOCOL_TCP,
    REASON_SEND_FAILED,
    STATUS_FAILED,
    STATUS_SENT,
    STATUS_SKIPPED,
    CueConfig,
    build_config,
    result,
)

logger = logging.getLogger(__name__)

PARAM_ENABLED = "cloid_demo_signal_enabled"
PARAM_PROTOCOL = "cloid_demo_signal_protocol"
PARAM_HOST = "cloid_demo_signal_host"
PARAM_PORT = "cloid_demo_signal_port"
PARAM_PAYLOAD = "cloid_demo_signal_payload"
PARAM_TERMINATOR = "cloid_demo_signal_terminator"
PARAM_TIMEOUT_SEC = "cloid_demo_signal_timeout_sec"


def _param(node: Any, name: str, default: Any = None) -> Any:
    """노드 파라미터 값을 읽는다(미선언 배포/테스트 더블은 기본값)."""
    try:
        value = getattr(node.get_parameter(name), "value", default)
    except Exception:  # noqa: BLE001 - 미선언 파라미터는 기본값으로 처리
        return default
    return default if value is None and default is not None else value


def config_from_node(node: Any) -> tuple[CueConfig | None, str]:
    """노드 파라미터에서 cue 설정을 읽어 검증한다."""
    return build_config(
        enabled=_param(node, PARAM_ENABLED, False),
        protocol=_param(node, PARAM_PROTOCOL, PROTOCOL_TCP),
        host=_param(node, PARAM_HOST, ""),
        port=_param(node, PARAM_PORT, 0),
        payload=_param(node, PARAM_PAYLOAD, DEFAULT_PAYLOAD),
        terminator=_param(node, PARAM_TERMINATOR, "none"),
        timeout_sec=_param(node, PARAM_TIMEOUT_SEC, DEFAULT_TIMEOUT_SEC),
    )


def send_cue(config: CueConfig, *, socket_factory: Any = socket.socket) -> dict[str, Any]:
    """cue 를 1회 전송한다. 실패는 예외 대신 결과로 정규화한다."""
    sock = None
    try:
        if config.protocol == PROTOCOL_TCP:
            sock = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(config.timeout_sec)
            sock.connect((config.host, config.port))
            sock.sendall(config.payload)
        else:
            sock = socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(config.timeout_sec)
            sock.sendto(config.payload, (config.host, config.port))
    except Exception as exc:  # noqa: BLE001 - cue 실패가 시연 결과를 바꾸지 않는다
        reason = type(exc).__name__
        logger.warning("데모 cue 전송 실패 (%s): %s", config.endpoint(), reason)
        return result(STATUS_FAILED, config, reason=REASON_SEND_FAILED, error=reason)
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001 - close 실패는 무시
                pass
    logger.info("데모 cue 전송 완료 (%s, %d bytes)", config.endpoint(), len(config.payload))
    return result(STATUS_SENT, config)


def send_demo_cue(node: Any, *, socket_factory: Any = socket.socket) -> dict[str, Any]:
    """노드 설정으로 cue 를 전송한다. 비활성/설정 오류는 ``skipped`` 로 보고한다."""
    config, reason = config_from_node(node)
    if config is None:
        return result(STATUS_SKIPPED, reason=reason)
    return send_cue(config, socket_factory=socket_factory)


__all__ = [
    "config_from_node",
    "send_cue",
    "send_demo_cue",
]
