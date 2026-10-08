import logging
from datetime import datetime
from typing import Any

from sensor_msgs.msg import BatteryState

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)


class GetStatusSkill(BaseSkill):
    """로봇 하드웨어 상태 정보(배터리, 위치 등) 조회 스킬"""

    name = "get_status"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "include_image": {
                "type": "boolean",
                "default": False,
                "description": "실시간 카메라 이미지 포함 여부 (VLM 분석용). 기본값은 false입니다.",
            },
            "camera_topic": {
                "type": "string",
                "description": "카메라 ROS 토픽명 (선택)",
            },
        },
        "additionalProperties": False,
    }
    risk_level = "read"
    description = (
        "로봇의 실시간 배터리 잔량, 현재 좌표(map 기준) 등 하드웨어 텔레메트리 정보를 조회합니다. "
        "주의: 이 도구는 주변에 장애물이 있는지, 문이 열려 있는지 등 '공간 인지' 정보는 제공하지 않습니다. "
        "공간 정보가 필요하면 반드시 get_map_visual 또는 analyze_scene 스킬을 사용하세요."
    )

    def get_robot_summary(self) -> dict[str, Any]:
        """에이전트 컨텍스트 주입을 위한 상세 요약 정보"""
        battery = self.wait_for_message(BatteryState, "/battery_state", timeout_sec=0.5)
        battery_data = {"percentage": "unknown", "status": "unknown"}
        if battery:
            pct = battery.percentage
            if 0.0 <= pct <= 1.0:
                pct *= 100.0
            battery_data = {
                "percentage": f"{pct:.1f}%",
                "voltage": f"{battery.voltage:.2f}V",
                "is_charging": battery.power_supply_status
                == BatteryState.POWER_SUPPLY_STATUS_CHARGING,
            }
        else:
            # sysfs 호스트 배터리 직접 조회
            pct = self._read_sysfs_battery()
            if pct > 0.0:
                status_str = self._read_sysfs_battery_status()
                battery_data = {
                    "percentage": f"{pct:.1f}%",
                    "voltage": "unknown",
                    "is_charging": status_str == "Charging",
                }

        map_pose = self.get_map_pose()
        pose_data = "unknown"
        if map_pose:
            pose_data = {
                "x": round(map_pose["x"], 2),
                "y": round(map_pose["y"], 2),
                "heading_deg": map_pose["heading_deg"],
                "frame": map_pose["frame"],
            }

        now = datetime.now().astimezone()
        _korean_weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

        return {
            "battery": battery_data,
            "pose": pose_data,
            "status": "Normal",
            # timestamp는 기존 소비자(문자열 가정)와의 호환을 위해 문자열을 유지하고,
            # 날짜/요일/타임존 등 상세 정보는 datetime_info로 분리해 제공한다.
            "timestamp": now.strftime("%H:%M:%S"),
            "datetime_info": {
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                "weekday": _korean_weekdays[now.weekday()],
                "timezone": str(now.tzinfo),
            },
        }

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

    def _read_sysfs_battery_status(self) -> str:
        from pathlib import Path

        for bat_dir in Path("/sys/class/power_supply").glob("BAT*"):
            try:
                status_file = bat_dir / "status"
                if status_file.exists():
                    return status_file.read_text().strip()
            except Exception:
                pass
        return "Unknown"

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        logger.info("Starting comprehensive robot status query")
        summary = self.get_robot_summary()

        # 실시간 카메라 이미지 포함 여부 결정 (VLM 분석용, 기본값 False)
        include_image = bool(params.get("include_image", False))

        if include_image:
            import base64

            import cv2

            cv_img, camera_topic, is_compressed = self.get_opencv_image(
                params, camera_topic_param_name="camera_topic", timeout_sec=1.0
            )
            if cv_img is not None:
                try:
                    _, buffer = cv2.imencode(".jpg", cv_img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    img_base64 = base64.b64encode(buffer).decode("utf-8")
                    summary["camera_scene_base64"] = img_base64
                    logger.info("Camera snapshot included")
                except Exception as e:
                    logger.warning("Image encoding failed: %s", e)

        return {
            "success": True,
            "message": f"현재 로봇 상태 조회 완료 (배터리: {summary['battery']['percentage']}, 위치: {summary['pose']})",
            "status_details": summary,
        }
