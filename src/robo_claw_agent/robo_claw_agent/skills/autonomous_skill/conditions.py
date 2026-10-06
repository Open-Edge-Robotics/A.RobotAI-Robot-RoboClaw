import logging
import math

import numpy as np

from robo_claw_agent.skill_manager import BaseSkill

from .core import BTNode, NodeStatus
from .globals import _AUTONOMOUS_ACTIVE

logger = logging.getLogger(__name__)


class CheckAutonomousActive(BTNode):
    def __init__(self) -> None:
        super().__init__("CheckAutonomousActive")

    def tick(self) -> NodeStatus:
        return NodeStatus.SUCCESS if _AUTONOMOUS_ACTIVE.is_set() else NodeStatus.FAILURE


class CheckBatterySufficient(BTNode):
    def __init__(
        self,
        skill: BaseSkill,
        min_pct: float = 20.0,
        allow_unknown: bool = True,
        check_only: bool = True,
    ) -> None:
        super().__init__("CheckBatterySufficient")
        self._skill = skill
        self._min_pct = min_pct
        self._allow_unknown = allow_unknown
        self._check_only = check_only

    def tick(self) -> NodeStatus:
        try:
            from sensor_msgs.msg import BatteryState  # type: ignore[import]

            msg = self._skill.wait_for_message(BatteryState, "/battery_state", timeout_sec=2.0)
            if msg is None:
                # 배터리 토픽 없음 -> 호스트 sysfs 배터리 직접 조회 시도
                pct = self._normalize_battery_percentage(self._read_sysfs_battery())
                if pct is None or pct <= 0.0:
                    return self._unknown_battery()
            else:
                pct = self._normalize_battery_percentage(getattr(msg, "percentage", None))
                if pct is None:
                    # BatteryState uses negative/NaN values for unknown readings on
                    # several test and hardware drivers. They are not 0% battery.
                    return self._unknown_battery()

            self._blackboard["battery_pct"] = pct
            self._blackboard["battery_unknown"] = False
            if pct >= self._min_pct:
                return NodeStatus.SUCCESS

            self._blackboard["low_battery_pct"] = pct
            if not self._check_only:
                logger.warning(
                    "[BT] Low battery: %.1f%% < %.1f%% — fail-closed", pct, self._min_pct
                )
                return NodeStatus.FAILURE
            logger.warning(
                "[BT] Low battery: %.1f%% < %.1f%% — check-only mode", pct, self._min_pct
            )
            return NodeStatus.SUCCESS
        except Exception as exc:
            logger.error("[BT] Battery check error: %s", exc)
            return self._unknown_battery()

    @staticmethod
    def _normalize_battery_percentage(value: object) -> float | None:
        """Return a valid percentage, or ``None`` for an unknown reading.

        ROS BatteryState commonly reports ``-1`` or ``NaN`` when a driver has no
        battery telemetry. Treating either value as 0% would incorrectly trigger
        the low-battery safety path on test robots.
        """
        try:
            pct = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(pct) or pct < 0.0 or pct > 100.0:
            return None
        if pct <= 1.0:
            pct *= 100.0
        return pct

    def _unknown_battery(self) -> NodeStatus:
        # Keep numeric compatibility for consumers such as EmergencyLowBattery;
        # battery_unknown is the authoritative distinction from a real 0% value.
        self._blackboard["battery_pct"] = 0.0
        self._blackboard["low_battery_pct"] = 0.0
        self._blackboard["battery_unknown"] = True
        if not self._check_only and not self._allow_unknown:
            logger.warning("[BT] Battery percentage unknown/unreadable — fail-closed")
            return NodeStatus.FAILURE
        logger.warning("[BT] Battery percentage unknown/unreadable — warning only")
        return NodeStatus.SUCCESS

    def _read_sysfs_battery(self) -> float:
        from pathlib import Path

        for bat_dir in Path("/sys/class/power_supply").glob("BAT*"):
            try:
                cap_file = bat_dir / "capacity"
                if cap_file.exists():
                    return float(cap_file.read_text().strip())
            except Exception:
                pass
        return 0.0


class CheckMapCoverage(BTNode):
    def __init__(self, skill: BaseSkill, min_pct: float = 40.0) -> None:
        super().__init__("CheckMapCoverage")
        self._skill = skill
        self._min_pct = min_pct

    def tick(self) -> NodeStatus:
        try:
            from nav_msgs.msg import OccupancyGrid  # type: ignore[import]

            map_msg = self._skill.wait_for_message(
                OccupancyGrid, "/map", timeout_sec=5.0, use_transient_local=True
            )
            if map_msg is None:
                return NodeStatus.FAILURE  # 맵 없음 → 탐험 필요
            data = np.array(map_msg.data)
            coverage = (1.0 - np.sum(data == -1) / data.size) * 100.0
            self._blackboard["current_map_coverage"] = round(coverage, 1)
            logger.debug("[BT] Map coverage: %.1f%%", coverage)
            return NodeStatus.SUCCESS if coverage >= self._min_pct else NodeStatus.FAILURE
        except Exception as exc:
            logger.error("[BT] Map check error: %s", exc)
            return NodeStatus.FAILURE


class CheckHasMap(BTNode):
    def __init__(self) -> None:
        super().__init__("CheckHasMap")

    def tick(self) -> NodeStatus:
        has_map = self._blackboard.get("has_map", False)
        return NodeStatus.SUCCESS if has_map else NodeStatus.FAILURE
