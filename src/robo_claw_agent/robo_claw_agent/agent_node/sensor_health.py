"""로봇 센서/헬스 상태 점검 로직.

AgentNode 에 있던 ``_check_sensors_cached`` / ``_get_robot_health_state`` 를
분리해 node.py 의 비대함을 줄인다. node 인스턴스의 속성/캐시를 그대로 사용하므로
상태 저장은 여전히 node 에 있다.
"""

import time
from typing import Any

from ..types import AgentState


def check_sensors_cached(node: Any) -> dict[str, bool] | None:
    """핵심 센서(lidar/imu/camera) 토픽 수신 여부를 점검한다.

    결과를 ``node._sensor_cache_ttl_sec``(기본 2초) 동안 캐싱하여 매 태스크마다
    발생하는 동기 대기를 최소화한다. 센서 토픽은 노드 파라미터
    (lidar_topic/imu_topic/camera_topic)를 따르며, 로봇 프로필 YAML에서
    주입된다.
    """
    now = time.monotonic()
    if (
        node._sensor_cache is not None
        and (now - node._sensor_cache_ts) < node._sensor_cache_ttl_sec
    ):
        return node._sensor_cache

    status_skill = node._skills._skills.get("get_status") if node._skills else None
    if status_skill is None:
        return None

    try:
        from sensor_msgs.msg import Imu, LaserScan

        lidar_topic = getattr(node, "_lidar_topic", "/scan") or "/scan"
        imu_topic = getattr(node, "_imu_topic", "/imu") or "/imu"
        # 하드웨어 센서 주기가 느릴 수 있으므로 1.0초 대기
        lidar_ok = (
            status_skill.wait_for_message(LaserScan, lidar_topic, timeout_sec=1.0)
            is not None
        )
        imu_ok = status_skill.wait_for_message(Imu, imu_topic, timeout_sec=1.0) is not None
        # 카메라 점검: 노드에 설정된 camera_topic을 실제로 타도록 params 전달
        cam_params: dict[str, Any] = {}
        cam_topic = getattr(node, "_camera_topic", "")
        if cam_topic:
            cam_params["camera_topic"] = cam_topic
        cam_img, _, _ = status_skill.get_opencv_image(
            cam_params,
            camera_topic_param_name="camera_topic",
            timeout_sec=1.0,
        )
        checks = {
            "lidar": lidar_ok,
            "imu": imu_ok,
            "camera": cam_img is not None,
        }
    except Exception as e:  # noqa: BLE001
        node.get_logger().warning(f"Sensor status check failed: {e}")
        return None

    node._sensor_cache = checks
    node._sensor_cache_ts = now
    return checks


def get_robot_health_state(node: Any, robot_summary: dict | None = None) -> dict:
    """로봇의 배터리·측위·센서 상태를 실측 데이터로 취합한다.

    배터리/측위는 get_robot_summary가 이미 읽은 실측값(robot_summary)을
    재활용하고, 핵심 센서는 토픽 수신 여부로 점검한다. 측정 불가 항목은
    고정값이 아니라 UNKNOWN으로 둔다.
    """
    summary = robot_summary if isinstance(robot_summary, dict) else {}
    state: dict[str, Any] = {
        "battery_percent": None,
        "localization_status": "UNKNOWN",
        "active_errors": [],
        "sensor_status": {
            "lidar": "UNKNOWN",
            "camera": "UNKNOWN",
            "imu": "UNKNOWN",
        },
    }

    # 배터리: get_robot_summary가 읽은 "xx.x%" 실측값 재활용
    battery = summary.get("battery")
    if isinstance(battery, dict):
        try:
            pct = float(str(battery.get("percentage", "")).rstrip("%"))
            state["battery_percent"] = pct
            if pct <= 15.0:
                state["active_errors"].append("LOW_BATTERY")
        except (TypeError, ValueError):
            pass

    # 측위: pose가 실측(dict)되었으면 OK, "unknown"이면 ERROR
    pose = summary.get("pose")
    if isinstance(pose, dict):
        state["localization_status"] = "OK"
    elif pose is not None:
        state["localization_status"] = "ERROR"
        state["active_errors"].append("LOCALIZATION_LOST")

    # 핵심 센서: 토픽 수신 여부로 점검 (캐싱 — 매 태스크마다
    # 0.9초 동기 대기하는 실시간성 저하 방지)
    sensor_checks = check_sensors_cached(node)
    if sensor_checks is not None:
        for key, ok in sensor_checks.items():
            state["sensor_status"][key] = "OK" if ok else "ERROR"
            if not ok:
                state["active_errors"].append(f"{key.upper()}_NO_DATA")

    if node._state == AgentState.ERROR:
        state["localization_status"] = "ERROR"
        state["active_errors"].append("AGENT_INTERNAL_ERROR")
    return state
