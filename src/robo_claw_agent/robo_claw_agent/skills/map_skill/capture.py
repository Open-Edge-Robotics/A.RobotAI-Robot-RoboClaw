import base64
import logging
import time
from typing import Any

import cv2
import numpy as np
from nav_msgs.msg import OccupancyGrid  # type: ignore[import]

from robo_claw_agent.skill_manager import BaseSkill

from ._grid_image import build_map_image

logger = logging.getLogger(__name__)


class CaptureMapSkill(BaseSkill):
    """VLM 분석 없이 맵 데이터를 시각화하여 이미지로 변환 및 획득하는 스킬"""

    name = "capture_map"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "map_topic": {"type": "string", "default": "/map"},
            "output_path": {"type": "string"},
        },
        "additionalProperties": False,
    }
    description = (
        "현재 맵(OccupancyGrid) 이미지 획득 요청이 있을 때 "
        "VLM 분석 없이 즉시 맵 이미지를 캡처하여 로컬에 저장하고 경로를 반환합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        map_topic = params.get("map_topic", "/map")

        # 유니크한 파일명 생성
        timestamp = int(time.time())
        default_path = f"/tmp/map_capture_{timestamp}.png"
        output_path = params.get("output_path", default_path)

        logger.info("Starting simple map image capture: topic=%s (10s)", map_topic)

        map_msg = self.wait_for_message(
            OccupancyGrid,
            map_topic,
            timeout_sec=10.0,
            use_transient_local=True,
            max_age_sec=10.0,
        )
        if not map_msg:
            return {
                "success": False,
                "message": f"맵 데이터를 수신할 수 없습니다. (topic={map_topic})",
            }
        frame_ok, frame_error = self.validate_message_frame(
            map_msg, getattr(self.node, "_map_frame", "map"), "OccupancyGrid"
        )
        if not frame_ok:
            return {"success": False, "message": frame_error}

        info = getattr(map_msg, "info", None)
        if not info:
            return {"success": False, "message": "OccupancyGrid info가 올바르지 않습니다."}

        width, height = getattr(info, "width", 0), getattr(info, "height", 0)
        res = getattr(info, "resolution", 0.0)
        data_raw = getattr(map_msg, "data", [])

        if width <= 0 or height <= 0 or res <= 0.0 or len(data_raw) != width * height:
            return {
                "success": False,
                "message": (
                    f"비정상적인 OccupancyGrid 데이터: {width}x{height} (res={res}), "
                    f"데이터 길이({len(data_raw)})가 일치하지 않습니다."
                ),
            }

        origin_pos = getattr(getattr(info, "origin", None), "position", None)
        origin_x = getattr(origin_pos, "x", 0.0) if origin_pos else 0.0
        origin_y = getattr(origin_pos, "y", 0.0) if origin_pos else 0.0
        data = np.array(data_raw, dtype=np.int8).reshape((height, width))

        img, crop = build_map_image(data)
        logger.info(
            "Map image cropped to explored area: %s -> %s",
            (width, height),
            (crop.crop_width, crop.crop_height),
        )

        try:
            write_ok = cv2.imwrite(output_path, img)
            if not write_ok:
                return {
                    "success": False,
                    "message": f"맵 이미지 파일 저장 실패: {output_path}",
                }
            encode_ok, buffer = cv2.imencode(".png", img)
            if not encode_ok or buffer is None:
                return {
                    "success": False,
                    "message": "맵 이미지 인코딩 실패",
                }
            img_base64 = base64.b64encode(buffer).decode("utf-8")
            logger.info("Map capture complete: %s", output_path)
        except Exception as e:
            logger.error("Image processing failed: %s", e)
            return {"success": False, "message": f"이미지 생성 중 오류 발생: {e}"}

        crop_origin_x = origin_x + crop.col_offset * res
        crop_origin_y = origin_y + crop.row_offset * res

        return {
            "success": True,
            "message": "맵 이미지를 캡처했습니다.",
            "image_base64": img_base64,
            "file_path": output_path,
            "map_metadata": {
                "width": width,
                "height": height,
                "resolution": info.resolution,
                "origin": {"x": info.origin.position.x, "y": info.origin.position.y},
                "crop": {
                    "origin": {"x": crop_origin_x, "y": crop_origin_y},
                    "width": crop.crop_width,
                    "height": crop.crop_height,
                    "scale": crop.scale,
                },
            },
        }
