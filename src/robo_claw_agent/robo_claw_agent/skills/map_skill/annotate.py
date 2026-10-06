import base64
import logging
import time
from typing import Any

import cv2
import numpy as np
from nav_msgs.msg import OccupancyGrid  # type: ignore[import]

from robo_claw_agent.agent_node.utils import extract_objects
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills._draw_text import draw_label

from ._grid_image import CropInfo, build_map_image, grid_to_image_xy, nearest_free_cell

logger = logging.getLogger(__name__)


class AnnotateMapSkill(BaseSkill):
    """전역 맵(/map) 이미지에 특정 장소나 좌표를 마킹합니다."""

    name = "annotate_map"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "map_topic": {"type": "string", "default": "/map"},
            "markers": {"type": "array", "items": {"type": "object"}},
            "prompt": {"type": "string"},
            "output_path": {"type": "string"},
        },
        "additionalProperties": False,
    }
    description = "전역 맵 이미지에 특정 장소나 좌표를 마킹합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        map_topic = params.get("map_topic", "/map")
        markers = params.get("markers", [])  # [{'name': str, 'x': float, 'y': float}]
        prompt = params.get(
            "prompt",
            "이 지도를 분석하여 로봇이 이동 가능한 주요 장소(예: 방, 복도 끝, 넓은 공간)들을 찾아줘.",
        )

        logger.info(
            "Starting map annotation skill: topic=%s, markers_count=%d", map_topic, len(markers)
        )

        # 맵 메시지 수신 및 이미지 변환
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
                "message": f"맵 데이터를 수신하지 못했습니다. (topic={map_topic})",
            }
        frame_ok, frame_error = self.validate_message_frame(
            map_msg, getattr(self.node, "_map_frame", "map"), "OccupancyGrid"
        )
        if not frame_ok:
            return {"success": False, "message": frame_error}

        info = map_msg.info
        width, height = info.width, info.height
        res = info.resolution
        origin_x = info.origin.position.x
        origin_y = info.origin.position.y
        data = np.array(map_msg.data, dtype=np.int8).reshape((height, width))
        free_mask = data == 0

        img, crop = build_map_image(data)
        img_bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR).copy()

        detected_places = []
        analysis_text = ""

        img_h, img_w, _ = img_bgr.shape

        if markers:
            logger.info("Performing marking using the provided markers")
            for m in markers:
                try:
                    # LLM이 'label' 또는 'name' 키로 장소명을 전달할 수 있다.
                    # (실제 로그에서 'label': '충전대'로 전달됐으나 name만 읽어
                    #  'Point'로 저장되는 버그가 있었음)
                    name = str(m.get("name") or m.get("label") or "Point")
                    wx = float(m.get("x", 0.0))
                    wy = float(m.get("y", 0.0))

                    row = (wy - origin_y) / res
                    col = (wx - origin_x) / res
                    cx, cy = grid_to_image_xy(row, col, crop)

                    if 0 <= cx < img_w and 0 <= cy < img_h:
                        self._draw_marker(img_bgr, cx, cy, name)
                        detected_places.append({"name": name, "world_x": wx, "world_y": wy})
                    else:
                        logger.warning(
                            "Coordinate out of map bounds: %s (x=%f, y=%f, cx=%d, cy=%d)",
                            name,
                            wx,
                            wy,
                            cx,
                            cy,
                        )
                except Exception as e:
                    logger.error("Error while processing marker: %s", e)
            analysis_text = f"Marked {len(detected_places)} points on the map."
        else:
            logger.info("No markers provided, performing VLM analysis.")
            try:
                _, buffer = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
                img_base64 = base64.b64encode(buffer).decode("utf-8")

                if self.node and hasattr(self.node, "_llm") and self.node._llm:
                    vlm_prompt = (
                        f"{prompt}\n\n"
                        + "### 이미지 색상 및 이동 지침\n"
                        + "- 흰색: 이동 가능한 개방 통로(Free Space)\n"
                        + "- 검은색: 이동 불가능한 장애물/벽(Occupied Space)\n"
                        + "- 회색: 이동 불가능한 미탐사/미확인 구역(Unknown Space - 진입 절대 금지)\n"
                        + "주의: 회색(미탐사 영역)과 검은색(장애물)은 모두 이동이 불가능한 위험/불가 영역입니다. "
                        + "반드시 오직 밝은 흰색 영역 내부에서만 안전한 좌표를 선택하세요. 회색/검정 영역이나 이미지 가장자리는 절대 선택하지 마세요.\n\n"
                        + "### 중요: 응답 형식 지침\n"
                        + "지도를 분석하고 이동 가능한 좌표들을 JSON 형식으로 추출하세요.\n"
                        + "응답 끝에 반드시 아래 JSON 블록 형식을 포함하세요.\n"
                        + "주의: 예시의 '...' 등 생략 기호를 포함하지 말고 실제 데이터만 유효한 JSON으로 작성하세요.\n\n"
                        + 'OBJECTS: [{"name": "Place1", "x_rel": 0.352, "y_rel": 0.418}]\n'
                        + "(x_rel, y_rel은 0.0~1.0 사이의 정규화된 값이며, 이미지의 가로세로 기준 상대적 위치 비율입니다. "
                        + "소수점 3자리 이상의 매우 세밀하고 정밀한 실수 값으로 추출하여 이동 오차를 최소화하세요.)"
                    )
                    analysis_text = self.node._llm.analyze_image(vlm_prompt, img_base64)
                    detected_places = self._parse_vlm_objects(
                        analysis_text, img_bgr, info, free_mask, crop
                    )
            except Exception as e:
                logger.error("Error during VLM analysis: %s", e)
                analysis_text = f"VLM analysis error: {e}"

        # 결과 이미지 저장
        timestamp = int(time.time())
        default_path = f"/tmp/annotated_map_{timestamp}.png"
        save_path = params.get("output_path", default_path)
        try:
            cv2.imwrite(save_path, img_bgr)
            logger.info("Annotated map saved: %s", save_path)
        except Exception as e:
            logger.error("Failed to save image: %s", e)
            return {"success": False, "message": f"Image save error: {e}"}

        place_names = [p["name"] for p in detected_places]
        rag_failures: list[str] = []

        if self.node and hasattr(self.node, "_memory"):
            memory = self.node._memory
            for index, place in enumerate(detected_places, start=1):
                place_name = str(place.get("name") or f"Place{index}")
                world_x = float(place["world_x"])
                world_y = float(place["world_y"])
                memory.add_object_location(
                    place_name,
                    world_x,
                    world_y,
                    metadata={
                        "source": "annotate_map",
                        "kind": "place",
                        "selection_index": index,
                    },
                    aliases=[
                        str(index),
                        f"Place {index}",
                        f"Location {index}",
                        f"Point {index}",
                    ],
                )
                # RAG 지식 베이스에도 함께 저장해, 이후 get_location/rag_search로
                # 이름→좌표 조회가 가능하도록 한다. (시맨틱 맵만 저장하면 RAG 검색으로
                # 좌표를 찾을 수 없어 "기억된 좌표를 불러와 답하지 못하는" 문제가 발생)
                if hasattr(memory, "add_knowledge"):
                    try:
                        stored = memory.add_knowledge(
                            f"{place_name}의 위치는 x: {world_x:.2f}, y: {world_y:.2f} 입니다.",
                            {
                                "location_name": place_name,
                                "x": world_x,
                                "y": world_y,
                                "type": "location",
                                "source": "annotate_map",
                            },
                        )
                        if not stored:
                            rag_failures.append(place_name)
                            logger.warning(
                                "RAG storage failed for marked location '%s' "
                                "(semantic map updated only)",
                                place_name,
                            )
                    except Exception as e:
                        rag_failures.append(place_name)
                        logger.warning(
                            "Failed to store marked location '%s' to RAG: %s",
                            place_name,
                            e,
                        )

        message = f"맵 마킹 완료. 표시된 장소: {', '.join(place_names)}"
        if rag_failures:
            message += (
                f" (주의: {', '.join(rag_failures)}은(는) RAG 지식에 저장되지 않아 "
                "rag_search로는 조회되지 않을 수 있습니다)"
            )

        return {
            "success": True,
            "message": message if place_names else "표시된 장소가 없습니다.",
            "analysis": analysis_text,
            "detected_places": detected_places,
            "file_path": save_path,
            "rag_storage_failures": rag_failures,
        }

    def _draw_marker(self, img: np.ndarray, cx: int, cy: int, name: str):
        img_h, img_w, _ = img.shape
        cv2.circle(img, (cx, cy), 12, (0, 0, 255), -1)
        cv2.circle(img, (cx, cy), 12, (255, 255, 255), 2)

        # 한글 장소명도 깨지지 않도록 PIL 기반 draw_label 사용
        font_size = 18
        tx = min(cx + 15, img_w - 120)
        ty = max(0, cy - font_size // 2)
        draw_label(
            img,
            name,
            tx,
            ty,
            font_size=font_size,
            text_color=(255, 255, 255),
            bg_color=(0, 0, 0),
        )

    def _parse_vlm_objects(
        self,
        text: str,
        img: np.ndarray,
        info: Any,
        free_mask: np.ndarray,
        crop: CropInfo,
        snap_radius: int = 10,
    ) -> list:
        detected = []
        if not text:
            return detected

        try:
            objects = extract_objects(text)
            img_h, img_w, _ = img.shape

            for obj in objects:
                if not isinstance(obj, dict):
                    continue
                name = str(obj.get("name") or obj.get("label") or "Point")

                # x_rel/y_rel 우선. 폴백 x/y는 0~1 정규화 범위일 때만 인정(픽셀좌표 오인 방지).
                x_rel = self._norm_coord(obj.get("x_rel"), obj.get("x"))
                y_rel = self._norm_coord(obj.get("y_rel"), obj.get("y"))
                if x_rel is None or y_rel is None:
                    logger.warning(
                        "Skipping due to missing/out-of-range normalized coordinate: %s", name
                    )
                    continue

                cx = int(x_rel * (img_w - 1))
                cy = int(y_rel * (img_h - 1))

                # 이미지 정규화 좌표(크롭된 이미지 기준) → 원본(전체) grid 연속 좌표(상하 반전 보정) → map 월드 좌표.
                # x_rel/y_rel은 종횡비 보존 리사이즈 기준이라 scale 보정은 불필요.
                orig_col = x_rel * crop.crop_width + crop.col_offset
                orig_row = (1.0 - y_rel) * crop.crop_height + crop.row_offset
                wx = info.origin.position.x + orig_col * info.resolution
                wy = info.origin.position.y + orig_row * info.resolution

                row = max(0, min(int(orig_row), free_mask.shape[0] - 1))
                col = max(0, min(int(orig_col), free_mask.shape[1] - 1))

                # VLM이 이동 불가(장애물/미탐사) 영역을 짚었다면 실제 occupancy grid로 검증해
                # 근처 free 셀로 보정하거나, 근처에도 없으면 좌표를 폐기한다.
                snapped = False
                if not free_mask[row, col]:
                    snap = nearest_free_cell(free_mask, row, col, radius=snap_radius)
                    if snap is None:
                        logger.warning(
                            "Discarding VLM point in unreachable area (no free cell within "
                            "%d cells): %s (row=%d, col=%d)",
                            snap_radius,
                            name,
                            row,
                            col,
                        )
                        continue
                    row, col = snap
                    wx = info.origin.position.x + (col + 0.5) * info.resolution
                    wy = info.origin.position.y + (row + 0.5) * info.resolution
                    snapped = True
                    cx, cy = grid_to_image_xy(row, col, crop)
                    logger.info(
                        "Snapped VLM point to nearest free cell: %s -> (row=%d, col=%d)",
                        name,
                        row,
                        col,
                    )

                self._draw_marker(img, cx, cy, name)
                detected.append({"name": name, "world_x": wx, "world_y": wy, "snapped": snapped})
        except Exception as e:
            logger.error("Error while parsing VLM output: %s", e)

        return detected

    @staticmethod
    def _norm_coord(rel_val: Any, raw_val: Any) -> "float | None":
        """정규화 좌표(0~1) 해석. rel_val 우선, 없으면 0~1 범위의 raw_val만 인정."""
        if rel_val is not None:
            try:
                return max(0.0, min(float(rel_val), 1.0))
            except (TypeError, ValueError):
                return None
        if raw_val is not None:
            try:
                v = float(raw_val)
            except (TypeError, ValueError):
                return None
            if 0.0 <= v <= 1.0:
                return v
        return None
