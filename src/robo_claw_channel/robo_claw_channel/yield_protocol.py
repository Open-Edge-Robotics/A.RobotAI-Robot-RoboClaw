"""자리 양보 gRPC 전송 규약 — 자유 문자열 대신 구조화된 JSON 페이로드.

서브넷이 분리된 로봇은 ROS 서비스 발견이 되지 않으므로, 메신저 클라이언트가
``SendCommand`` 의 ``content`` 로 이 페이로드를 보낸다. 수신측 채널 노드가
``metadata["type"] == "yield_slot"`` 을 보고 디코드해 로컬 에이전트의
``YieldSlot`` 서비스로 넘긴다.

JSON 을 쓰는 이유는 기존 gRPC 메시지가 ``content: string`` 만 갖기 때문이다.
그래서 수신측은 **형식 검증을 통과한 값만** 서비스로 승격한다(fail-closed).
"""

from __future__ import annotations

import json
import math
from typing import Any

YIELD_MESSAGE_TYPE = "yield_slot"

# 자유 문자열 채널에 실리는 페이로드이므로 크기를 제한한다.
MAX_PAYLOAD_LENGTH = 2048
# 요청 값 상한 — 손상된 페이로드가 비정상 파라미터로 승격되지 않게 한다.
MAX_RADIUS_M = 2.0
MAX_TIMEOUT_SEC = 120.0
# 장소 이름 상한 — 자유 문자열 채널로 실리므로 길이를 제한한다.
MAX_PLACE_LENGTH = 64

_ALLOWED_DECISIONS = frozenset({"not_needed", "accepted", "busy", "denied"})


def _finite(value: Any) -> float | None:
    """유한한 숫자만 float 로 반환한다(nan/inf 제외)."""
    parsed = _parse_float(value)
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


def _parse_float(value: Any) -> float | None:
    """숫자로 해석 가능한 값만 float 로 반환한다(nan 허용)."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _finite_or_nan(value: Any) -> float | None:
    """유한값 또는 nan 을 반환한다. inf·비숫자는 None."""
    parsed = _parse_float(value)
    if parsed is None or math.isinf(parsed):
        return None
    return parsed


def encode_request(
    *,
    requester_id: str,
    target_place: str,
    radius_m: float = 0.0,
    timeout_sec: float = 0.0,
    requester_at_target: bool | None = None,
) -> str | None:
    """요청 페이로드를 만든다. 값이 유효하지 않으면 None.

    목표는 장소 이름만 담는다. 로봇마다 같은 맵이라도 좌표가 다를 수 있어
    좌표를 로봇 사이에 넘기지 않는다.
    """
    requester = str(requester_id or "").strip()
    place = str(target_place or "").strip()
    if not requester or not place:
        return None
    if len(place) > MAX_PLACE_LENGTH:
        return None
    radius = _finite(radius_m)
    timeout = _finite(timeout_sec)
    if radius is None or radius < 0.0 or radius > MAX_RADIUS_M:
        return None
    if timeout is None or timeout < 0.0 or timeout > MAX_TIMEOUT_SEC:
        return None

    payload: dict[str, Any] = {
        "requester_id": requester,
        "target_place": place,
        "radius_m": radius,
        "timeout_sec": timeout,
    }
    if requester_at_target is not None:
        payload["requester_at_target"] = bool(requester_at_target)
    encoded = json.dumps(payload, separators=(",", ":"))
    return encoded if len(encoded) <= MAX_PAYLOAD_LENGTH else None


def decode_request(content: str) -> dict[str, Any] | None:
    """수신한 요청 페이로드를 검증해 dict 로 돌려준다. 실패하면 None."""
    raw = str(content or "").strip()
    if not raw or len(raw) > MAX_PAYLOAD_LENGTH:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    requester_id = str(data.get("requester_id") or "").strip()
    target_place = str(data.get("target_place") or "").strip()
    if not requester_id or not target_place:
        return None
    if len(target_place) > MAX_PLACE_LENGTH:
        return None

    radius = _finite(data.get("radius_m"))
    radius = 0.0 if radius is None else radius
    if radius < 0.0 or radius > MAX_RADIUS_M:
        return None
    timeout = _finite(data.get("timeout_sec"))
    timeout = 0.0 if timeout is None else timeout
    if timeout < 0.0 or timeout > MAX_TIMEOUT_SEC:
        return None

    payload: dict[str, Any] = {
        "requester_id": requester_id,
        "target_place": target_place,
        "radius_m": radius,
        "timeout_sec": timeout,
    }
    if "requester_at_target" in data:
        payload["requester_at_target"] = bool(data.get("requester_at_target"))
    return payload


def encode_response(
    *,
    decision: str,
    reason: str = "",
    distance_m: float = -1.0,
    retry_after_sec: float = 0.0,
    has_yield_spot: bool = False,
    yield_spot_x: float = 0.0,
    yield_spot_y: float = 0.0,
    message: str = "",
) -> str:
    """판정 결과를 응답 페이로드로 만든다."""
    distance = _finite(distance_m)
    retry_after = _finite(retry_after_sec)
    payload = {
        "decision": str(decision or ""),
        "reason": str(reason or ""),
        "distance_m": -1.0 if distance is None else distance,
        "retry_after_sec": 0.0 if retry_after is None else max(0.0, retry_after),
        "has_yield_spot": bool(has_yield_spot),
        "yield_spot_x": _finite(yield_spot_x) or 0.0,
        "yield_spot_y": _finite(yield_spot_y) or 0.0,
        "message": str(message or "")[:1024],
    }
    return json.dumps(payload, separators=(",", ":"))


def decode_response(content: str) -> dict[str, Any] | None:
    """응답 페이로드를 검증해 dict 로 돌려준다. 알 수 없는 판정 코드는 거부한다."""
    raw = str(content or "").strip()
    if not raw or len(raw) > MAX_PAYLOAD_LENGTH:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    decision = str(data.get("decision") or "").strip()
    if decision not in _ALLOWED_DECISIONS:
        return None

    distance = _finite(data.get("distance_m"))
    retry_after = _finite(data.get("retry_after_sec"))
    return {
        "decision": decision,
        "reason": str(data.get("reason") or ""),
        "distance_m": distance if distance is not None else -1.0,
        "retry_after_sec": max(0.0, retry_after) if retry_after is not None else 0.0,
        "has_yield_spot": bool(data.get("has_yield_spot", False)),
        "yield_spot_x": _finite(data.get("yield_spot_x")) or 0.0,
        "yield_spot_y": _finite(data.get("yield_spot_y")) or 0.0,
        "message": str(data.get("message") or "")[:1024],
    }
