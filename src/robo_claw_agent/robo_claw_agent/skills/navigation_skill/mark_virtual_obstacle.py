"""
가상 장애물 마킹 스킬 — 라이다에 감지되지 않는 장애물을 Nav2 costmap에 주입
"""

import base64
import json
import logging
import math
import struct
import threading
import time
from typing import Any

import cv2
from cv_bridge import CvBridge
from sensor_msgs.msg import LaserScan, PointCloud2, PointField
from std_msgs.msg import Header

from robo_claw_agent.agent_node.utils import _extract_first_json_block
from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)

# 전역 가상 장애물 및 스레드 상태
_VIRTUAL_CLOUDS: list[dict[str, Any]] = []
_VIRTUAL_CLOUDS_LOCK = threading.Lock()
_PUBLISHER_THREAD = None
_PUBLISH_ACTIVE = False

_DISALLOWED_PUBLISH_TOPICS = frozenset(
    {
        "/cmd_vel",
        "/initialpose",
        "/odom",
        "/tf",
        "/tf_static",
        "/joint_states",
        "/amcl_pose",
    }
)

# VLM에게 장애물 위치 추정을 요청하는 프롬프트
_VLM_OBSTACLE_PROMPT = """
이미지에서 '{target}'를 찾아줘.
해당 장애물의 이미지 내 위치(cx: 가로 중심, 0.0=왼쪽 끝, 1.0=오른쪽 끝)와
이미지 상에서 차지하는 높이 비율(height_ratio: 0.0~1.0, 클수록 가까움)을 알려줘.

반드시 아래 JSON 형식으로만 답해줘:
RESULT: {{"found": true, "cx": 0.5, "height_ratio": 0.3, "label": "glass wall"}}
못 찾으면: RESULT: {{"found": false}}
"""


def _make_point_cloud2(points: list[tuple], frame_id: str, stamp: Any) -> PointCloud2:
    """(x, y, z) 좌표 리스트를 PointCloud2 메시지로 변환한다."""
    fields = [
        PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
        PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
        PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
    ]
    point_step = 12
    data = b"".join(struct.pack("fff", x, y, z) for x, y, z in points)

    header = Header()
    header.frame_id = frame_id
    header.stamp = stamp

    msg = PointCloud2()
    msg.header = header
    msg.height = 1
    msg.width = len(points)
    msg.fields = fields
    msg.is_bigendian = False
    msg.point_step = point_step
    msg.row_step = point_step * len(points)
    msg.data = data
    msg.is_dense = True
    return msg


