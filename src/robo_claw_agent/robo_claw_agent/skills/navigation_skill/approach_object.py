import base64
import json
import logging
import math
import threading
from typing import Any

import cv2
from cv_bridge import CvBridge
from sensor_msgs.msg import LaserScan

from robo_claw_agent.agent_node.utils import _extract_first_json_block
from robo_claw_agent.skill_manager import BaseSkill

from ..perception_skill.core import _compute_depth_point_base, _distance_at_bearing
from .core import (
    _get_nav2_dependency_error,
    _send_navigation_goal,
    _wait_for_goal_result,
)

logger = logging.getLogger(__name__)

# VLM에게 객체 방향각 추정을 요청할 때 사용하는 프롬프트 템플릿
_VLM_BEARING_PROMPT = """
이미지에서 '{target}'를 찾아줘.
찾았으면 해당 객체의 바운딩 박스 중심 좌표를 이미지 전체 크기 대비 0.0~1.0 사이 비율로 알려줘.
- cx: 가로 위치 (0.0 = 왼쪽 끝, 0.5 = 정중앙, 1.0 = 오른쪽 끝)
- cy: 세로 위치 (0.0 = 위쪽 끝, 0.5 = 정중앙, 1.0 = 아래쪽 끝)

반드시 아래 JSON 형식으로만 답해줘:
RESULT: {{"found": true, "cx": 0.5, "cy": 0.5, "label": "shelf"}}
찾지 못했으면: RESULT: {{"found": false}}
"""


