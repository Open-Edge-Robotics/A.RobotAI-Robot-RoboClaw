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
    target_x: float,
    target_y: float,
    requester_x: float,
    requester_y: float,
    radius_m: float = 0.0,
    timeout_sec: float = 0.0,
    target_place: str = "",
    has_target_coords: bool = True,
    requester_at_target: bool | None = None,
) -> str | None:
    """요청 페이로드를 만든다. 값이 유효하지 않으면 None.

    ``has_target_coords=False`` 면 장소 이름(``target_place``)만으로 요청한다.
    두 로봇이 같은 맵을 쓰지 않을 때 쓰는 방식이다.
    """
    if not str(requester_id or "").strip():
        return None
    place = str(target_place or "").strip()
    target = (_finite(target_x), _finite(target_y)) if has_target_coords else (0.0, 0.0)
    requester = (_finite_or_nan(requester_x), _finite_or_nan(requester_y))
    if has_target_coords and (target[0] is None or target[1] is None):
        return None
    if not has_target_coords and not place:
        return None
    if len(place) > MAX_PLACE_LENGTH:
        return None
    # 요청자 위치는 모를 수 있다(nan). 다만 숫자여야 한다.
    if requester[0] is None or requester[1] is None:
        return None
    radius = _finite(radius_m)
    timeout = _finite(timeout_sec)
    if radius is None or radius < 0.0 or radius > MAX_RADIUS_M:
        return None
    if timeout is None or timeout < 0.0 or timeout > MAX_TIMEOUT_SEC:
        return None
    payload = {
        "requester_id": str(requester_id).strip(),
        "has_target_coords": bool(has_target_coords),
        "target_x": target[0],
        "target_y": target[1],
        "target_place": place,
        "requester_x": requester[0],
        "requester_y": requester[1],
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
    if not requester_id:
        return None

    has_target_coords = bool(data.get("has_target_coords", True))
    target_place = str(data.get("target_place") or "").strip()
    if len(target_place) > MAX_PLACE_LENGTH:
        return None
    if has_target_coords:
        target_x = _finite(data.get("target_x"))
        target_y = _finite(data.get("target_y"))
        if target_x is None or target_y is None:
            return None
    else:
        target_x = target_y = 0.0
        if not target_place:
            return None

    # 요청자 위치는 nan 을 허용한다(위치 미상). 숫자가 아니면 거부한다.
    requester_x = _finite_or_nan(data.get("requester_x"))
    requester_y = _finite_or_nan(data.get("requester_y"))
    if requester_x is None or requester_y is None:
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
        "has_target_coords": has_target_coords,
        "target_x": target_x,
        "target_y": target_y,
        "target_place": target_place,
        "requester_x": requester_x,
        "requester_y": requester_y,
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
