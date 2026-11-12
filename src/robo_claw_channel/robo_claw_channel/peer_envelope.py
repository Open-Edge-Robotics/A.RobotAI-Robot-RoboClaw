"""협동 메시지의 상관 ID 전달 규약 (채널 노드 측).

에이전트는 협동 메시지를 JSON envelope(``_robo_claw_message_type`` 표식)로 보내고,
채널 노드는 그 안의 ``message_id``/``task_id``/``session_id``/``reply_to``/``status``
``result``/``kind`` 를 gRPC ``ChatMessage.metadata`` 에 옮겨 싣는다. 수신측 채널은
metadata 를 ``PeerMessage`` 서비스 필드로 넘겨 에이전트가 같은 ID로 상관할 수 있게
한다.

``kind`` 는 재전송 차단 범위를 정한다. ``request`` 만 task 중복 차단 키를 소유하므로
한 작업의 서로 다른 결과/회신 메시지는 통과하고, 같은 요청의 재전송은 계속 차단된다.
kind 가 없는 구버전 메시지는 기존처럼 message/task 둘 다 차단 키로 쓴다.

ROS2 의존이 없어 단위 테스트가 가능하다.
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

COOPERATION_MESSAGE_TYPE = "peer_cooperation"

#: 새 작업 위임 — 이 kind 만 task 중복 차단 키를 점유한다.
KIND_REQUEST = "request"
KIND_REPLY = "reply"
KIND_RESULT = "result"
KINDS = frozenset({KIND_REQUEST, KIND_REPLY, KIND_RESULT})

#: PeerMessage 서비스로 넘기는 상관 필드(순서 = 전달 순서).
PEER_MESSAGE_FIELDS = (
    "message_id",
    "task_id",
    "session_id",
    "reply_to",
    "status",
    "result",
    "kind",
)
#: ChatMessage.metadata 로 옮기는 필드(kind 포함).
CHAT_METADATA_FIELDS = (*PEER_MESSAGE_FIELDS, "kind")

#: 재빌드 전 생성코드로는 상관 정보를 보존할 수 없다는 명시 오류.
REBUILD_REQUIRED_ERROR = (
    "robo_claw_msgs rebuild required: PeerMessage.srv correlation fields are missing"
)
#: 서버 로그 미리보기 기본 길이.
MAX_LOG_PREVIEW_CHARS = 200

MAX_ID_CHARS = 64
MAX_STATUS_CHARS = 32
MAX_RESULT_CHARS = 1024
MAX_ENVELOPE_CHARS = 8192

_ID_PATTERN = re.compile(r"[^A-Za-z0-9._:-]+")


def _normalize_id(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _ID_PATTERN.sub("_", raw)[:MAX_ID_CHARS]


def _normalize_status(value: object) -> str:
    return str(value or "").strip().lower()[:MAX_STATUS_CHARS]


def _normalize_kind(value: object) -> str:
    raw = str(value or "").strip().lower()
    return raw if raw in KINDS else ""


@dataclass(frozen=True)
class PeerCorrelation:
    """한 협동 메시지의 상관 정보 — 전송 계층 사이에서 그대로 전달된다."""

    message_id: str = ""
    task_id: str = ""
    session_id: str = ""
    reply_to: str = ""
    status: str = ""
    result: str = ""
    kind: str = ""

    def __post_init__(self) -> None:
        # 생성 경로와 무관하게 전송 가능한 값만 남긴다(ID 문자/길이, 결과 길이).
        object.__setattr__(self, "message_id", _normalize_id(self.message_id))
        object.__setattr__(self, "task_id", _normalize_id(self.task_id))
        object.__setattr__(self, "session_id", _normalize_id(self.session_id))
        object.__setattr__(self, "reply_to", _normalize_id(self.reply_to))
        object.__setattr__(self, "status", _normalize_status(self.status))
        object.__setattr__(self, "result", str(self.result or "")[:MAX_RESULT_CHARS])
        object.__setattr__(self, "kind", _normalize_kind(self.kind))

    def as_chat_metadata(
        self, *, generate_message_id: Callable[[], str] | None = None
    ) -> dict[str, str]:
        """ChatMessage.metadata 로 실을 dict. message_id 가 없으면 생성한다."""
        metadata: dict[str, str] = {}
        message_id = self.message_id
        if not message_id and generate_message_id is not None:
            message_id = generate_message_id()
        for name in CHAT_METADATA_FIELDS:
            value = message_id if name == "message_id" else getattr(self, name)
            if value:
                metadata[name] = value
        return metadata

    def as_peer_message_fields(self) -> dict[str, str]:
        """PeerMessage 서비스 요청 필드로 실을 dict(빈 값은 넣지 않는다)."""
        return {name: getattr(self, name) for name in PEER_MESSAGE_FIELDS if getattr(self, name)}


def parse_cooperation_envelope(instruction: str) -> tuple[str, dict[str, Any]] | None:
    """envelope 문자열을 ``(message_type, payload)`` 로 해석한다.

    envelope 표식이 없거나 JSON 이 깨졌으면 None.
    """
    text = str(instruction or "").strip()
    if not text.startswith("{"):
        return None
    if len(text) > MAX_ENVELOPE_CHARS:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    message_type = str(payload.get("_robo_claw_message_type", "")).strip()
    if not message_type:
        return None
    return message_type, payload


def correlation_from_envelope(envelope: Mapping[str, Any]) -> PeerCorrelation:
    """envelope payload 에서 상관 필드를 뽑는다."""
    return PeerCorrelation(
        message_id=_normalize_id(envelope.get("message_id")),
        task_id=_normalize_id(envelope.get("task_id")),
        session_id=_normalize_id(envelope.get("session_id")),
        reply_to=_normalize_id(envelope.get("reply_to")),
        status=_normalize_status(envelope.get("status")),
        result=str(envelope.get("result") or "")[:MAX_RESULT_CHARS],
        kind=_normalize_kind(envelope.get("kind")),
    )


def correlation_from_metadata(metadata: Mapping[str, Any] | None) -> PeerCorrelation:
    """ChatMessage.metadata 에서 상관 필드를 뽑는다(인증 정보는 옮기지 않는다)."""
    data = metadata or {}
    return PeerCorrelation(
        message_id=_normalize_id(data.get("message_id")),
        task_id=_normalize_id(data.get("task_id")),
        session_id=_normalize_id(data.get("session_id")),
        reply_to=_normalize_id(data.get("reply_to")),
        status=_normalize_status(data.get("status")),
        result=str(data.get("result") or "")[:MAX_RESULT_CHARS],
        kind=_normalize_kind(data.get("kind")),
    )


def correlation_summary(metadata: Mapping[str, Any] | None) -> str:
    """로그용 상관 ID 요약(빈 값은 생략). 인증 정보는 포함하지 않는다."""
    correlation = correlation_from_metadata(metadata)
    parts = [
        f"{name}={value}"
        for name in ("message_id", "task_id", "session_id", "reply_to", "kind", "status")
        if (value := getattr(correlation, name))
    ]
    return ",".join(parts) or "-"


def peer_callback_accepts_correlation(callback: Callable[..., Any]) -> bool:
    """콜백이 상관 정보를 세 번째 인자로 받을 수 있는지 판별한다."""
    try:
        parameters = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False
    positional = 0
    for parameter in parameters.values():
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            positional += 1
    return positional >= 3


_PREVIEW_PATTERNS = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-+/=]+"), r"\1 ***"),
    (
        re.compile(
            r"(?i)\b(auth_token|access_token|refresh_token|token|password|passwd|secret|"
            r"api_key|private_key|credential|authorization)\b\s*[:=]\s*\S+"
        ),
        r"\1=***",
    ),
)


def log_preview(content: object, limit: int = MAX_LOG_PREVIEW_CHARS) -> str:
    """서버 로그용 본문 미리보기 — 자격증명 패턴을 가리고 길이를 제한한다."""
    text = str(content or "")
    for pattern, replacement in _PREVIEW_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[: max(0, int(limit))]


def fill_peer_message_request(request: object, correlation: PeerCorrelation) -> str | None:
    """``PeerMessage.Request`` 에 상관 필드를 채우고, 실패하면 오류 문자열을 돌려준다.

    재빌드 전 생성코드에는 필드가 없어 ``setattr`` 이 예외를 내거나(protobuf) 값이
    조용히 사라진다(slotted 순수 객체). 어느 쪽이든 통신 내용 상관을 숨기지 않도록
    필드 부재를 먼저 감지해 ``REBUILD_REQUIRED_ERROR`` 로 알린다.
    """
    missing = [name for name in PEER_MESSAGE_FIELDS if not hasattr(request, name)]
    if missing:
        return f"{REBUILD_REQUIRED_ERROR} (missing: {', '.join(missing)})"
    for name, value in correlation.as_peer_message_fields().items():
        setattr(request, name, value)
    return None


def call_peer_message_callback_safely(
    callback: Callable[..., Any],
    sender_id: str,
    content: str,
    correlation: PeerCorrelation,
) -> Any:
    """협동 콜백을 호출하고, 예외는 gRPC UNKNOWN 으로 새지 않게 실패 tuple 로 바꾼다."""
    try:
        return call_peer_message_callback(callback, sender_id, content, correlation)
    except Exception as exc:  # noqa: BLE001 - 채널은 실패 응답으로 알린다
        logger.warning(
            "[peer_envelope] cooperation callback failed: %s: %s", type(exc).__name__, exc
        )
        return False, f"협동 메시지 처리 실패: {type(exc).__name__}", ""


def call_peer_message_callback(
    callback: Callable[..., Any],
    sender_id: str,
    content: str,
    correlation: PeerCorrelation,
) -> Any:
    """협동 메시지 콜백을 호출한다. 2-인자 legacy 콜백도 지원한다."""
    if peer_callback_accepts_correlation(callback):
        return callback(sender_id, content, correlation)
    return callback(sender_id, content)
