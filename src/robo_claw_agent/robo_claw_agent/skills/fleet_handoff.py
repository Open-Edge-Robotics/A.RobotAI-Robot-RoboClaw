"""Maestro 관제 중 로봇 간 협업 금지 정책.

Maestro(FleetControl)에 연결된 로봇은 Maestro가 모든 로봇 명령의 단일 경로여야
한다. 그래야 safety policy, 위험 스킬 승인, 로봇별 실행 순서, Mission 기록이
지켜진다. 그래서 관제 중에는 다음을 금지한다.

- 동료 로봇 직접 호출/위임(``delegate_task``, ``call_peer_robot`` 등)
- 동료 로봇 대상 브로드캐스트/자율협동(``autonomous_cooperate``)
- 동료로부터 오는 협동 메시지 수신

대신 ``tidy_home`` 같은 스킬은 필요한 동료 작업을 결과의 ``peer_requests`` 로
보고하고, Maestro가 그 보고를 보고 해당 동료에게 후속 작업을 보낸다.

- ``MAESTRO_ENABLED=true``: peer 협업 금지 + 동료 작업은 Maestro에 보고.
- ``MAESTRO_ENABLED=false``(기본값): 기존 동료 직접 위임 동작 유지.
- ``RC_PEER_HANDOFF_MODE`` 는 Maestro 비활성일 때만 유효하다. 활성 상태에서
  ``direct`` 를 지정하면 무시하고 ``report`` 로 동작한다.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

HANDOFF_MODES = frozenset({"direct", "report"})
REPORTED_STATUS = "reported_to_maestro"

# Maestro 관제 중 로봇이 직접 사용하면 안 되는 스킬.
# 자리 양보(request_yield_slot)는 협업이 아니라 결정론적 안전 프로토콜이므로 제외한다.
PEER_COLLAB_SKILLS = frozenset(
    {
        "delegate_task",
        "call_peer_robot",
        "broadcast_to_peers",
        "list_peer_robots",
        "query_peer_status",
        "query_peer_capabilities",
        "coordinate_peer_task",
        "autonomous_cooperate",
        "stop_autonomous_cooperate",
    }
)


def _env_flag(name: str, default: bool = False) -> bool:
    """Read a boolean env value; unset or empty keeps the default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def maestro_control_active() -> bool:
    """Maestro가 이 로봇의 명령 단일 경로인지 여부."""
    return _env_flag("MAESTRO_ENABLED")


def peer_handoff_mode() -> str:
    raw = os.getenv("RC_PEER_HANDOFF_MODE", "").strip().lower()
    if maestro_control_active():
        if raw == "direct":
            logger.warning("RC_PEER_HANDOFF_MODE=direct is ignored while MAESTRO_ENABLED=true")
        return "report"
    if raw in HANDOFF_MODES:
        return raw
    return "direct"


def reports_to_maestro() -> bool:
    return peer_handoff_mode() == "report"
