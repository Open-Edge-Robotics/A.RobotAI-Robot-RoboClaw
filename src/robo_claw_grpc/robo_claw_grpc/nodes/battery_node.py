"""robo_claw_grpc 배터리 노드"""

import threading

from rclpy.node import Node, QoSProfile
from rclpy.qos import QoSDurabilityPolicy, QoSReliabilityPolicy
from sensor_msgs.msg import BatteryState


class RoboClawGrpcBatteryNode(Node):
    """배터리 상태 구독 노드"""

    def __init__(self, battery_topic: str = "/battery_state"):
        super().__init__("robo_claw_grpc_battery_node")

        try:
            qos_profile = QoSProfile(
                depth=10,
                reliability=QoSReliabilityPolicy.SYSTEM_DEFAULT,
                durability=QoSDurabilityPolicy.SYSTEM_DEFAULT,
            )

            self.battery_subscription = self.create_subscription(
                msg_type=BatteryState,
                topic=battery_topic,
                callback=self.battery_callback,
                qos_profile=qos_profile,
            )

            self._lock = threading.Lock()
            self._current_battery = 0.0
            self._last_logged_battery = -1.0
            self._topic_updated = False

            self.get_logger().info(f"Battery node started — topic: {battery_topic}")

        except Exception as e:
            self.get_logger().error(f"Battery node initialization error: {e}")
            raise e

    def battery_callback(self, msg: BatteryState) -> None:
        try:
            with self._lock:
                percentage = msg.percentage
                if 0.0 <= percentage <= 1.0:
                    percentage *= 100.0
                self._current_battery = percentage
                self._topic_updated = True

            if abs(self._current_battery - self._last_logged_battery) >= 1.0:
                self.get_logger().info(f"Battery: {self._current_battery:.1f}%")
                self._last_logged_battery = self._current_battery

        except Exception as e:
            self.get_logger().error(f"Battery callback error: {e}")

    def get_battery_status(self) -> float:
        with self._lock:
            if not self._topic_updated:
                sysfs_pct = self._read_sysfs_battery()
                if sysfs_pct > 0.0:
                    return sysfs_pct
            return self._current_battery

    def _read_sysfs_battery(self) -> float:
        from pathlib import Path
        for bat_dir in Path("/sys/class/power_supply").glob("BAT*"):
            try:
                cap_file = bat_dir / "capacity"
                if cap_file.exists():
                    pct = float(cap_file.read_text().strip())
                    return pct
            except Exception as e:
                self.get_logger().debug(f"sysfs battery query failed ({bat_dir.name}): {e}")
        return 0.0
