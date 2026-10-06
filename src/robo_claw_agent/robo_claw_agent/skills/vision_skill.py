"""
비전 분석 스킬 — 실전 VLM 연동을 통한 장면 이해 및 객체 등록
"""

import base64
import logging
import time
from typing import Any

import cv2
from cv_bridge import CvBridge

from robo_claw_agent.agent_node.utils import extract_objects
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills._draw_text import draw_label

logger = logging.getLogger(__name__)


class AnalyzeSceneSkill(BaseSkill):
    """실전 VLM(Vision-Language Model) 연동 시각 분석 스킬"""

    name = "analyze_scene"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "camera": {"type": "string", "enum": ["base", "gripper"]},
            "camera_source": {"type": "string"},
            "camera_topic": {"type": "string"},
            "prompt": {"type": "string"},
            "h_fov_deg": {"type": "number"},
            "estimated_distance_m": {"type": "number"},
        },
        "additionalProperties": True,
    }
    description = (
        "카메라 이미지를 VLM으로 분석하여 주변 상황을 설명하고, "
        "인식된 주요 객체들의 위치를 시맨틱 맵에 등록합니다."
    )

    def __init__(self) -> None:
        super().__init__()
        self._bridge = CvBridge()

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        camera_source = str(params.get("camera") or params.get("camera_source") or "base")
        prompt = params.get(
            "prompt",
            "이 장면을 분석하고 보이는 주요 객체들과 그들의 대략적인 위치를 설명해줘.",
        )

        cv_img, camera_topic, is_compressed = self.get_opencv_image(
            params, camera_topic_param_name="camera_topic", timeout_sec=15.0
        )

        if cv_img is None:
            return {"success": False, "message": "카메라 이미지를 수신하지 못했습니다."}

        try:
            timestamp = int(time.time())
            filename = f"scene_{timestamp}.png"
            save_path = f"/tmp/{filename}"
            write_ok = cv2.imwrite(save_path, cv_img)
            if not write_ok:
                logger.warning("Failed to save image locally to %s", save_path)

            encode_ok, buffer = cv2.imencode(".png", cv_img)
            if not encode_ok or buffer is None:
                return {"success": False, "message": "이미지 인코딩 실패"}
            img_base64 = base64.b64encode(buffer).decode("utf-8")
        except Exception as e:
            logger.error("Image processing failed: %s", e)
            return {"success": False, "message": f"이미지 처리 오류: {e}"}

        analysis_text = "분석 실패"
        if self.node and hasattr(self.node, "_llm") and self.node._llm:  # type: ignore
            try:
                vlm_prompt = (
                    f"{prompt}\n\n"
                    + """
장면을 분석한 내용을 한국어로 자세히 설명해줘.
설명 끝에 반드시 인식된 객체들을 다음 JSON 형식으로 포함해줘 (객체명은 영문으로 작성, '...' 등 생략 기호 금지):
OBJECTS: [{"name": "object_name", "x_rel": 0.5, "y_rel": 0.5}]
                    """
                )
                analysis_text = self.node._llm.analyze_image(vlm_prompt, img_base64)  # type: ignore
                logger.info("VLM analysis complete: %s", analysis_text)
            except Exception as e:
                logger.error("Error during VLM analysis: %s", e)
                return {"success": False, "message": f"VLM 분석 오류: {e}"}

        import math

        detected_names = []
        pose = self.get_map_pose()
        if self.node and hasattr(self.node, "_memory") and pose:
            try:
                h_fov = math.radians(float(params.get("h_fov_deg", 60.0)))
                nominal_dist = float(params.get("estimated_distance_m", 1.5))
                rx, ry, ryaw = pose["x"], pose["y"], pose["yaw"]
                for obj in extract_objects(analysis_text):
                    name = obj.get("name") or obj.get("label")
                    if not name:
                        continue
                    # 깊이 정보가 없으므로, 이미지 가로 상대위치(x_rel)를 카메라 FOV 기준
                    # 방위로 환산해 로봇 전방 nominal_dist 거리의 map 좌표로 근사 추정.
                    # bearing 규약은 scan_room과 동일((x_rel-0.5)*FOV, wb=yaw+bearing).
                    x_rel = max(0.0, min(float(obj.get("x_rel", 0.5)), 1.0))
                    wb = ryaw + (x_rel - 0.5) * h_fov
                    world_x = rx + nominal_dist * math.cos(wb)
                    world_y = ry + nominal_dist * math.sin(wb)
                    self.node._memory.add_object_location(  # type: ignore
                        name,
                        world_x,
                        world_y,
                        {
                            "source": "vlm",
                            "kind": "object",
                            "frame_id": pose["frame"],
                            "estimated": True,
                            # depth 정보가 없는 2D 추정치이지만, grasp/place가 object_name으로
                            # 이 위치를 조회할 수 있도록 find_object와 동일한 target_pose 형태로도 기록한다.
                            # (perception_skill/core.py::_target_pose_metadata 와 동일 shape)
                            "target_pose": {
                                "frame_id": pose["frame"],
                                "position": {"x": world_x, "y": world_y, "z": 0.0},
                                "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                            },
                        },
                    )
                    detected_names.append(name)
            except Exception as e:
                logger.warning("Failed to parse/register object info: %s", e)
        elif not pose:
            logger.warning(
                "[analyze_scene] Skipping object registration due to failed robot pose lookup."
            )

        # RAG 자동 저장
        if self.node and hasattr(self.node, "_memory") and hasattr(self.node, "_enable_rag"):
            if getattr(self.node, "_enable_rag", False):
                try:
                    loc_str = ""
                    if pose:
                        loc_str = (
                            f" [위치 x={round(pose['x'], 2)} y={round(pose['y'], 2)} "
                            f"frame={pose['frame']}]"
                        )
                    obj_str = ", ".join(detected_names) if detected_names else ""
                    rag_text = f"{loc_str} 장면 분석: {analysis_text[:500]}"
                    if obj_str:
                        rag_text += f" | 감지 객체: {obj_str}"
                    self.node._memory.add_knowledge(  # type: ignore
                        rag_text,
                        {
                            "type": "scene_observation",
                            "detected_objects": detected_names,
                            "source": "analyze_scene",
                        },
                    )
                    logger.info("[analyze_scene] RAG save complete")
                except Exception as _rag_e:
                    logger.debug("[analyze_scene] RAG save failed: %s", _rag_e)

        return {
            "success": True,
            "message": f"시각 분석 완료. 인식된 객체: {', '.join(detected_names)}",
            "analysis": analysis_text,
            "detected_objects": detected_names,
            "camera": camera_source,
            "camera_topic": camera_topic,
            "file_path": save_path,
        }


class AnnotateImageSkill(BaseSkill):
    """실전 VLM 연동을 통한 카메라 이미지 마킹 스킬"""

    name = "annotate_image"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "camera": {"type": "string", "enum": ["base", "gripper"]},
            "camera_source": {"type": "string"},
            "camera_topic": {"type": "string"},
            "prompt": {"type": "string"},
        },
        "additionalProperties": True,
    }
    description = (
        "카메라 이미지를 분석하여 인식된 객체의 위치에 바운딩 박스와 텍스트를 "
        "그려넣은 이미지를 생성하고 반환합니다."
    )

    def __init__(self) -> None:
        super().__init__()
        self._bridge = CvBridge()

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        camera_source = str(params.get("camera") or params.get("camera_source") or "base")
        prompt = params.get(
            "prompt",
            "이 장면을 분석하고 보이는 주요 객체들을 찾아서 바운딩 박스를 그려줘.",
        )

        cv_img, camera_topic, is_compressed = self.get_opencv_image(
            params, camera_topic_param_name="camera_topic", timeout_sec=15.0
        )

        if cv_img is None:
            return {"success": False, "message": "카메라 이미지를 수신하지 못했습니다."}

        try:
            cv_img = cv_img.copy()

            _, buffer = cv2.imencode(".png", cv_img)
            img_base64 = base64.b64encode(buffer).decode("utf-8")
        except Exception as e:
            logger.error("Image processing failed: %s", e)
            return {"success": False, "message": f"이미지 처리 오류: {e}"}

        analysis_text = "분석 실패"
        if self.node and hasattr(self.node, "_llm") and self.node._llm:  # type: ignore
            try:
                vlm_prompt = (
                    f"{prompt}\n\n"
                    + "### 필수 지시사항 (반드시 지킬 것):\n"
                    + "1. 장면을 분석한 내용을 한국어로 자세히 설명해줘.\n"
                    + "2. 설명이 끝난 후, 반드시 아래와 같이 'OBJECTS:' 키워드로 시작하는 JSON 리스트를 한 줄에 하나씩 포함해줘 (객체명은 영문으로 작성, '...' 등 생략 기호 절대 금지).\n"
                    + 'OBJECTS: [{"name": "object_name", "ymin": 0.0, "xmin": 0.0, "ymax": 1.0, "xmax": 1.0}]\n'
                    + "3. 좌표(ymin, xmin, ymax, xmax)는 이미지 전체 크기 대비 0.0에서 1.0 사이의 정규화된 비율이어야 해."
                )
                analysis_text = self.node._llm.analyze_image(vlm_prompt, img_base64)  # type: ignore
                logger.info("VLM annotation analysis complete")
            except Exception as e:
                logger.error("Error during VLM analysis: %s", e)
                return {"success": False, "message": f"VLM 분석 오류: {e}"}

        detected_names = []
        analysis_text = analysis_text or ""
        try:
            objects = extract_objects(analysis_text)
            height, width, _ = cv_img.shape
            logger.info("Parsed %d recognized objects", len(objects))

            for obj in objects:
                name = obj.get("name") or obj.get("label") or "Unknown"

                ymin = float(obj.get("ymin", 0.0))
                xmin = float(obj.get("xmin", 0.0))
                ymax = float(obj.get("ymax", 1.0))
                xmax = float(obj.get("xmax", 1.0))

                # 0~1 범위 클램프 후 min>max 뒤바뀜 보정
                ymin, ymax = sorted((max(0.0, min(ymin, 1.0)), max(0.0, min(ymax, 1.0))))
                xmin, xmax = sorted((max(0.0, min(xmin, 1.0)), max(0.0, min(xmax, 1.0))))

                y1, x1 = int(ymin * height), int(xmin * width)
                y2, x2 = int(ymax * height), int(xmax * width)

                cv2.rectangle(cv_img, (x1, y1), (x2, y2), (255, 0, 0), 3)

                # 한글 라벨도 깨지지 않도록 PIL 기반 draw_label 사용 (배경 파란 박스)
                label_y = max(0, y1 - 26)
                draw_label(
                    cv_img,
                    name,
                    x1 + 3,
                    label_y,
                    font_size=22,
                    text_color=(255, 255, 255),
                    bg_color=(255, 0, 0),
                )

                detected_names.append(name)

            if not objects:
                logger.warning(
                    "No valid objects found in VLM response. Excerpt: %s",
                    analysis_text[:200],
                )
        except Exception as e:
            logger.warning("Failed to parse or render object information: %s", e)

        # 결과 이미지 저장
        try:
            timestamp = int(time.time())
            filename = f"annotated_scene_{timestamp}.png"
            save_path = f"/tmp/{filename}"
            cv2.imwrite(save_path, cv_img)
            logger.info("Annotation image saved: %s", save_path)
        except Exception as e:
            logger.error("Failed to save annotation image: %s", e)
            return {"success": False, "message": f"Image save error: {e}"}

        return {
            "success": True,
            "message": f"마킹 완료. 인식된 객체: {', '.join(detected_names)}"
            if detected_names
            else "인식된 객체가 없습니다.",
            "analysis": analysis_text,
            "detected_objects": detected_names,
            "camera": camera_source,
            "camera_topic": camera_topic,
            "file_path": save_path,
        }


