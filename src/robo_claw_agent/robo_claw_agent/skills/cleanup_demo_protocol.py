"""검토된 CLOi 정리 시연 작업의 좁은 피어 요청 계약."""

from __future__ import annotations

import json
from typing import Any

CLEANUP_DEMO_PREFIX = "ROBOCLAW_CLEANUP_DEMO_V1:"
_REQUIRED_FIELDS = frozenset({"request_id", "target_location", "target_object", "scene_summary"})
_ALLOWED_FIELDS = _REQUIRED_FIELDS
_FIELD_LIMITS = {
    "request_id": 80,
    "target_location": 80,
    "target_object": 120,
    "scene_summary": 800,
}


def _validated_payload(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict) or not _REQUIRED_FIELDS.issubset(value):
        return None
    if set(value) - _ALLOWED_FIELDS:
        return None

    normalized: dict[str, str] = {}
    for field, limit in _FIELD_LIMITS.items():
        if field not in value:
            continue
        item = value[field]
        if not isinstance(item, str):
            return None
        item = item.strip()
        if field in _REQUIRED_FIELDS and not item:
            return None
        if len(item) > limit:
            return None
        normalized[field] = item
    return normalized


def encode_cleanup_demo_request(request: dict[str, Any]) -> str:
    """Encode a semantic one-object request; object poses are intentionally unsupported."""
    payload = _validated_payload(request)
    if payload is None:
        raise ValueError("정리 시연 요청의 필드 또는 값이 올바르지 않습니다.")
    return CLEANUP_DEMO_PREFIX + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def decode_cleanup_demo_request(instruction: str) -> dict[str, str] | None:
    """Decode only the versioned internal request; malformed commands fail closed."""
    if not isinstance(instruction, str) or not instruction.startswith(CLEANUP_DEMO_PREFIX):
        return None
    raw = instruction[len(CLEANUP_DEMO_PREFIX) :]
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return _validated_payload(value)
