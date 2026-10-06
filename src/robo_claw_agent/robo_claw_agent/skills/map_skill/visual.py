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


class GetMapVisualSkill(BaseSkill):
    """맵 데이터를 시각화하여 이미지로 변환 및 VLM 의미 분석을 수행하는 핵심 도구"""

    name = "get_map_visual"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "map_topic": {"type": "string", "default": "/map"},
            "output_path": {"type": "string"},
            "analyze": {"type": "boolean", "default": True},
        },
        "additionalProperties": False,
    }
    description = (
        "현재 맵(OccupancyGrid)을 기반으로 고해상도 시각 이미지를 생성하고, "
        "VLM 분석을 통해 '이동 가능한 안전한 장소', '막혀 있는 문', '복도의 끝' 등을 의미론적으로 판단합니다. "
        "사용자가 공간 분석이나 경로의 안전성을 물을 때 반드시(MUST) 이 도구를 먼저 실행하여 시각 데이터를 확보하세요. "
        "단순한 수치 확인(analyze_map)보다 훨씬 정교한 판단이 가능합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        map_topic = params.get("map_topic", "/map")

        # 유니크한 파일명 생성
        timestamp = int(time.time())
        default_path = f"/tmp/map_{timestamp}.png"
        output_path = params.get("output_path", default_path)
        should_analyze = params.get("analyze", True)

        logger.info("Waiting to receive map visualization data: topic=%s (10s)", map_topic)

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
                "message": f"맵 데이터를 수신할 수 없습니다. (topic={map_topic}). 맵이 현재 발행 중인지 'ros2 topic echo {map_topic}' 명령으로 확인하세요.",
            }
        frame_ok, frame_error = self.validate_message_frame(
            map_msg, getattr(self.node, "_map_frame", "map"), "OccupancyGrid"
        )
        if not frame_ok:
            return {"success": False, "message": frame_error}

        info = map_msg.info
        width, height = info.width, info.height
        data = np.array(map_msg.data, dtype=np.int8).reshape((height, width))

        img, crop = build_map_image(data)
        logger.info(
            "Map image cropped to explored area: %s -> %s",
            (width, height),
            (crop.crop_width, crop.crop_height),
        )

        try:
            cv2.imwrite(output_path, img)

            _, buffer = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            img_base64 = base64.b64encode(buffer).decode("utf-8")

            logger.info("Map visualization complete (JPG encoding): %s", output_path)
        except Exception as e:
            logger.error("Image processing failed: %s", e)
            return {"success": False, "message": f"이미지 생성 중 오류 발생: {e}"}

        # VLM 분석 수행
        analysis_text = ""
        vlm_success = True
        if should_analyze and self.node and hasattr(self.node, "_llm") and self.node._llm:
            try:
                logger.info("Requesting VLM map analysis...")
                prompt = (
                    "이 이미지는 로봇의 2D 점유 격자 지도(Occupancy Grid Map)입니다.\n"
                    "- 흰색(255): 이동 가능한 개방 통로(Free Space)\n"
                    "- 검은색(0): 장애물 및 벽(Occupied Space - 이동 불가)\n"
                    "- 회색(128): 미탐사/미확인 구역(Unknown Space - 진입/이동 절대 불가)\n\n"
                    "주의: 회색(미탐사 영역)과 검은색(장애물)은 모두 로봇이 진입하거나 이동할 수 없는 구역입니다. "
                    "오직 밝은 흰색 영역만을 로봇 이동 가능 공간으로 고려하여 환경의 구조와 특징을 설명해줘."
                )
                analysis_text = self.node._llm.analyze_image(prompt, img_base64)
                logger.info("VLM map analysis complete")
            except Exception as e:
                logger.warning("VLM map analysis failed (image was still generated): %s", e)
                analysis_text = f"(시각 분석 실패: {e})"
                vlm_success = False

        result = {
            "success": True,
            "message": f"맵 시각화 완료 (VLM 분석: {'성공' if vlm_success else '실패'})",
            "image_base64": img_base64,
            "file_path": output_path,
            "vlm_status": "success" if vlm_success else "failed",
            "map_metadata": {
                "width": width,
                "height": height,
                "resolution": info.resolution,
                "origin": {"x": info.origin.position.x, "y": info.origin.position.y},
            },
            "interpretation": analysis_text
            or "맵 이미지가 생성되었습니다.  시각적 분석 결과는 없습니다.",
        }

        # RAG 저장 (VLM 분석 결과가 있는 경우만)
        if (
            vlm_success
            and analysis_text
            and self.node
            and hasattr(self.node, "_memory")
            and getattr(self.node, "_enable_rag", False)
        ):
            try:
                pose = self.get_map_pose()
                loc_str = ""
                if pose:
                    loc_str = (
                        f" [위치 x={round(pose['x'], 2)} y={round(pose['y'], 2)} "
                        f"frame={pose['frame']}]"
                    )
                rag_text = f"{loc_str} 맵 시각 분석: {analysis_text[:600]}"
                self.node._memory.add_knowledge(  # type: ignore
                    rag_text,
                    {
                        "type": "map_visual_analysis",
                        "source": "get_map_visual",
                        "map_width": width,
                        "map_height": height,
                    },
                )
                logger.info("[get_map_visual] RAG save complete")
            except Exception as _rag_e:
                logger.debug("[get_map_visual] RAG save failed: %s", _rag_e)

        return result
