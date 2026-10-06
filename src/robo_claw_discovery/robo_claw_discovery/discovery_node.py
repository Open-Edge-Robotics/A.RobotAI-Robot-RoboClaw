"""
Discovery 노드 — ROS2 토픽/서비스/액션을 자동 탐색해 에이전트 컨텍스트를 구성
"""

import json
import subprocess
import threading
from typing import Any

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String


class DiscoveryNode(Node):
    """
    ROS2 그래프를 주기적으로 탐색해 사용 가능한 인터페이스 목록을 퍼블리시.
    에이전트 노드는 이 정보를 기반으로 스킬 실행 가능 여부를 판단한다.
    """

    def __init__(self, scan_interval_sec: float = 10.0) -> None:
        super().__init__("robo_claw_discovery_node")

        self.declare_parameter("scan_interval_sec", scan_interval_sec)
        interval = (
            self.get_parameter("scan_interval_sec").get_parameter_value().double_value
        )

        qos = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE)

        # 탐색 결과 퍼블리시
        self._pub = self.create_publisher(String, "~/robot_capabilities", qos)
        self._scan_lock = threading.Lock()
        self._scan_thread: threading.Thread | None = None

        # 최초 스캔 + 주기 스캔
        self._timer = self.create_timer(interval, self._scan)
        self.get_logger().info(f"DiscoveryNode ready ({interval:.1f}s interval)")
        self._scan()  # 시작 시 즉시 실행

    def _scan(self) -> None:
        """ROS2 그래프 탐색을 백그라운드에서 수행한다."""
        with self._scan_lock:
            if self._scan_thread is not None and self._scan_thread.is_alive():
                self.get_logger().debug("Previous scan still in progress, skipping this scan.")
                return
            self._scan_thread = threading.Thread(
                target=self._scan_worker,
                name="robo-claw-discovery-scan",
                daemon=True,
            )
            self._scan_thread.start()

    def _scan_worker(self) -> None:
        """ROS2 그래프 탐색 후 결과를 JSON으로 퍼블리시"""
        # Node API를 사용해 내부적으로 탐색 (subprocess 지양)
        nodes = self.get_node_names()
        # Phase 13: 다른 RoboClaw 에이전트 식별
        agents = [n for n in nodes if "robo_claw_agent_node" in n]

        capabilities = {
            "topics": self._get_topics_api(),
            "services": self._get_services_api(),
            "actions": self._get_actions_api(),
            "nodes": nodes,
            "agents": agents,
        }
        msg = String()
        msg.data = json.dumps(capabilities, ensure_ascii=False)
        self._pub.publish(msg)
        self.get_logger().info(
            f"Discovery complete: topics {len(capabilities['topics'])}, "
            f"services {len(capabilities['services'])}, "
            f"actions {len(capabilities['actions'])}, "
            f"nodes {len(capabilities['nodes'])}"
        )

    # ── 탐색 헬퍼 (Node API 사용) ─────────────────────────────────────────────

    def _get_topics_api(self) -> list[dict[str, Any]]:
        try:
            names_and_types = self.get_topic_names_and_types()
            return [{"name": name, "type": types[0]} for name, types in names_and_types]
        except Exception as e:
            self.get_logger().error(f"Topic discovery failed: {e}")
            return []

    def _get_services_api(self) -> list[dict[str, Any]]:
        try:
            names_and_types = self.get_service_names_and_types()
            return [{"name": name, "type": types[0]} for name, types in names_and_types]
        except Exception as e:
            self.get_logger().error(f"Service discovery failed: {e}")
            return []

    def _get_actions_api(self) -> list[dict[str, Any]]:
        try:
            # Action은 rclpy에 직접적인 get_action_names_and_types가 없으므로
            # 토픽 이름 패턴(/_action/feedback 등)으로 추론하거나 subprocess 유지
            # 여기서는 안정성을 위해 subprocess를 쓰되 timeout을 짧게 잡음
            result = subprocess.run(
                ["ros2", "action", "list", "-t"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            actions = []
            for line in result.stdout.strip().splitlines():
                parts = line.strip().split(" ")
                if len(parts) == 2:
                    actions.append({"name": parts[0], "type": parts[1].strip("[]")})
            return actions
        except Exception as e:
            self.get_logger().error(f"Action discovery failed: {e}")
            return []


def main() -> None:
    rclpy.init()
    node = DiscoveryNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        try:
            rclpy.shutdown()
        except Exception:
            pass
