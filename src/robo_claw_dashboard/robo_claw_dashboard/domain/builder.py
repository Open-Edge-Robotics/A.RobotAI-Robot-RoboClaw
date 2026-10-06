"""대시보드 스냅샷 취합 — 여러 소스의 상태 데이터를 단일 JSON 구조로 정규화.

주의: ROS2 / 네트워크 / generated message import 금지. 입력은 순수 Python dict/str만 받는다.
"""

import json
from datetime import datetime, timezone
from typing import Any

from .models import normalize_state_code


def _parse_state_json(state_json: str | None) -> dict[str, Any]:
    """QueryState 응답의 state_json 문자열을 안전하게 dict로 파싱한다."""
    if not isinstance(state_json, str):
        return {}
    try:
        parsed = json.loads(state_json)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_snapshot(
    state_json: str | None = None,
    agent_status: dict[str, Any] | None = None,
    telemetry: dict[str, Any] | None = None,
    sensor_health: dict[str, Any] | None = None,
    peers: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """여러 상태 소스를 단일 대시보드 스냅샷으로 정규화한다."""
    agent_status = agent_status or {}
    timestamp = now or datetime.now(timezone.utc)
    return {
        "state": _parse_state_json(state_json),
        "agent": {
            "state": normalize_state_code(agent_status.get("state")),
            "current_skill": agent_status.get("current_skill", "") or "",
            "message": agent_status.get("message", "") or "",
        },
        "telemetry": dict(telemetry) if telemetry else {},
        "sensor_health": dict(sensor_health) if sensor_health else {},
        "peers": list(peers) if peers else [],
        "timestamp": timestamp.isoformat(),
    }
