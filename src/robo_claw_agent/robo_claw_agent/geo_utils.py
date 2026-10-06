"""지오메트리 헬퍼.

ROS2 ``geometry_msgs/Quaternion`` (속성 접근: ``q.x, q.y, q.z, q.w``) 와
dict-like quaternion (``Mapping``: ``q["x"] ...``) 모두에서 yaw(라디안)을
추출하는 공통 유틸. 여러 노드/스킬에서 복제되던 ``atan2(...)`` 공식을 한 곳으로 통합.

rclpy / sensor_msgs 등 ROS 의존성이 없으므로 테스트 환경에서도 안전하게 임포트 가능.
"""

import math
from collections.abc import Mapping
from typing import Any


def _components(q: Any) -> tuple[float, float, float, float]:
    """quaternion 에서 (x, y, z, w) 성분을 꺼낸다.

    ROS 메시지(속성)와 dict-like(Mapping) 양쪽을 모두 지원한다.
    """
    if isinstance(q, Mapping):
        get = q.get
        return (
            float(get("x", 0.0)),
            float(get("y", 0.0)),
            float(get("z", 0.0)),
            float(get("w", 1.0)),
        )
    return (
        float(getattr(q, "x", 0.0)),
        float(getattr(q, "y", 0.0)),
        float(getattr(q, "z", 0.0)),
        float(getattr(q, "w", 1.0)),
    )


def yaw_from_quaternion(q: Any) -> float:
    """quaternion → yaw(z축 회전, 라디안).

    ROS Rz*Ry*Rx 컨벤션. ``manipulation_runtime.math.rpy_from_quaternion`` 의
    yaw 성분만 따로 뽑은 경량 버전이며 메시지/Mapping 양쪽을 모두 받는다.
    """
    x, y, z, w = _components(q)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def heading_deg_from_quaternion(q: Any) -> float:
    """quaternion → heading(도). 0~360 범위가 아닌 atan2 결과 그대로의 도 단위."""
    return math.degrees(yaw_from_quaternion(q))