class CaptureCameraImageSkill(BaseSkill):
    """VLM 분석 없이 카메라 이미지를 빠르게 획득하는 스킬"""

    name = "capture_camera_image"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "camera": {"type": "string", "enum": ["base", "gripper"]},
            "camera_source": {"type": "string"},
            "camera_topic": {"type": "string"},
        },
        "additionalProperties": True,
    }
    description = (
        "카메라 이미지 수집 요청이 있거나 단순히 카메라 화면을 보고해야 할 때 "
        "VLM 분석 없이 이미지를 신속하게 캡처하여 로컬에 저장하고 경로를 반환합니다."
    )

    def __init__(self) -> None:
        super().__init__()
        self._bridge = CvBridge()

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        camera_source = str(params.get("camera") or params.get("camera_source") or "base")
        cv_img, camera_topic, is_compressed = self.get_opencv_image(
            params, camera_topic_param_name="camera_topic", timeout_sec=10.0
        )

        if cv_img is None:
            return {"success": False, "message": "카메라 이미지를 수신하지 못했습니다."}

        try:
            timestamp = int(time.time())
            filename = f"camera_capture_{timestamp}.png"
            save_path = f"/tmp/{filename}"
            cv2.imwrite(save_path, cv_img)
            logger.info("Camera image capture saved: %s", save_path)

            _, buffer = cv2.imencode(".png", cv_img)
            img_base64 = base64.b64encode(buffer).decode("utf-8")
        except Exception as e:
            logger.error("Image processing failed: %s", e)
            return {"success": False, "message": f"이미지 처리 오류: {e}"}

        return {
            "success": True,
            "message": "카메라 이미지를 캡처했습니다.",
            "file_path": save_path,
            "image_base64": img_base64,
            "camera": camera_source,
            "camera_topic": camera_topic,
        }
