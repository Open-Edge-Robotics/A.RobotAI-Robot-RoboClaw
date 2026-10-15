"""sysfs 기반 배터리 잔량 조회 헬퍼.

``autonomous_skill/conditions.py``(배터리 게이트)와 ``system_skill/status.py``
(상태 보고)가 동일한 sysfs 탐색 루프를 각자 복사해 갖고 있었다.
"""

from __future__ import annotations

from pathlib import Path

BATTERY_SUPPLY_GLOB = "/sys/class/power_supply"


def read_sysfs_battery(default: float = 0.0) -> float:
    """첫 번째 BAT* supply 의 capacity(%) 를 읽는다. 실패 시 ``default``."""
    for bat_dir in Path(BATTERY_SUPPLY_GLOB).glob("BAT*"):
        try:
            cap_file = bat_dir / "capacity"
            if cap_file.exists():
                return float(cap_file.read_text().strip())
        except Exception:  # noqa: BLE001 - sysfs 접근 실패는 default 로 흡수
            pass
    return default
