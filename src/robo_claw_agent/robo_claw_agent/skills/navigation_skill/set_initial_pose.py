"""AMCL 초기 위치 설정 스킬 — /initialpose 토픽 발행"""

import logging
import math
from typing import Any

from geometry_msgs.msg import PoseWithCovarianceStamped

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)


class SetInitialPoseSkill(BaseSkill):
    """AMCL의 초기 추정 위치를 설정합니다.

    /initialpose 토픽에 PoseWithCovarianceStamped 메시지를 발행하여
    AMCL 파티클 분포를 지정된 위치 주변으로 집중시킵니다.
    """

    name = "set_initial_pose"
    input_schema = {"type": "object", "properties": {
        "x": {"type": "number"}, "y": {"type": "number"}, "yaw": {"type": "number", "description": "라디안"}
    }, "required": ["x", "y", "yaw"], "additionalProperties": False}
    description = (
        "로봇의 초기 위치를 지정된 x, y 좌표와 yaw(라디안)로 설정합니다. "
        "AMCL 파티클 필터의 초기 추정치를 해당 위치로 좁힙니다. "
        "예: {'x': 0.0, 'y': 0.0, 'yaw': 0.0}"
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if (err := self.require_node()):
            return err

        x = self.get_float_param(params, "x", 0.0)
        y = self.get_float_param(params, "y", 0.0)
        yaw = self.get_float_param(params, "yaw", 0.0)

        from robo_claw_agent.agent_node.nav_safety import check_navigation_safety_gate

        gate_ok, gate_err = check_navigation_safety_gate(
            self.node, target_x=x, target_y=y, target_yaw=yaw, check_stow=False
        )
        if not gate_ok:
            logger.warning("SetInitialPose safety gate rejection: %s", gate_err)
            return self.fail_result(f"초기 위치 설정 거부: {gate_err}")

        logger.info("SetInitialPose: x=%.2f, y=%.2f, yaw=%.2f", x, y, yaw)

        try:
            init_pose = PoseWithCovarianceStamped()
            init_pose.header.frame_id = "map"
            try:
                init_pose.header.stamp = self.node.get_clock().now().to_msg()
            except Exception:
                init_pose.header.stamp.sec = 0
                init_pose.header.stamp.nanosec = 0
            init_pose.pose.pose.position.x = float(x)
            init_pose.pose.pose.position.y = float(y)
            init_pose.pose.pose.position.z = 0.0
            init_pose.pose.pose.orientation.z = math.sin(float(yaw) / 2.0)
            init_pose.pose.pose.orientation.w = math.cos(float(yaw) / 2.0)

            # 위치 공분산 (AMCL이 파티클을 좁은 영역에 집중하도록 작은 값 설정)
            init_pose.pose.covariance[0] = 0.05   # x
            init_pose.pose.covariance[7] = 0.05   # y
            init_pose.pose.covariance[35] = 0.05  # yaw

            # /initialpose 퍼블리셔 생성 및 발행
            from rclpy.qos import QoSProfile, ReliabilityPolicy

            qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE)
            pub = self.node.create_publisher(
                PoseWithCovarianceStamped, "/initialpose", qos
            )

            # 퍼블리셔 준비 대기 (최대 2초)
            import time

            t0 = time.monotonic()
            while not pub.wait_for_subscribers(timeout_sec=0.5):
                if time.monotonic() - t0 > 2.0:
                    logger.warning("No subscribers on /initialpose after 2s, publishing anyway")
                    break

            pub.publish(init_pose)
            logger.info("Initial pose published to /initialpose")

            # 발행 후 정리
            self.node.destroy_publisher(pub)

            self.send_user_message(
                f"📍 초기 위치를 설정했습니다: ({x:.2f}, {y:.2f}), 방향 {math.degrees(yaw):.1f}°"
            )
            return self.success_result(
                f"초기 위치 설정 성공: x={x:.2f}, y={y:.2f}, yaw={yaw:.2f} ({math.degrees(yaw):.1f}°)",
                x=x,
                y=y,
                yaw=yaw,
            )
        except Exception as e:
            logger.exception("SetInitialPose failed: %s", e)
            return self.fail_result(f"초기 위치 설정 실패: {e}")
