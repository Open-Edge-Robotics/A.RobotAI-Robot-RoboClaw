import logging
import math
from typing import Any

from sensor_msgs.msg import LaserScan

from robo_claw_agent.skill_manager import BaseSkill

from .core import _distance_at_bearing

logger = logging.getLogger(__name__)


class GetDistanceSkill(BaseSkill):
    """라이다 기반 전방 거리 측정 스킬 (실제 연동)"""

    name = "get_distance"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {"direction": {"type": "string", "enum": ["front", "left", "right", "back"]}},
        "required": ["direction"],
        "additionalProperties": False,
    }
    description = (
        "라이다 센서로 지정 방향의 거리를 측정합니다. direction: front | left | right | back"
    )

    _VALID_DIRECTIONS = {"front", "left", "right", "back"}

    def validate_params(self, params: dict[str, Any]) -> bool:
        direction = params.get("direction", "front")
        return direction in self._VALID_DIRECTIONS

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        direction = params.get("direction", "front")
        lidar_topic = params.get("lidar_topic", "/scan")

        logger.info("Starting distance measurement: direction=%s", direction)

        scan = self.wait_for_message(LaserScan, lidar_topic, timeout_sec=2.0, max_age_sec=1.0)
        if not scan:
            return {
                "success": False,
                "message": f"라이다 데이터를 수신하지 못했습니다 ({lidar_topic})",
            }

        angle_map = {
            "front": 0.0,
            "left": math.pi / 2.0,
            "right": -math.pi / 2.0,
            "back": math.pi,
        }
        min_dist = _distance_at_bearing(scan, angle_map[direction])
        if min_dist is None or not math.isfinite(min_dist):
            return {
                "success": False,
                "message": f"{direction} 방향에 유효한 라이다 측정값이 없습니다.",
                "direction": direction,
                "distance_m": None,
            }

        return {
            "success": True,
            "message": f"{direction} 방향 거리: {min_dist:.2f}m",
            "direction": direction,
            "distance_m": round(min_dist, 3),
        }
