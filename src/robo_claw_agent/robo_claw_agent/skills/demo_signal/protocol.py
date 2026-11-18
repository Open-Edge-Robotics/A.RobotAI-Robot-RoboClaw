"""데모 cue 전송 계약 — 설정 검증, payload 인코딩, 결과 정규화(순수 로직).

ROS/소켓 의존이 없다. 실제 전송은 ``sender`` 모듈이 담당하고, 이 모듈은 "어디로
무엇을 보낼지"만 결정한다. 시연 보조 신호이므로 실패해도 모션/시연 결과를 바꾸지
않는다(best-effort).

payload 는 설정값만 사용한다. LLM/사용자 메시지로 목적지나 바이트를 바꿀 수 없고,
개행은 종단 설정(``none``/``lf``/``crlf``)으로만 붙인다(기본 없음).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from .._param_utils import bool_param

PROTOCOL_TCP = "tcp"
PROTOCOL_UDP = "udp"
PROTOCOLS = (PROTOCOL_TCP, PROTOCOL_UDP)

TERMINATOR_NONE = "none"
#: 종단 설정 → 실제 바이트. 기본은 개행을 붙이지 않는다.
TERMINATORS: dict[str, bytes] = {
    TERMINATOR_NONE: b"",
    "lf": b"\n",
    "crlf": b"\r\n",
}

DEFAULT_PAYLOAD = "shoe pick"
DEFAULT_TIMEOUT_SEC = 1.0
MIN_TIMEOUT_SEC = 0.1
MAX_TIMEOUT_SEC = 3.0
MIN_PORT = 1
MAX_PORT = 65535
MAX_PAYLOAD_BYTES = 128
MAX_HOST_CHARS = 253

STATUS_SENT = "sent"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

#: 설정/전송 결과 사유 코드(결과·관측·로그에 그대로 쓴다).
REASON_DISABLED = "disabled"
REASON_INVALID_PROTOCOL = "invalid_protocol"
REASON_INVALID_HOST = "invalid_host"
REASON_INVALID_PORT = "invalid_port"
REASON_INVALID_PAYLOAD = "invalid_payload"
REASON_INVALID_TERMINATOR = "invalid_terminator"
REASON_INVALID_TIMEOUT = "invalid_timeout"
REASON_SEND_FAILED = "send_failed"

#: 스킴/경로/공백이 섞인 값을 걸러내는 호스트 형식(리터럴 IPv4 포함).
_HOST_RE = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?")
_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


class CueConfigError(ValueError):
    """데모 cue 설정이 계약을 어겼을 때 발생한다."""


@dataclass(frozen=True)
class CueConfig:
    """검증된 cue 전송 설정(바이트는 미리 인코딩해 둔다)."""

    protocol: str
    host: str
    port: int
    payload: bytes
    timeout_sec: float

    def endpoint(self) -> str:
        """로그/관측에 남길 목적지 표기."""
        return f"{self.protocol}://{self.host}:{self.port}"


def normalize_host(value: Any) -> str | None:
    """호스트를 소문자 도메인/리터럴 IPv4로 검증한다(스킴·경로·공백 불가)."""
    if not isinstance(value, str):
        return None
    host = value.lower()
    if not host or len(host) > MAX_HOST_CHARS:
        return None
    if not _HOST_RE.fullmatch(host):
        return None
    if any(not _LABEL_RE.fullmatch(label) for label in host.split(".")):
        return None
    return host


def parse_port(value: Any) -> int | None:
    """포트를 1~65535 정수로 검증한다(bool/소수/범위 밖은 거부)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        value = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            return None
        value = int(text)
    elif not isinstance(value, int):
        return None
    return value if MIN_PORT <= value <= MAX_PORT else None


def normalize_terminator(value: Any) -> bytes | None:
    """종단 설정을 바이트로 바꾼다(모르는 값은 None)."""
    key = TERMINATOR_NONE if value is None or value == "" else str(value).strip().lower()
    return TERMINATORS.get(key)


def build_payload(text: Any, suffix: bytes) -> bytes | None:
    """payload 를 UTF-8 바이트로 인코딩한다(제어문자/과길이 거부)."""
    if not isinstance(text, str):
        return None
    try:
        body = text.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if not body or any(byte < 0x20 or byte == 0x7F for byte in body):
        return None
    data = body + suffix
    if len(data) > MAX_PAYLOAD_BYTES:
        return None
    return data


def _timeout(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        timeout = float(DEFAULT_TIMEOUT_SEC if value is None else value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(timeout) or not MIN_TIMEOUT_SEC <= timeout <= MAX_TIMEOUT_SEC:
        return None
    return timeout


def build_config(
    *,
    enabled: Any,
    protocol: Any,
    host: Any,
    port: Any,
    payload: Any,
    terminator: Any,
    timeout_sec: Any,
) -> tuple[CueConfig | None, str]:
    """설정을 검증한다. 사용하지 않거나 잘못된 설정은 ``(None, 사유)``."""
    if not bool_param(enabled, False):
        return None, REASON_DISABLED
    name = str(protocol or PROTOCOL_TCP).strip().lower()
    if name not in PROTOCOLS:
        return None, REASON_INVALID_PROTOCOL
    normalized_host = normalize_host(host)
    if normalized_host is None:
        return None, REASON_INVALID_HOST
    normalized_port = parse_port(port)
    if normalized_port is None:
        return None, REASON_INVALID_PORT
    suffix = normalize_terminator(terminator)
    if suffix is None:
        return None, REASON_INVALID_TERMINATOR
    data = build_payload(DEFAULT_PAYLOAD if payload is None else payload, suffix)
    if data is None:
        return None, REASON_INVALID_PAYLOAD
    timeout = _timeout(timeout_sec)
    if timeout is None:
        return None, REASON_INVALID_TIMEOUT
    return (
        CueConfig(
            protocol=name,
            host=normalized_host,
            port=normalized_port,
            payload=data,
            timeout_sec=timeout,
        ),
        "",
    )


def result(
    status: str,
    config: CueConfig | None = None,
    *,
    reason: str = "",
    error: str = "",
) -> dict[str, Any]:
    """스킬 결과/관측에 싣는 cue 결과(주소·바이트 수만, payload 원문은 담지 않는다)."""
    payload: dict[str, Any] = {"status": status, "reason": reason, "bytes": 0}
    if config is not None:
        payload.update(
            {
                "protocol": config.protocol,
                "host": config.host,
                "port": config.port,
                "bytes": len(config.payload),
            }
        )
    if error:
        payload["error"] = str(error)[:120]
    return payload


__all__ = [
    "CueConfig",
    "CueConfigError",
    "DEFAULT_PAYLOAD",
    "DEFAULT_TIMEOUT_SEC",
    "MAX_PAYLOAD_BYTES",
    "PROTOCOLS",
    "PROTOCOL_TCP",
    "PROTOCOL_UDP",
    "REASON_DISABLED",
    "REASON_INVALID_HOST",
    "REASON_INVALID_PAYLOAD",
    "REASON_INVALID_PORT",
    "REASON_INVALID_PROTOCOL",
    "REASON_INVALID_TERMINATOR",
    "REASON_INVALID_TIMEOUT",
    "REASON_SEND_FAILED",
    "STATUS_FAILED",
    "STATUS_SENT",
    "STATUS_SKIPPED",
    "TERMINATORS",
    "build_config",
    "build_payload",
    "normalize_host",
    "normalize_terminator",
    "parse_port",
    "result",
]
