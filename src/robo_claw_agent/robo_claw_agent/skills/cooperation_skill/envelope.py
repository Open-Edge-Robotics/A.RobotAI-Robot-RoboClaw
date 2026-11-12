"""동료 협동 메시지 envelope — 상관 ID와 구조화된 결과 상태.

gRPC ``ChatMessage.content`` 는 자유 문자열이므로, 협동 메시지는 JSON envelope 로
감싸 보낸다. envelope 는 메시지/태스크/세션 식별자와 회신 대상(message_id), 그리고
실행 결과 상태를 함께 실어 로봇 사이에서 같은 작업을 같은 ID로 추적하게 한다.

구버전 로봇은 식별자 없이 ``content`` 만 보낸다. 그런 메시지도 받아들이고, 받는
쪽에서 ID를 새로 만든다(하위 호환). 순수 모듈이라 ROS2/네트워크 없이 테스트한다.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

#: 협동 메시지를 식별하는 envelope 표식. gRPC metadata["type"] 과 같은 값이다.
COOPERATION_MESSAGE_TYPE = "peer_cooperation"

#: 새 작업을 위임하는 메시지. 이 kind 만 task 중복 차단 키를 소유한다.
KIND_REQUEST = "request"
#: 대화 응답. 같은 task_id 의 후속 메시지여도 재전송 차단에 걸리지 않는다.
KIND_REPLY = "reply"
#: 실행 결과 보고. status/result 를 함께 싣는다.
KIND_RESULT = "result"
KINDS = frozenset({KIND_REQUEST, KIND_REPLY, KIND_RESULT})

# 태스크 수명주기 상태 — queued 는 "전송 대기열에 넣었다"이지 상대가 받았다는 뜻이 아니다.
TASK_STATUS_QUEUED = "queued"
TASK_STATUS_RECEIVED = "received"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_DENIED = "denied"
TASK_STATUS_PARTIAL = "partial"
TASK_STATUS_UNKNOWN = "unknown"
TASK_STATUSES = frozenset(
    {
        TASK_STATUS_QUEUED,
        TASK_STATUS_RECEIVED,
        TASK_STATUS_RUNNING,
        TASK_STATUS_COMPLETED,
        TASK_STATUS_FAILED,
        TASK_STATUS_DENIED,
        TASK_STATUS_PARTIAL,
        TASK_STATUS_UNKNOWN,
    }
)

#: 원격 실행 결과가 확정된 상태. 이 상태만 상관된 관측 결과로 인정한다.
OBSERVED_RESULT_STATUSES = frozenset(
    {TASK_STATUS_COMPLETED, TASK_STATUS_FAILED, TASK_STATUS_DENIED, TASK_STATUS_PARTIAL}
)

#: PeerMessage 서비스에서 에이전트로 넘어오는 상관 필드(구버전 srv 감지에도 쓴다).
PEER_MESSAGE_CORRELATION_FIELDS = (
    "message_id",
    "task_id",
    "session_id",
    "reply_to",
    "status",
    "result",
    "kind",
)
#: 재빌드 전 생성코드로는 상관 정보를 보존할 수 없다는 명시 오류.
REBUILD_REQUIRED_ERROR = (
    "robo_claw_msgs rebuild required: PeerMessage.srv correlation fields are missing"
)

_MESSAGE_TYPE_KEY = "_robo_claw_message_type"
MAX_ENVELOPE_CONTENT_CHARS = 4096
MAX_ID_CHARS = 64
MAX_RESULT_CHARS = 1024
MAX_STATUS_CHARS = 32
_ID_PATTERN = re.compile(r"[^A-Za-z0-9._:-]+")


def new_id() -> str:
    """협동 추적용 짧은 고유 ID."""
    return uuid.uuid4().hex


def normalize_id(value: object) -> str:
    """전송 경계에서 들어온 ID를 안전한 문자/길이로 정규화한다."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _ID_PATTERN.sub("_", raw)[:MAX_ID_CHARS]


def normalize_kind(value: object) -> str:
    """envelope kind 를 알려진 값으로 정규화한다. 모르는 값은 빈 문자열."""
    raw = str(value or "").strip().lower()
    return raw if raw in KINDS else ""


def normalize_task_status(value: object, default: str = TASK_STATUS_UNKNOWN) -> str:
    """태스크 상태 문자열을 알려진 상태로 정규화한다."""
    raw = str(value or "").strip().lower()[:MAX_STATUS_CHARS]
    return raw if raw in TASK_STATUSES else default