class MarkVirtualObstacleSkill(BaseSkill):
    """
    Nav2 costmap에 가상 장애물 포인트클라우드를 발행하여 자동 우회를 유도하는 스킬
    """

    name = "mark_virtual_obstacle"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string"},
            "x": {"type": "number"},
            "y": {"type": "number"},
            "estimated_distance_m": {"type": "number"},
            "obstacle_radius_m": {"type": "number", "default": 0.5},
            "h_fov_deg": {"type": "number", "default": 60.0},
            "publish_topic": {"type": "string"},
        },
        "required": ["target_object"],
        "additionalProperties": False,
    }
    description = (
        "라이다 미감지 장애물(유리, 투명체, 낮은 물체 등)을 Nav2 costmap에 가상 장애물로 주입하여 자동 우회 경로를 생성합니다. "
        "[모드 1] 좌표 직접 지정: x, y(월드 좌표)를 제공하면 카메라/VLM 없이 즉시 등록합니다. "
        "[모드 2] VLM 탐지: x, y 없이 target_object만 주면 카메라로 장애물을 찾아 위치를 추정합니다. "
        "공통 파라미터: target_object(str, 장애물 설명, 필수), obstacle_radius_m(float, 반경(m), 기본 0.5), "
        "publish_topic(str, 기본 /virtual_obstacles_cloud). "
        "모드 1 추가: x(float, 월드 x좌표), y(float, 월드 y좌표). "
        "모드 2 추가: estimated_distance_m(float), h_fov_deg(float, 기본 60). "
        "예(좌표): {'target_object': '유리벽', 'x': 2.5, 'y': -1.0} "
        "예(VLM): {'target_object': '유리벽', 'estimated_distance_m': 1.5}"
    )

    def __init__(self) -> None:
        super().__init__()
        self._bridge = CvBridge()
        self._pub: Any | None = None
        self._pub_topic = ""

    def _get_publisher(self, topic: str) -> Any:
        """PointCloud2 퍼블리셔를 노드에 생성/재사용한다."""
        if self._pub is None or self._pub_topic != topic:
            self._pub = self.node.create_publisher(PointCloud2, topic, 10)  # type: ignore
            self._pub_topic = topic
        return self._pub

    def _vlm_locate(self, target: str, img_base64: str) -> dict[str, float] | None:
        """VLM으로 장애물 cx(0~1)와 height_ratio(0~1)를 추출한다."""
        if not (self.node and hasattr(self.node, "_llm") and self.node._llm):
            return None
        prompt = _VLM_OBSTACLE_PROMPT.format(target=target)
        try:
            resp = self.node._llm.analyze_image(prompt, img_base64)
            start_idx = resp.find("RESULT:")
            search_text = resp[start_idx + 7 :] if start_idx != -1 else resp
            json_str = _extract_first_json_block(search_text)

            if "{" not in json_str:
                logger.warning(
                    "[mark_virtual_obstacle] No JSON block in VLM response: %s", resp[:200]
                )
                return None
            data = json.loads(json_str)
            if not data.get("found", False):
                return None
            return {
                "cx": float(data.get("cx", 0.5)),
                "height_ratio": float(data.get("height_ratio", 0.2)),
                "label": str(data.get("label", target)),
            }
        except Exception as e:
            logger.error("[mark_virtual_obstacle] VLM failed: %s", e)
            return None

    def _estimate_distance(self, height_ratio: float, h_fov_deg: float) -> float:
        """
        height_ratio(이미지 내 장애물 높이 비율)로 대략적인 거리를 추정한다.
        실제 카메라 캘리브레이션 없이 경험적 관계 사용.
        """
        clamped = max(0.05, min(1.0, height_ratio))
        return round(0.5 / clamped, 2)

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        target = str(params.get("target_object") or "").strip()
        if not target:
            return {"success": False, "message": "target_object 파라미터가 필요합니다."}

        raw_radius = self.get_float_param(params, "obstacle_radius_m", 0.5)
        obstacle_radius = min(max(raw_radius, 0.05), 5.0)

        publish_topic = self.get_string_param(
            params, "publish_topic", "/virtual_obstacles_cloud"
        ).strip()

        if publish_topic in _DISALLOWED_PUBLISH_TOPICS or not (
            publish_topic.startswith("/virtual_obstacle")
            or "cloud" in publish_topic
            or "obstacle" in publish_topic
        ):
            return {
                "success": False,
                "message": f"허용되지 않은 가상 장애물 발행 토픽입니다: {publish_topic}",
            }

        # 모드 분기
        coord_x = params.get("x")
        coord_y = params.get("y")

        if coord_x is not None and coord_y is not None:
            # 월드 좌표 직접 지정
            return self._register_by_coordinate(
                target=target,
                obs_x=float(coord_x),
                obs_y=float(coord_y),
                obstacle_radius=obstacle_radius,
                publish_topic=publish_topic,
                distance_source="user_coordinate",
                frame_id="map",
            )

        # VLM 탐지 등록
        estimated_dist_param = params.get("estimated_distance_m")
        h_fov_deg = float(params.get("h_fov_deg", 60.0))
        h_fov_rad = math.radians(h_fov_deg)
        lidar_topic = self.get_string_param(params, "lidar_topic", "/scan")

        cv_img, camera_topic, is_compressed = self.get_opencv_image(
            params, camera_topic_param_name="camera_topic", timeout_sec=10.0
        )
        if cv_img is None:
            return {
                "success": False,
                "message": (
                    f"카메라 이미지 수신 실패 ({camera_topic}). "
                    "좌표(x, y)를 직접 지정하면 카메라 없이도 등록할 수 있습니다."
                ),
            }

        try:
            _, buf = cv2.imencode(".jpg", cv_img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            img_base64 = base64.b64encode(buf).decode("utf-8")
        except Exception as e:
            return {"success": False, "message": f"이미지 변환 실패: {e}"}

        vlm_result = self._vlm_locate(target, img_base64)
        if vlm_result is None:
            return {
                "success": False,
                "message": (
                    f"카메라에서 '{target}'를 찾지 못했습니다. "
                    "좌표(x, y)를 직접 지정하면 카메라 없이도 등록할 수 있습니다."
                ),
            }

        cx = vlm_result["cx"]
        height_ratio = vlm_result["height_ratio"]
        label = vlm_result["label"]
        bearing_rad = (cx - 0.5) * h_fov_rad

        # 거리 추정 (라이다 또는 VLM)
        distance_m: float
        distance_source: str

        if estimated_dist_param is not None:
            distance_m = float(estimated_dist_param)
            distance_source = "user_input"
        else:
            scan = self.wait_for_message(LaserScan, lidar_topic, timeout_sec=1.5)
            if scan:
                from ..perception_skill.core import _distance_at_bearing

                lidar_dist = _distance_at_bearing(scan, bearing_rad)
                if lidar_dist is not None and lidar_dist < scan.range_max * 0.95:
                    distance_m = lidar_dist
                    distance_source = "lidar"
                else:
                    distance_m = self._estimate_distance(height_ratio, h_fov_deg)
                    distance_source = "vlm_estimate"
            else:
                distance_m = self._estimate_distance(height_ratio, h_fov_deg)
                distance_source = "vlm_estimate"

        logger.info(
            "[mark_virtual_obstacle] '%s' bearing=%.1f°, dist=%.2fm (%s)",
            label,
            math.degrees(bearing_rad),
            distance_m,
            distance_source,
        )

        pose = self.get_map_pose()
        if not pose:
            return {"success": False, "message": "로봇 위치(map/odom) 조회에 실패했습니다."}

        world_bearing = pose["yaw"] + bearing_rad
        obs_x = pose["x"] + distance_m * math.cos(world_bearing)
        obs_y = pose["y"] + distance_m * math.sin(world_bearing)

        # 좌표를 계산한 프레임과 동일 프레임으로 등록(costmap 정합). 보통 map.
        return self._register_by_coordinate(
            target=label,
            obs_x=obs_x,
            obs_y=obs_y,
            obstacle_radius=obstacle_radius,
            publish_topic=publish_topic,
            distance_source=distance_source,
            distance_m=distance_m,
            bearing_deg=math.degrees(bearing_rad),
            frame_id=pose["frame"],
        )

    def _register_by_coordinate(
        self,
        target: str,
        obs_x: float,
        obs_y: float,
        obstacle_radius: float,
        publish_topic: str,
        distance_source: str,
        distance_m: float = 0.0,
        bearing_deg: float = 0.0,
        frame_id: str = "map",
    ) -> dict[str, Any]:
        """월드 좌표(obs_x, obs_y)에 PointCloud2를 발행하고 RAG에 저장한다."""
        n_ring = 12
        points: list[tuple] = []
        for i in range(n_ring):
            angle = 2.0 * math.pi * i / n_ring
            px = obs_x + obstacle_radius * math.cos(angle)
            py = obs_y + obstacle_radius * math.sin(angle)
            points.append((px, py, 0.05))
        points.append((obs_x, obs_y, 0.05))

        global _VIRTUAL_CLOUDS, _PUBLISHER_THREAD, _PUBLISH_ACTIVE

        with _VIRTUAL_CLOUDS_LOCK:
            found_group = False
            for cloud_group in _VIRTUAL_CLOUDS:
                if cloud_group["frame_id"] == frame_id:
                    cloud_group["points"].extend(points)
                    found_group = True
                    break
            if not found_group:
                _VIRTUAL_CLOUDS.append({"frame_id": frame_id, "points": points})

        pub = self._get_publisher(publish_topic)

        if _PUBLISHER_THREAD is None or not _PUBLISHER_THREAD.is_alive():
            _PUBLISH_ACTIVE = True

            def publish_loop():
                while _PUBLISH_ACTIVE and rclpy.ok():
                    if pub:
                        with _VIRTUAL_CLOUDS_LOCK:
                            clouds_snapshot = [
                                dict(g, points=list(g["points"])) for g in _VIRTUAL_CLOUDS
                            ]
                        for cloud_group in clouds_snapshot:
                            if not cloud_group["points"]:
                                continue
                            try:
                                st = self.node.get_clock().now().to_msg()
                                msg = _make_point_cloud2(
                                    cloud_group["points"], cloud_group["frame_id"], st
                                )
                                pub.publish(msg)
                            except Exception as e:
                                logger.error(
                                    "[mark_virtual_obstacle] Periodic publish failed: %s", e
                                )
                    time.sleep(1.0)

            import rclpy

            _PUBLISHER_THREAD = threading.Thread(target=publish_loop, daemon=True)
            _PUBLISHER_THREAD.start()
            logger.info("[mark_virtual_obstacle] Background publish thread started")

        with _VIRTUAL_CLOUDS_LOCK:
            total_pts = sum(len(g["points"]) for g in _VIRTUAL_CLOUDS)
        logger.info(
            "[mark_virtual_obstacle] Obstacle accumulation complete (%d total points, frame: %s)",
            total_pts,
            frame_id,
        )

        if self.node and hasattr(self.node, "_memory") and getattr(self.node, "_enable_rag", False):
            try:
                rag_text = (
                    f"가상 장애물 등록: '{target}' — "
                    f"위치 x={obs_x:.2f}, y={obs_y:.2f} "
                    f"(감지 방법: {distance_source})"
                )
                if distance_m > 0:
                    rag_text += f", 거리 {distance_m:.2f}m"
                self.node._memory.add_knowledge(  # type: ignore
                    rag_text,
                    {
                        "type": "virtual_obstacle",
                        "label": target,
                        "x": round(obs_x, 3),
                        "y": round(obs_y, 3),
                        "distance_m": round(distance_m, 3),
                        "distance_source": distance_source,
                        "source": "mark_virtual_obstacle",
                    },
                )
                logger.info("[mark_virtual_obstacle] RAG save complete")
            except Exception as _rag_e:
                logger.debug("[mark_virtual_obstacle] RAG save failed: %s", _rag_e)

        result: dict[str, Any] = {
            "success": True,
            "message": (
                f"'{target}' 가상 장애물을 등록했습니다. "
                f"(위치 x={obs_x:.2f}, y={obs_y:.2f}) "
                "Nav2가 해당 장애물을 우회하는 경로를 계산합니다."
            ),
            "label": target,
            "obstacle_position": {"x": round(obs_x, 3), "y": round(obs_y, 3)},
            "obstacle_radius_m": obstacle_radius,
            "distance_source": distance_source,
            "publish_topic": publish_topic,
            "point_count": len(points),
        }
        if distance_m > 0:
            result["distance_m"] = round(distance_m, 3)
        if bearing_deg != 0.0:
            result["bearing_deg"] = round(bearing_deg, 1)
        return result


class ClearVirtualObstaclesSkill(BaseSkill):
    """Nav2 costmap에 주입된 모든 가상 장애물을 제거하는 스킬"""

    name = "clear_virtual_obstacles"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = "등록된 모든 가상 장애물을 제거하고 costmap을 정리합니다."

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        global _VIRTUAL_CLOUDS
        with _VIRTUAL_CLOUDS_LOCK:
            _VIRTUAL_CLOUDS.clear()
        return {"success": True, "message": "모든 가상 장애물을 제거했습니다."}
