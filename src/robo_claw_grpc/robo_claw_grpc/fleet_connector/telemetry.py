"""Collect FleetControl telemetry from existing Robo-Claw nodes."""

from __future__ import annotations

import math


class TelemetryProvider:
    def __init__(self, config, battery_node=None, nav_node=None) -> None:
        self.config = config
        self.battery_node = battery_node
        self.nav_node = nav_node

    def __call__(self) -> dict:
        data = {
            "map_id": self.config.map_id,
            "frame_id": self.config.map_frame_id,
            "operational_state": "IDLE",
            "health_issues": [],
        }
        if self.battery_node is not None:
            try:
                data["battery_percentage"] = float(self.battery_node.get_battery_status())
            except Exception:
                data["health_issues"].append("battery")
        if self.nav_node is not None:
            try:
                pose = self.nav_node.get_current_pose()
                if pose:
                    value = pose.pose.pose
                    data.update(
                        {
                            "x": float(value.position.x),
                            "y": float(value.position.y),
                            "z": float(value.position.z),
                            "yaw": self._yaw(value.orientation),
                        }
                    )
                if hasattr(self.nav_node, "is_navigating") and self.nav_node.is_navigating():
                    data["operational_state"] = "EXECUTING"
            except Exception:
                data["health_issues"].append("localization")
        return data

    @staticmethod
    def _yaw(q) -> float:
        return math.atan2(
            2.0 * (q.w * q.z + q.x * q.y),
            1.0 - 2.0 * (q.y * q.y + q.z * q.z),
        )