@dataclass(frozen=True)
class PeerEnvelope:
    """로봇 사이에서 오가는 협동 메시지 한 건."""

    content: str = ""
    message_id: str = ""
    task_id: str = ""
    session_id: str = ""
    reply_to: str = ""
    kind: str = ""
    status: str = ""
    result: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0

    @property
    def has_observed_result(self) -> bool:
        """구조화된 실행 결과를 담고 있는지."""
        return self.status in OBSERVED_RESULT_STATUSES

    def to_payload(self) -> dict[str, Any]:
        """gRPC content 로 실을 JSON payload."""
        return {
            _MESSAGE_TYPE_KEY: COOPERATION_MESSAGE_TYPE,
            "content": self.content,
            "message_id": self.message_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "reply_to": self.reply_to,
            "kind": self.kind,
            "status": self.status,
            "result": self.result,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


def envelope_from_transport(
    *,
    content: str,
    message_id: str = "",
    task_id: str = "",
    session_id: str = "",
    reply_to: str = "",
    status: str = "",
    result: str = "",
    kind: str = "",
) -> PeerEnvelope:
    """전송 계층(ROS 서비스/gRPC metadata)에서 받은 필드로 envelope 를 만든다."""
    return PeerEnvelope(
        content=str(content or "").strip()[:MAX_ENVELOPE_CONTENT_CHARS],
        message_id=normalize_id(message_id),
        task_id=normalize_id(task_id),
        session_id=normalize_id(session_id),
        reply_to=normalize_id(reply_to),
        kind=normalize_kind(kind),
        status=normalize_task_status(status, default=""),
        result=str(result or "")[:MAX_RESULT_CHARS],
    )


def build_outbound_envelope(
    *,
    content: str,
    message_id: str,
    task_id: str,
    session_id: str = "",
    reply_to: str = "",
    kind: str = "",
    status: str = "",
    result: str = "",
    lifetime_sec: float = 300.0,
) -> PeerEnvelope:
    """송신용 envelope 를 만든다. 만료 시각은 지금부터 ``lifetime_sec``."""
    now = time.time()
    return PeerEnvelope(
        content=str(content or "")[:MAX_ENVELOPE_CONTENT_CHARS],
        message_id=normalize_id(message_id),
        task_id=normalize_id(task_id),
        session_id=normalize_id(session_id),
        reply_to=normalize_id(reply_to),
        kind=normalize_kind(kind),
        status=normalize_task_status(status, default=""),
        result=str(result or "")[:MAX_RESULT_CHARS],
        created_at=now,
        expires_at=now + max(1.0, float(lifetime_sec)),
    )


def missing_request_fields(request: object) -> list[str]:
    """요청 객체에 없는 상관 필드 이름 목록(재빌드 전 생성코드 감지)."""
    return [name for name in PEER_MESSAGE_CORRELATION_FIELDS if not hasattr(request, name)]


def is_result_report(*, kind: object = "", status: object = "") -> bool:
    """실행 결과/상태 보고로 식별되는 메시지인지.

    ``kind == result`` 이거나 ``status`` 가 확정 결과 상태면 결과 보고로 본다. 이런
    메시지는 상관이 맞을 때만 관측 결과로 반영하고, 아니면 실행/LLM 경로로 보내지
    않고 거부 관측으로 끝낸다.
    """
    if normalize_kind(kind) == KIND_RESULT:
        return True
    return normalize_task_status(status, default="") in OBSERVED_RESULT_STATUSES


def parse_envelope(raw: object) -> PeerEnvelope | None:
    """JSON 문자열(또는 dict)을 envelope 로 해석한다. 협동 메시지가 아니면 None."""
    if isinstance(raw, dict):
        data: dict[str, Any] = raw
    else:
        text = str(raw or "").strip()
        if not text.startswith("{"):
            return None
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        data = parsed

    if str(data.get(_MESSAGE_TYPE_KEY, "")).strip() != COOPERATION_MESSAGE_TYPE:
        return None
    content = str(data.get("content") or "").strip()
    if not content:
        return None
    return envelope_from_transport(
        content=content,
        message_id=data.get("message_id", ""),
        task_id=data.get("task_id", ""),
        session_id=data.get("session_id", ""),
        reply_to=data.get("reply_to", ""),
        kind=data.get("kind", ""),
        status=data.get("status", ""),
        result=data.get("result", ""),
    )
