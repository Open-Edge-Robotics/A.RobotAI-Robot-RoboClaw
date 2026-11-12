"""협동 관측 기록의 민감정보 마스킹과 크기 제한.

저널은 로봇에서 일어난 일을 그대로 남기지만, 인증 토큰·비밀번호·모델 프롬프트
같은 값이 관측 API로 새면 안 된다. 이 모듈은 기록 직전에 값을 마스킹하고
문자열·컬렉션 크기를 상한으로 자른다. 순수 함수만 두어 ROS2/네트워크 없이
테스트할 수 있다.
"""

from __future__ import annotations

import json
import re
from typing import Any

#: 이벤트 content 상한. 원문 메시지는 이 길이까지만 남긴다.
MAX_CONTENT_CHARS = 2048
#: details 를 JSON 으로 직렬화했을 때의 상한.
MAX_DETAIL_CHARS = 2048
#: details 의 dict/list 항목 수 상한.
MAX_DETAIL_ITEMS = 20
#: details 의 중첩 깊이 상한.
MAX_DETAIL_DEPTH = 4
#: details 안 개별 문자열 상한.
MAX_DETAIL_STRING = 512
#: 마스킹된 값을 대신하는 표시.
MASK = "***"
#: 서버 로그 미리보기 기본 길이.
DEFAULT_LOG_PREVIEW_CHARS = 200

# 키 이름에 이 조각이 들어가면 값을 가린다(대소문자·구분자 무시).
_DENY_KEY_PARTS = (
    "token",
    "password",
    "passwd",
    "secret",
    "auth",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "privatekey",
    "cookie",
    "session_key",
    "bearer",
)

# 값 자체에 박힌 자격증명도 가린다. gRPC/HTTP 헤더가 details 로 흘러드는 경로가 있다.
_TEXT_PATTERNS = (
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-+/=]+"), r"\1 ***"),
    (
        re.compile(
            r"(?i)\b(auth_token|access_token|refresh_token|id_token|token|password|"
            r"passwd|secret|api_key|private_key|credential|authorization)\b\s*[:=]\s*\S+"
        ),
        r"\1=***",
    ),
)

_DEPTH_MARKER = "depth_limit"


def is_sensitive_key(key: object) -> bool:
    """키 이름이 민감정보를 담는지 여부."""
    normalized = str(key or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not normalized:
        return False
    return any(part in normalized for part in _DENY_KEY_PARTS)


def sanitize_text(value: object, limit: int = MAX_CONTENT_CHARS) -> str:
    """문자열에서 자격증명 패턴을 가리고 길이를 제한한다."""
    text = "" if value is None else str(value)
    for pattern, replacement in _TEXT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text[: max(0, int(limit))]


def preview_text(value: object, limit: int = DEFAULT_LOG_PREVIEW_CHARS) -> str:
    """로그용 미리보기 — 자격증명 패턴을 가리고 길이를 제한한다."""
    return sanitize_text(value, limit)


def sanitize_value(value: Any, depth: int = 0) -> Any:
    """중첩 구조를 재귀적으로 마스킹하고 깊이/개수를 제한한다."""
    if isinstance(value, dict):
        if depth >= MAX_DETAIL_DEPTH:
            return _DEPTH_MARKER
        sanitized: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_DETAIL_ITEMS:
                break
            name = str(key)
            sanitized[name] = MASK if is_sensitive_key(name) else sanitize_value(item, depth + 1)
        return sanitized
    if isinstance(value, (list, tuple, set, frozenset)):
        if depth >= MAX_DETAIL_DEPTH:
            return _DEPTH_MARKER
        return [sanitize_value(item, depth + 1) for item in list(value)[:MAX_DETAIL_ITEMS]]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return sanitize_text(value, MAX_DETAIL_STRING)


def sanitize_details(details: object, limit: int = MAX_DETAIL_CHARS) -> dict[str, Any]:
    """details dict 를 마스킹하고 직렬화 크기를 상한 이하로 맞춘다."""
    if not isinstance(details, dict) or not details:
        return {}
    sanitized = sanitize_value(details)
    try:
        encoded = json.dumps(sanitized, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return {}
    if len(encoded) <= limit:
        return sanitized
    # 상한을 넘으면 잘라낸 미리보기만 남긴다. 잘린 본문은 다시 문자열로 검사한다.
    return {
        "_truncated": True,
        "_preview": sanitize_text(encoded, max(0, limit // 2)),
    }