class ApproachObjectSkill(BaseSkill):
    """VLM 기반 객체 탐지 + 라이다 거리 측정 → 객체에 접근 이동하는 스킬"""

    name = "approach_object"
    exclusive_resources = ("base_control",)
    description = (
        "카메라 이미지에서 VLM으로 지정 객체를 찾고, 라이다로 해당 방향의 거리를 측정한 뒤 "
        "지정한 정지 거리까지 접근합니다. "
        "depth_topic/camera_info_topic이 설정된 로봇(RealSense 등, config yaml의 "
        "realsense_depth/realsense_camera_info)에서는 깊이 카메라 기반 3D 위치를 우선 "
        "사용해 접근 정밀도를 높이고, LiDAR 방향각 추정치와 0.5m 이상 차이나면 경고 로그를 "
        "남깁니다. depth 미설정 로봇/시뮬레이션에서는 기존 LiDAR 전용 방식으로 동작합니다. "
        "COCO 클래스가 아닌 '선반', '문', '책상' 같은 임의 객체명도 사용 가능합니다. "
        "파라미터: target_object(str, 접근할 객체명, 필수), "
        "stop_distance_m(float, 객체 앞 정지 거리(m), 기본 0.5), "
        "h_fov_deg(float, 카메라 수평 시야각(°), 기본 60.0), "
        "use_depth(bool, 깊이 카메라 사용 여부, 기본 True), "
        "depth_topic(str, 정렬된 depth 이미지 토픽, 미지정 시 노드 파라미터 사용), "
        "camera_info_topic(str, depth 카메라 CameraInfo 토픽, 미지정 시 노드 파라미터 사용), "
        "depth_window_px(int, depth 샘플링 윈도우 크기(px), 기본 5). "
        "예: {'target_object': '선반', 'stop_distance_m': 0.3}"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string", "description": "접근할 객체명"},
            "stop_distance_m": {"type": "number", "default": 0.5},
            "h_fov_deg": {"type": "number", "default": 60.0},
            "use_depth": {"type": "boolean", "default": True},
            "depth_topic": {"type": "string"},
            "camera_info_topic": {"type": "string"},
            "depth_window_px": {"type": "integer", "default": 5},
        },
        "required": ["target_object"],
        "additionalProperties": False,
    }
    side_effects = ("base_motion",)

    def __init__(self) -> None:
        super().__init__()
        self._bridge = CvBridge()

    def _vlm_locate(self, target: str, img_base64: str) -> tuple[float, float] | None:
        """VLM으로 객체의 화면 내 (cx, cy) (각 0~1)를 추출한다. 실패 시 None.

        cy는 depth 역투영에 필요한 세로 좌표다. cy가 응답에 없거나 형식이 잘못된
        경우에도 cx/found 결과 자체는 살리기 위해 cy만 0.5(세로 중앙 가정)로
        대체하고 스킬 전체를 실패시키지 않는다.
        """
        if not (self.node and hasattr(self.node, "_llm") and self.node._llm):
            return None
        prompt = _VLM_BEARING_PROMPT.format(target=target)
        try:
            resp = self.node._llm.analyze_image(prompt, img_base64)
            # RESULT: 키워드 이후의 JSON 블록 추출
            start_idx = resp.find("RESULT:")
            search_text = resp[start_idx + 7 :] if start_idx != -1 else resp
            json_str = _extract_first_json_block(search_text)

            if "{" not in json_str:
                logger.warning("No JSON block found in VLM response: %s", resp[:200])
                return None
            data = json.loads(json_str)
            if not data.get("found", False):
                logger.info("VLM: could not find object '%s' in the image.", target)
                return None
            cx = float(data.get("cx", 0.5))
            try:
                cy = float(data.get("cy", 0.5))
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid cy format in VLM response, falling back to 0.5 (vertical center): %s",
                    data.get("cy"),
                )
                cy = 0.5
            cx = max(0.0, min(1.0, cx))
            cy = max(0.0, min(1.0, cy))
            logger.info("VLM: '%s' position cx=%.3f, cy=%.3f", target, cx, cy)
            return cx, cy
        except Exception as e:
            logger.error("VLM object detection failed: %s", e)
            return None

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        target = str(params.get("target_object") or "").strip()
        if not target:
            return {"success": False, "message": "target_object 파라미터가 필요합니다."}

        stop_distance_m = float(params.get("stop_distance_m", 0.5))
        h_fov_deg = float(params.get("h_fov_deg", 60.0))
        h_fov_rad = math.radians(h_fov_deg)
        lidar_topic = self.get_string_param(params, "lidar_topic", "/scan")

        nav2_error = _get_nav2_dependency_error()
        if nav2_error:
            return {"success": False, "message": nav2_error}

        cv_img, camera_topic, is_compressed = self.get_opencv_image(
            params, camera_topic_param_name="camera_topic", timeout_sec=10.0
        )
        if cv_img is None:
            return {"success": False, "message": f"카메라 이미지 수신 실패 ({camera_topic})"}

        height, width = cv_img.shape[:2]

        try:
            _, buf = cv2.imencode(".jpg", cv_img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            img_base64 = base64.b64encode(buf).decode("utf-8")
        except Exception as e:
            return {"success": False, "message": f"이미지 변환 실패: {e}"}

        located = self._vlm_locate(target, img_base64)
        if located is None:
            return {
                "success": False,
                "message": f"카메라 이미지에서 '{target}'를 찾지 못했습니다.",
            }
        cx, cy = located

        bearing_rad = (cx - 0.5) * h_fov_rad

        scan = self.wait_for_message(LaserScan, lidar_topic, timeout_sec=2.0)
        if not scan:
            return {"success": False, "message": f"라이다 데이터 수신 실패 ({lidar_topic})"}

        measured_dist = _distance_at_bearing(scan, bearing_rad)
        logger.info(
            "'%s' bearing=%.1f°, lidar distance=%s",
            target,
            math.degrees(bearing_rad),
            f"{measured_dist:.2f}m" if measured_dist is not None else "None",
        )

        # depth 카메라 기반 3D 위치(map 프레임). use_depth/depth_topic/camera_info_topic이
        # 없거나 실패하면 ROS 호출 없이 즉시 (None, None)을 반환해 기존 LiDAR 전용 경로로
        # 자동 폴백한다(find_object 스킬과 동일한 파이프라인 재사용).
        pixel_x, pixel_y = cx * width, cy * height
        depth_point_map, depth_distance_m = _compute_depth_point_base(
            self, params, pixel_x, pixel_y
        )
        if depth_point_map is not None:
            logger.info(
                "'%s' depth-based 3D position(map)=(%.2f, %.2f, %.2f), depth=%.2fm",
                target,
                depth_point_map[0],
                depth_point_map[1],
                depth_point_map[2],
                depth_distance_m,
            )

        if measured_dist is None:
            if depth_distance_m is not None:
                measured_dist = depth_distance_m
                logger.info(
                    "LiDAR 측정값 부재로 depth 센서 거리(%.2fm)를 사용합니다.", depth_distance_m
                )
            else:
                return {
                    "success": False,
                    "message": f"'{target}'까지의 거리를 라이다 및 depth 센서로 측정하지 못했습니다.",
                }

        approach_dist = measured_dist - stop_distance_m
        if approach_dist <= 0.05:
            return {
                "success": True,
                "message": (
                    f"'{target}'까지 거리 {measured_dist:.2f}m로 이미 충분히 가깝습니다. "
                    f"(정지 여유: {stop_distance_m}m)"
                ),
                "distance_m": round(measured_dist, 3),
                "bearing_deg": round(math.degrees(bearing_rad), 1),
            }

        pose = self.get_map_pose()
        if not pose:
            return {"success": False, "message": "로봇 위치(map/odom) 조회에 실패했습니다."}

        rx, ry = pose["x"], pose["y"]
        world_bearing = pose["yaw"] + bearing_rad

        # LiDAR-bearing 기반 추정 좌표 (기존 방식, 항상 계산해 둔다)
        lidar_x = rx + measured_dist * math.cos(world_bearing)
        lidar_y = ry + measured_dist * math.sin(world_bearing)

        obj_x, obj_y = lidar_x, lidar_y
        position_source = "lidar_bearing"

        # depth 카메라 기반 3D 포인트가 있으면 대표 좌표로 사용한다. 두 추정치가
        # 0.5m 넘게 차이나면 캘리브레이션/TF 오류 가능성을 로그로 남긴다
        # (find_object 스킬의 재조정 패턴과 동일).
        if depth_point_map is not None:
            disagreement_m = math.hypot(depth_point_map[0] - lidar_x, depth_point_map[1] - lidar_y)
            if disagreement_m > 0.5:
                logger.warning(
                    "[ApproachObjectSkill] depth(map) and LiDAR-bearing estimates differ by %.2fm "
                    "(depth=(%.2f,%.2f), lidar=(%.2f,%.2f)) — using the depth value.",
                    disagreement_m,
                    depth_point_map[0],
                    depth_point_map[1],
                    lidar_x,
                    lidar_y,
                )
            obj_x, obj_y = depth_point_map[0], depth_point_map[1]
            position_source = "depth_tf"

        obj_dist = math.hypot(obj_x - rx, obj_y - ry)
        approach_dist = obj_dist - stop_distance_m
        if approach_dist <= 0.05:
            return {
                "success": True,
                "message": (
                    f"'{target}'까지 거리 {obj_dist:.2f}m({position_source} 기반)로 "
                    f"이미 충분히 가깝습니다. (정지 여유: {stop_distance_m}m)"
                ),
                "distance_m": round(measured_dist, 3),
                "bearing_deg": round(math.degrees(bearing_rad), 1),
                "position_source": position_source,
                "depth_available": depth_point_map is not None,
            }

        unit_x, unit_y = (obj_x - rx) / obj_dist, (obj_y - ry) / obj_dist
        target_x = rx + approach_dist * unit_x
        target_y = ry + approach_dist * unit_y

        # 목표 좌표를 계산한 프레임과 동일한 프레임으로 전송해 좌표 불일치를 방지
        frame = pose["frame"]

        from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

        gate_ok, gate_err = check_navigation_safety_gate(
            self.node, target_x=target_x, target_y=target_y
        )
        if not gate_ok:
            logger.warning("approach_object safety gate rejection: %s", gate_err)
            return self.fail_result(gate_err)

        logger.info(
            "Approach move: current(%.2f,%.2f) -> target(%.2f,%.2f) [%.2fm move, frame=%s]",
            rx,
            ry,
            target_x,
            target_y,
            approach_dist,
            frame,
        )

        accepted, message, goal_handle = _send_navigation_goal(self.node, target_x, target_y, frame)
        if not accepted:
            return {"success": False, "message": message}

        self.send_user_message(
            f"📍 '{target}' 방향으로 이동을 시작합니다. "
            f"(거리 {measured_dist:.1f}m, {stop_distance_m}m 앞에서 정지)"
        )

        token = getattr(goal_handle, "_goal_token", None)

        def wait_for_completion() -> None:
            success, result_message = _wait_for_goal_result(
                goal_handle, timeout_sec=120.0, label="객체 접근", token=token
            )
            if success:
                self.send_user_message(f"✅ '{target}' 앞 {stop_distance_m}m 지점에 도착했습니다.")
            elif result_message == "취소되었습니다.":
                self.send_user_message(f"🛑 '{target}' 접근 이동이 취소되었습니다.")
            else:
                logger.error("Object approach move failed: %s", result_message)
                self.send_user_message(
                    f"❌ '{target}' 접근 중 문제가 발생했습니다. {result_message}"
                )

        thread = threading.Thread(target=wait_for_completion, daemon=True)
        thread.start()

        result: dict[str, Any] = {
            "success": True,
            "message": f"'{target}' 방향 접근 이동을 시작했습니다.",
            "target_object": target,
            "measured_distance_m": round(measured_dist, 3),
            "approach_distance_m": round(approach_dist, 3),
            "stop_distance_m": stop_distance_m,
            "bearing_deg": round(math.degrees(bearing_rad), 1),
            "destination": {"x": round(target_x, 3), "y": round(target_y, 3)},
            "position_source": position_source,
            "depth_available": depth_point_map is not None,
        }
        if depth_distance_m is not None:
            result["depth_distance_m"] = round(depth_distance_m, 3)
        return result
