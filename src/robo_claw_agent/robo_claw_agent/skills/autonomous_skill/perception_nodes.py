import logging
import math

from robo_claw_agent.skill_manager import BaseSkill

from .core import BTNode, NodeStatus

logger = logging.getLogger(__name__)


class MonitorObjectNode(BTNode):
    """특정 객체가 감지되는지 감시하는 BT 노드. 감지 시 좌표를 Blackboard에 저장하고 SUCCESS 반환.

    좌표는 카메라 픽셀 bearing + LiDAR 거리를 로봇의 현재 map pose에 더한 극좌표
    추정치다(perception_skill.detect.FindObjectSkill과 동일한 방식). LiDAR 거리를
    구하지 못하면 fallback_distance_m을 임시 거리로 사용해 최소한 "bearing 방향"은
    맞는 좌표를 만든다 — 로봇의 현재 위치 자체를 물체 위치로 오인해 저장하지 않는다
    (그렇게 하면 ApproachObjectNode가 로봇을 제자리로 이동시키는 결과가 된다).
    """

    def __init__(
        self,
        skill: BaseSkill,
        target_object: str,
        min_score: float = 0.5,
        detections_topic: str = "/object_detector_node/detections",
        lidar_topic: str = "/scan",
        h_fov_deg: float = 60.0,
        image_width: float = 640.0,
        fallback_distance_m: float = 2.0,
    ) -> None:
        super().__init__("MonitorObjectNode")
        self._skill = skill
        self._target_object = target_object.lower()
        self._min_score = min_score
        self._detections_topic = detections_topic
        self._lidar_topic = lidar_topic
        self._h_fov_rad = math.radians(h_fov_deg)
        self._image_width = image_width
        self._fallback_distance_m = fallback_distance_m

    def tick(self) -> NodeStatus:
        from robo_claw_agent.skills.perception_skill.globals import _VISION_MSGS_AVAILABLE
        if not _VISION_MSGS_AVAILABLE:
            return NodeStatus.FAILURE

        from vision_msgs.msg import Detection2DArray

        from robo_claw_agent.skills.perception_skill.core import (
            _distance_at_bearing,
            _resolve_class_name,
        )

        # 토픽에서 메시지 하나 확인 (짧은 타임아웃)
        msg = self._skill.wait_for_message(
            Detection2DArray, self._detections_topic, timeout_sec=0.1
        )
        if not msg:
            return NodeStatus.RUNNING

        for det in msg.detections:
            for r in det.results:
                score = r.hypothesis.score
                if score < self._min_score:
                    continue

                class_name = _resolve_class_name(r.hypothesis.class_id)
                if self._target_object in class_name.lower():
                    logger.info("[BT] MonitorObjectNode: '%s' detected! (score=%.2f)", class_name, score)

                    pose = self._skill.get_map_pose()
                    if not pose:
                        logger.warning(
                            "[BT] MonitorObjectNode: failed to query robot map pose, "
                            "deferring '%s' coordinate registration and continuing to monitor.",
                            class_name,
                        )
                        continue

                    cx = det.bbox.center.position.x
                    bearing_offset = (cx / self._image_width - 0.5) * self._h_fov_rad

                    from sensor_msgs.msg import LaserScan

                    scan = self._skill.wait_for_message(
                        LaserScan, self._lidar_topic, timeout_sec=0.3
                    )
                    distance_m = (
                        _distance_at_bearing(scan, bearing_offset) if scan else None
                    )
                    estimated = distance_m is None
                    if estimated:
                        distance_m = self._fallback_distance_m

                    world_bearing = pose["yaw"] + bearing_offset
                    obj_x = pose["x"] + distance_m * math.cos(world_bearing)
                    obj_y = pose["y"] + distance_m * math.sin(world_bearing)

                    self._blackboard["detected_target_pos"] = {
                        "x": obj_x,
                        "y": obj_y,
                        "frame_id": pose["frame"],
                        "name": class_name,
                        "estimated": estimated,
                    }

                    return NodeStatus.SUCCESS

        return NodeStatus.RUNNING
