import logging
import math
from typing import Any

from sensor_msgs.msg import LaserScan

from robo_claw_agent.skill_manager import BaseSkill

from . import globals
from .core import (
    _depth_point_for_pixel,
    _distance_at_bearing,
    _fetch_depth_frame,
    _resolve_class_name,
    _select_candidate,
    _target_pose_metadata,
    target_class_variants,
)

if globals._VISION_MSGS_AVAILABLE:
    from vision_msgs.msg import Detection2DArray

logger = logging.getLogger(__name__)


def _is_gripper_camera(params: dict[str, Any]) -> bool:
    camera_source = str(params.get("camera") or params.get("camera_source") or "").strip().lower()
    return camera_source in {"gripper", "wrist", "hand", "end_effector", "eoa"}


def _default_detections_topic(params: dict[str, Any]) -> str:
    if _is_gripper_camera(params):
        return "/gripper_object_detector_node/detections"
    return "/object_detector_node/detections"


class DetectObjectSkill(BaseSkill):
    """카메라 기반 객체 탐지 스킬 (현재는 데이터 수신 확인)"""

    name = "detect_object"
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
    description = "카메라 이미지를 수신하여 물체 탐지 가능 여부를 확인합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        cv_img, camera_topic, is_compressed = self.get_opencv_image(
            params,
            camera_topic_param_name="camera_topic",
            default_compressed_topic="/camera/color/image_raw/compressed",
            default_raw_topic="/camera/color/image_raw",
            timeout_sec=10.0,
        )
        if cv_img is None:
            return {
                "success": False,
                "message": f"카메라 토픽({camera_topic}) 수신 실패",
            }

        height, width, _ = cv_img.shape
        return {
            "success": True,
            "message": f"이미지 수신 성공 ({width}x{height})",
            "resolution": f"{width}x{height}",
            "encoding": "compressed_bgr8" if is_compressed else "bgr8",
        }


