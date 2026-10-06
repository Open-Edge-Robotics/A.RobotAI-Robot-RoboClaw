"""robo_claw_grpc 카메라 노드"""

import base64
import threading

import cv2
import numpy as np
from cv_bridge import CvBridge, CvBridgeError
from rclpy.node import Node, QoSProfile
from rclpy.qos import QoSDurabilityPolicy, QoSReliabilityPolicy
from sensor_msgs.msg import CompressedImage, Image


class RoboClawGrpcCameraNode(Node):
    """robo_claw_grpc 카메라 노드"""

    def __init__(self, camera_topic: str, camera_compressed_topic: str):
        super().__init__("robo_claw_grpc_camera_node")

        try:
            self.bridge = CvBridge()

            qos_profile = QoSProfile(
                depth=1,
                reliability=QoSReliabilityPolicy.SYSTEM_DEFAULT,
                durability=QoSDurabilityPolicy.SYSTEM_DEFAULT,
            )

            self.image_raw_subscription = self.create_subscription(
                msg_type=Image,
                topic=camera_topic,
                callback=self.image_raw_callback,
                qos_profile=qos_profile,
            )
            self.image_compressed_subscription = self.create_subscription(
                msg_type=CompressedImage,
                topic=camera_compressed_topic,
                callback=self.image_compressed_callback,
                qos_profile=qos_profile,
            )

            self._lock = threading.Lock()
            self._latest_raw: np.ndarray | None = None
            self._latest_raw_timestamp: float = 0.0
            self._latest_compressed: bytes | None = None
            self._latest_compressed_timestamp: float = 0.0

            self.get_logger().info(f"Camera node started — raw: {camera_topic}, compressed: {camera_compressed_topic}")

        except Exception as e:
            self.get_logger().error(f"Camera node initialization error: {e}")
            raise e

    def image_raw_callback(self, msg: Image) -> None:
        try:
            cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            with self._lock:
                self._latest_raw = cv_image
                self._latest_raw_timestamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        except CvBridgeError as e:
            self.get_logger().error(f"CV Bridge error: {e}")
        except Exception as e:
            self.get_logger().error(f"Raw image callback error: {e}")

    def image_compressed_callback(self, msg: CompressedImage) -> None:
        try:
            image_data = bytes(msg.data)
            with self._lock:
                self._latest_compressed = image_data
                self._latest_compressed_timestamp = msg.header.stamp.sec + msg.header.stamp.nanosec * 1e-9
        except Exception as e:
            self.get_logger().error(f"Compressed image callback error: {e}")

    def get_latest_image(self, image_type: int = 1) -> tuple[str | None, str | None, float]:
        """요청된 이미지 타입(0: RAW, 1: COMPRESSED)에 맞는 가장 최신 이미지를 Base64로 반환. (이미지 문자열, 포맷, 타임스탬프)"""
        image_str = None
        fmt = None
        timestamp = 0.0

        try:
            with self._lock:
                if image_type == 1:  # COMPRESSED 요청
                    if self._latest_compressed is not None:
                        data_to_process = self._latest_compressed
                        timestamp = self._latest_compressed_timestamp
                        is_compressed = True
                    elif self._latest_raw is not None:
                        data_to_process = self._latest_raw
                        timestamp = self._latest_raw_timestamp
                        is_compressed = False
                    else:
                        return None, None, 0.0
                else:  # RAW 요청 (0)
                    if self._latest_raw is not None:
                        data_to_process = self._latest_raw
                        timestamp = self._latest_raw_timestamp
                        is_compressed = False
                    elif self._latest_compressed is not None:
                        data_to_process = self._latest_compressed
                        timestamp = self._latest_compressed_timestamp
                        is_compressed = True
                    else:
                        return None, None, 0.0

            if is_compressed:
                if isinstance(data_to_process, bytes):
                    image_str = base64.b64encode(data_to_process).decode("utf-8")
                    fmt = "COMPRESSED"
            else:
                if isinstance(data_to_process, np.ndarray):
                    success, buffer = cv2.imencode(".jpg", data_to_process)
                    if success:
                        image_str = base64.b64encode(buffer).decode("utf-8")  # type: ignore
                        fmt = "RAW"
                    else:
                        self.get_logger().error("JPEG compression failed")

        except Exception as e:
            self.get_logger().error(f"Image encoding error: {e}")
            return None, None, 0.0

        return image_str, fmt, timestamp
