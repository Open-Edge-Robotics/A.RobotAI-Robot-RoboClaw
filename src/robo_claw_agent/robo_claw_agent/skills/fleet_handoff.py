"""동료 위임 경로 결정 — Maestro 관제 중에는 로봇끼리 직접 일을 시키지 않는다.

Maestro(FleetControl)에 연결된 로봇은 Maestro가 모든 로봇 명령의 단일 경로여야 한다.
그래야 safety policy, 위험 스킬 승인, 로봇별 실행 순서, Mission 기록이 지켜진다.
그래서 관제 중에는 ``tidy_home`` 같은 스킬이 동료를 직접 부르지 않고(``delegate_task``,
``call_peer_robot``), 필요한 동료 작업을 스킬 결과의 ``peer_requests`` 로 보고한다.
Maestro가 그 보고를 보고 해당 동료에게 후속 작업을 보낸다.

- ``RC_PEER_HANDOFF_MODE=report``: 직접 위임하지 않고 보고한다.
- ``RC_PEER_HANDOFF_MODE=direct``: 기존처럼 동료에게 직접 위임한다.
- 값이 없으면 ``MAESTRO_IP`` 가 설정되어 있을 때(FleetControl 연결 대상이 있을 때) ``report``,
  아니면 ``direct``.
"""

from __future__ import annotations

import os

HANDOFF_MODES = frozenset({"direct", "report"})
REPORTED_STATUS = "reported_to_maestro"


def peer_handoff_mode() -> str:
    raw = os.getenv("RC_PEER_HANDOFF_MODE", "").strip().lower()
    if raw in HANDOFF_MODES:
        return raw
    return "report" if os.getenv("MAESTRO_IP", "").strip() else "direct"


def reports_to_maestro() -> bool:
    return peer_handoff_mode() == "report"