class GetDetectionsSkill(BaseSkill):
    """ONNX 객체 인식 노드(object_detector_node)의 Detection2DArray를 수신"""

    name = "get_detections"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "detections_topic": {"type": "string"},
            "timeout_sec": {"type": "number", "default": 5.0},
            "camera": {"type": "string", "enum": ["base", "gripper"]},
            "camera_source": {"type": "string"},
        },
        "additionalProperties": True,
    }
    description = (
        "object_detector_node가 발행하는 실시간 ONNX 객체 인식 결과를 수신합니다. "
        "반환값: detections 리스트 (class_id, score, bbox_center_x/y, size_x/y). "
        "use_vision:=true 로 시스템이 기동된 경우에만 동작합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if not globals._VISION_MSGS_AVAILABLE:
            return {
                "success": False,
                "message": "vision_msgs 패키지가 설치되지 않았습니다. ros-humble-vision-msgs 를 설치하세요.",
            }

        detections_topic = self.get_string_param(
            params, "detections_topic", _default_detections_topic(params)
        )
        timeout_sec = float(params.get("timeout_sec", 5.0))

        msg = self.wait_for_message(
            Detection2DArray,
            detections_topic,
            timeout_sec=timeout_sec,
            max_age_sec=min(timeout_sec, 2.0),
        )
        if not msg:
            return {
                "success": False,
                "message": (
                    f"Detection2DArray 수신 실패 ({detections_topic}). "
                    "use_vision:=true 로 launch 했는지 확인하세요."
                ),
            }

        results = []
        for det in msg.detections:
            entry = {
                "bbox_center_x": det.bbox.center.position.x,
                "bbox_center_y": det.bbox.center.position.y,
                "size_x": det.bbox.size_x,
                "size_y": det.bbox.size_y,
                "results": [
                    {
                        "class_id": r.hypothesis.class_id,
                        "score": round(r.hypothesis.score, 4),
                    }
                    for r in det.results
                ],
            }
            results.append(entry)

        return {
            "success": True,
            "message": f"인식된 객체 수: {len(results)}",
            "camera": "gripper" if _is_gripper_camera(params) else "base",
            "detections_topic": detections_topic,
            "detections": results,
        }


class FindObjectSkill(BaseSkill):
    """ONNX 인식 결과로 특정 물체를 탐지하고 라이다/오도메트리로 위치를 추정합니다."""

    name = "find_object"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string"},
            "selection": {"type": "string", "enum": ["score", "nearest"], "default": "score"},
            "camera": {"type": "string", "enum": ["base", "gripper"]},
            "camera_source": {"type": "string"},
            "timeout_sec": {"type": "number"},
            "h_fov_deg": {"type": "number"},
            "image_width": {"type": "number"},
            "min_score": {"type": "number"},
            "register_to_map": {"type": "boolean"},
            "lidar_topic": {"type": "string"},
        },
        "required": ["target_object"],
        "additionalProperties": True,
    }
    description = (
        "ONNX 객체 인식으로 특정 물체를 탐지하고 라이다/오도메트리로 위치를 추정합니다. "
        "target_object: COCO 클래스명(예: cup, person, bottle). "
        "동일 클래스 물체가 여러 개 감지되면 selection 파라미터로 선택 기준을 지정할 수 "
        "있습니다: 'score'(기본, 인식 신뢰도가 가장 높은 후보) 또는 'nearest'(로봇에서 "
        "가장 가까운 후보 — depth 있으면 depth, 없으면 LiDAR bearing 거리 사용). "
        "결과의 candidate_count/candidates 필드로 몇 개의 후보가 매칭됐는지 확인할 수 있습니다. "
        "use_vision:=true 로 시스템이 기동된 경우에만 동작합니다."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if not globals._VISION_MSGS_AVAILABLE:
            return {
                "success": False,
                "message": "vision_msgs 패키지가 설치되지 않았습니다. ros-humble-vision-msgs 를 설치하세요.",
            }

        target = self.get_string_param(params, "target_object", "").strip().lower()
        if not target:
            return {"success": False, "message": "target_object 파라미터가 필요합니다."}
        target_variants = target_class_variants(target)

        detections_topic = self.get_string_param(
            params, "detections_topic", _default_detections_topic(params)
        )
        lidar_topic = self.get_string_param(params, "lidar_topic", "/scan")
        h_fov_rad = math.radians(float(params.get("h_fov_deg", 60.0)))
        image_width = float(params.get("image_width", 640))
        # object_detector_node는 0.25 이상을 publish한다. 여기서 0.4로 다시
        # 올리면 실기에서 낮은 confidence의 작은/투명 물체 후보를 버리게 된다.
        min_score = float(params.get("min_score", 0.25))
        timeout_sec = float(params.get("timeout_sec", 10.0))
        register_to_map = bool(params.get("register_to_map", True))
        selection = self.get_string_param(params, "selection", "score").strip().lower()

        msg = self.wait_for_message(
            Detection2DArray,
            detections_topic,
            timeout_sec=timeout_sec,
            max_age_sec=min(timeout_sec, 2.0),
        )
        if not msg:
            return {
                "success": False,
                "message": (
                    f"Detection2DArray 수신 실패 ({detections_topic}). "
                    "use_vision:=true 로 launch 했는지 확인하세요."
                ),
            }

        # 1) 임계값을 넘는 모든 매칭 후보를 먼저 모은다 (점수 최댓값만 보는 게 아니라
        # 다중 후보 disambiguation을 위해 필요).
        matches: list[dict[str, Any]] = []
        for det in msg.detections:
            for r in det.results:
                class_name = _resolve_class_name(r.hypothesis.class_id)
                score = r.hypothesis.score
                if (
                    any(alias in class_name.lower() for alias in target_variants)
                    and score >= min_score
                ):
                    matches.append(
                        {
                            "det": det,
                            "class_name": class_name,
                            "score": score,
                            "cx": det.bbox.center.position.x,
                            "cy": det.bbox.center.position.y,
                        }
                    )

        if not matches:
            # COCO 어휘 밖 물체(예: 물통)는 YOLO가 절대 못 잡는다. 이 경우에만
            # VLM 오픈어휘 로컬라이즈를 폴백으로 시도해 3D 위치까지 계산한다.
            vlm_result = self._find_object_vlm_fallback(
                params,
                target,
                detections_topic,
                lidar_topic,
                h_fov_rad,
                image_width,
                register_to_map,
            )
            if vlm_result is not None:
                return vlm_result
            return {
                "success": False,
                "message": f"'{target}' 미탐지 (min_score={min_score}, topic={detections_topic})",
            }

        # 2) depth/LiDAR은 후보 수와 무관하게 한 번만 구독해 모든 후보에 재사용한다.
        depth_frame = _fetch_depth_frame(self, params)
        scan = self.wait_for_message(LaserScan, lidar_topic, timeout_sec=2.0)

        for m in matches:
            bearing_offset = (m["cx"] / image_width - 0.5) * h_fov_rad
            depth_point_map, depth_distance_m = _depth_point_for_pixel(
                self, depth_frame, params, m["cx"], m["cy"]
            )
            lidar_distance_m = _distance_at_bearing(scan, bearing_offset) if scan else None
            m["bearing_offset"] = bearing_offset
            m["depth_point_map"] = depth_point_map
            m["depth_distance_m"] = depth_distance_m
            m["lidar_distance_m"] = lidar_distance_m
            # 선택 기준 거리: depth(정밀)가 있으면 depth, 없으면 LiDAR bearing으로 폴백.
            m["distance_m"] = depth_distance_m if depth_distance_m is not None else lidar_distance_m

        # 3) selection 전략에 따라 최종 후보 하나를 확정한다.
        best = _select_candidate(matches, selection=selection)
        best_class = best["class_name"]
        best_score = best["score"]
        cx = best["cx"]
        cy = best["cy"]
        bearing_offset = best["bearing_offset"]
        depth_point_map = best["depth_point_map"]
        depth_distance_m = best["depth_distance_m"]
        distance_m = best["lidar_distance_m"]

        pose = self.get_map_pose()

        world_x: float | None = None
        world_y: float | None = None
        registered = False
        position_source = ""

        # 감지된 물체의 (x, y)는 두 가지 방식으로 추정될 수 있다:
        #  1) depth_point_map: 깊이 카메라 + TF (정밀, map 프레임 3D)
        #  2) LiDAR bearing + 로봇 map pose의 극좌표 합산 (deproject 실패 시 대체용)
        # 서로 다른 센서/계산이므로 둘 다 있으면 정밀한 depth 쪽을 대표 좌표로 쓰고,
        # 두 추정치가 크게 벌어지면(>0.5m) 로그로 남겨 캘리브레이션/TF 오류를 드러낸다.
        if pose and distance_m is not None:
            world_bearing = pose["yaw"] + bearing_offset
            world_x = pose["x"] + distance_m * math.cos(world_bearing)
            world_y = pose["y"] + distance_m * math.sin(world_bearing)
            position_source = "lidar_bearing"

        if depth_point_map is not None:
            if world_x is not None and world_y is not None:
                disagreement_m = math.hypot(
                    depth_point_map[0] - world_x, depth_point_map[1] - world_y
                )
                if disagreement_m > 0.5:
                    logger.warning(
                        "[FindObjectSkill] depth(map) and LiDAR-bearing estimates differ by %.2fm "
                        "(depth=(%.2f,%.2f), lidar=(%.2f,%.2f)) — using depth value.",
                        disagreement_m,
                        depth_point_map[0],
                        depth_point_map[1],
                        world_x,
                        world_y,
                    )
            world_x, world_y = depth_point_map[0], depth_point_map[1]
            position_source = "depth_tf"

        distance_source = (
            "depth" if depth_distance_m is not None else ("lidar" if distance_m is not None else "")
        )
        final_distance_m = depth_distance_m if depth_distance_m is not None else distance_m

        if position_source and register_to_map:
            if not (self.node and hasattr(self.node, "_memory")):
                logger.warning("[FindObjectSkill] Memory not accessible, skipping map registration")
            else:
                try:
                    target_frame_id = (
                        "map"
                        if position_source == "depth_tf"
                        else (pose["frame"] if pose else "map")
                    )
                    metadata: dict[str, Any] = {
                        "score": round(best_score, 4),
                        "bbox_cx": cx,
                        "bbox_cy": cy,
                        "source": "find_object",
                        "kind": "object",
                        "camera": "gripper" if _is_gripper_camera(params) else "base",
                        "position_source": position_source,
                        "distance_source": distance_source,
                        "frame_id": target_frame_id,
                    }
                    if final_distance_m is not None:
                        metadata["distance_m"] = round(final_distance_m, 3)
                    if depth_point_map is not None:
                        metadata["target_pose"] = _target_pose_metadata(self, depth_point_map)
                    self.node._memory.add_object_location(
                        best_class,
                        world_x,
                        world_y,
                        metadata=metadata,
                        aliases=[target],
                    )
                    registered = True
                except Exception as exc:
                    logger.warning("Semantic map registration failed: %s", exc)

        result: dict[str, Any] = {
            "success": True,
            "message": f"'{best_class}' 탐지 완료 (score={best_score:.2f})",
            "class_name": best_class,
            "camera": "gripper" if _is_gripper_camera(params) else "base",
            "detections_topic": detections_topic,
            "score": round(best_score, 4),
            "bearing_offset_deg": round(math.degrees(bearing_offset), 1),
        }
        if final_distance_m is not None:
            result["distance_m"] = round(final_distance_m, 3)
            result["distance_source"] = distance_source
        if world_x is not None and world_y is not None:
            result["world_x"] = round(world_x, 3)
            result["world_y"] = round(world_y, 3)
            result["position_source"] = position_source
        result["depth_available"] = depth_point_map is not None
        if depth_distance_m is not None:
            result["depth_distance_m"] = round(depth_distance_m, 3)
        if depth_point_map is not None:
            result["target_pose"] = _target_pose_metadata(self, depth_point_map)
        result["registered_to_map"] = registered

        result["candidate_count"] = len(matches)
        result["selection"] = selection
        if len(matches) > 1:
            result["candidates"] = [
                {
                    "score": round(c["score"], 4),
                    "distance_m": (
                        round(c["distance_m"], 3) if c["distance_m"] is not None else None
                    ),
                }
                for c in sorted(matches, key=lambda c: -c["score"])[:5]
            ]
        return result

    def _find_object_vlm_fallback(
        self,
        params: dict[str, Any],
        target: str,
        detections_topic: str,
        lidar_topic: str,
        h_fov_rad: float,
        image_width: float,
        register_to_map: bool,
    ) -> dict[str, Any] | None:
        """VLM 오픈어휘 폴백: 대상의 픽셀 중심을 얻어 3D 위치까지 계산한다.

        YOLO(COCO 80클래스)가 못 잡는 물체(물통 등)를 대상으로, 기존 depth/LiDAR
        파이프라인에 그대로 연결해 map 3D 좌표를 산출한다. 실패/비활성 시 None.
        """
        from robo_claw_agent.skills.perception_skill.vlm_localize import (
            _use_vlm_fallback,
            _vlm_object_center_px,
        )

        if not _use_vlm_fallback(self, params, target):
            return None

        center = _vlm_object_center_px(self, params, target)
        if center is None:
            return None
        cx, cy, vlm_class = center

        depth_frame = _fetch_depth_frame(self, params)
        scan = self.wait_for_message(LaserScan, lidar_topic, timeout_sec=2.0)
        bearing_offset = (cx / image_width - 0.5) * h_fov_rad
        depth_point_map, depth_distance_m = _depth_point_for_pixel(
            self, depth_frame, params, cx, cy
        )
        lidar_distance_m = _distance_at_bearing(scan, bearing_offset) if scan else None
        distance_m = depth_distance_m if depth_distance_m is not None else lidar_distance_m

        pose = self.get_map_pose()
        world_x: float | None = None
        world_y: float | None = None
        position_source = ""
        if pose and distance_m is not None:
            world_bearing = pose["yaw"] + bearing_offset
            world_x = pose["x"] + distance_m * math.cos(world_bearing)
            world_y = pose["y"] + distance_m * math.sin(world_bearing)
            position_source = "lidar_bearing"
        if depth_point_map is not None:
            world_x, world_y = depth_point_map[0], depth_point_map[1]
            position_source = "depth_tf"

        distance_source = (
            "depth" if depth_distance_m is not None else ("lidar" if distance_m is not None else "")
        )
        final_distance_m = depth_distance_m if depth_distance_m is not None else distance_m

        registered = False
        if position_source and register_to_map:
            if not (self.node and hasattr(self.node, "_memory")):
                logger.warning("[FindObjectSkill] Memory not accessible, skipping map registration")
            else:
                try:
                    target_frame_id = (
                        "map"
                        if position_source == "depth_tf"
                        else (pose["frame"] if pose else "map")
                    )
                    metadata: dict[str, Any] = {
                        "score": 0.0,
                        "bbox_cx": cx,
                        "bbox_cy": cy,
                        "source": "find_object_vlm",
                        "kind": "object",
                        "camera": "gripper" if _is_gripper_camera(params) else "base",
                        "position_source": position_source,
                        "distance_source": distance_source,
                        "frame_id": target_frame_id,
                        "localize": "vlm",
                    }
                    if final_distance_m is not None:
                        metadata["distance_m"] = round(final_distance_m, 3)
                    if depth_point_map is not None:
                        metadata["target_pose"] = _target_pose_metadata(self, depth_point_map)
                    self.node._memory.add_object_location(
                        vlm_class,
                        world_x,
                        world_y,
                        metadata=metadata,
                        aliases=[target],
                    )
                    registered = True
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Semantic map registration failed (vlm): %s", exc)

        result: dict[str, Any] = {
            "success": True,
            "message": f"VLM 오픈어휘 탐지 '{vlm_class}' (score=0.0)",
            "class_name": vlm_class,
            "camera": "gripper" if _is_gripper_camera(params) else "base",
            "detections_topic": detections_topic,
            "score": 0.0,
            "bearing_offset_deg": round(math.degrees(bearing_offset), 1),
            "localize": "vlm",
        }
        if final_distance_m is not None:
            result["distance_m"] = round(final_distance_m, 3)
            result["distance_source"] = distance_source
        if world_x is not None and world_y is not None:
            result["world_x"] = round(world_x, 3)
            result["world_y"] = round(world_y, 3)
            result["position_source"] = position_source
        result["depth_available"] = depth_point_map is not None
        if depth_distance_m is not None:
            result["depth_distance_m"] = round(depth_distance_m, 3)
        if depth_point_map is not None:
            result["target_pose"] = _target_pose_metadata(self, depth_point_map)
        result["registered_to_map"] = registered
        result["candidate_count"] = 1
        result["selection"] = "vlm"
        return result


class EstimateGripperObjectPoseSkill(FindObjectSkill):
    """그리퍼 카메라 RGB-D로 물체의 3D pose를 추정하고 메모리에 등록."""

    name = "estimate_gripper_object_pose"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {"target_object": {"type": "string"}, "timeout_sec": {"type": "number"}},
        "required": ["target_object"],
        "additionalProperties": False,
    }
    description = (
        "Stretch3 그리퍼 카메라와 aligned depth를 사용해 target_object의 3D pose를 "
        "추정하고 메모리에 등록합니다. grasp 전에 물체 위치를 손 앞 카메라로 "
        "정밀하게 확인할 때 사용하세요."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        gripper_params = dict(params)
        gripper_params["camera"] = "gripper"
        gripper_params.setdefault("detections_topic", "/gripper_object_detector_node/detections")
        gripper_params.setdefault("min_score", 0.1)
        gripper_params.setdefault("timeout_sec", 6.0)
        gripper_params.setdefault("use_depth", True)
        result = super().execute(gripper_params)
        if result.get("success") and not result.get("target_pose"):
            result["success"] = False
            result["message"] = (
                "그리퍼 카메라로 객체는 감지했지만 depth 기반 3D pose를 계산하지 못했습니다. "
                "gripper_depth_topic/camera_info_topic/TF를 확인하세요."
            )
        return result
