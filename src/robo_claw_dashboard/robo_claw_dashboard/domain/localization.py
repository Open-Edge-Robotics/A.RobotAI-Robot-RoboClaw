"""지도/위치 도메인 규칙 — OccupancyGrid 정규화와 초기 위치 검증.

주의: ROS2 / rclpy / generated message import 금지. 입력은 순수 Python 값만 받는다.
브라우저가 바로 그릴 수 있도록 occupancy 셀을 uint8 로 정규화하고 base64 로 인코딩한다.
"""

import base64
import math
from collections.abc import Mapping, Sequence
from typing import Any

#: OccupancyGrid 의 미확인 셀(-1)을 브라우저에서 구분하기 위한 값.
UNKNOWN_CELL = 255
#: 점유 확률 상한(ROS 관례상 100).
OCCUPIED_MAX = 100
#: 초기 위치 좌표 절대값 상한(m). 지도 밖 좌표로 AMCL 파티클을 흩뜨리는 입력을 막는다.
MAX_ABS_COORD_M = 1000.0
#: 지도 경계 판정 여유(m). 경계에 걸친 로봇 위치를 허용한다.
MAP_MARGIN_M = 1.0
#: AMCL 초기 위치 공분산(명시 입력). robo_claw_agent set_initial_pose 스킬과 동일 값.
INITIAL_POSE_COVARIANCE = 0.05
#: 지도 토픽 기본값. 순서가 우선순위이며, 데이터가 들어오는 토픽을 사용한다.
#: 로봇마다 지도 토픽이 다르다(예: former0047 `/map`, CLOiD `/lio/grid_map`).
DEFAULT_MAP_TOPICS = ("/map", "/lio/grid_map")


def parse_map_topics(raw: Any) -> list[str]:
    """지도 토픽 설정 문자열을 토픽 목록으로 바꾼다(쉼표 구분, 순서 유지, 중복 제거)."""
    if raw is None:
        return list(DEFAULT_MAP_TOPICS)
    if isinstance(raw, (list, tuple)):
        candidates = [str(item) for item in raw]
    else:
        candidates = str(raw).split(",")
    topics: list[str] = []
    for candidate in candidates:
        topic = candidate.strip()
        if topic and topic not in topics:
            topics.append(topic)
    return topics or list(DEFAULT_MAP_TOPICS)


def normalize_yaw(yaw: float) -> float:
    """yaw 를 [-pi, pi] 범위로 정규화한다."""
    normalized = math.fmod(yaw + math.pi, 2.0 * math.pi)
    if normalized < 0:
        normalized += 2.0 * math.pi
    return normalized - math.pi


def _normalize_cell(value: Any) -> int:
    """OccupancyGrid 셀 값을 uint8(0~100, 미확인 255)로 정규화한다."""
    try:
        cell = int(value)
    except (TypeError, ValueError):
        return UNKNOWN_CELL
    if cell < 0:
        return UNKNOWN_CELL
    return min(cell, OCCUPIED_MAX)


def encode_cells_base64(cells: Sequence[Any]) -> str:
    """occupancy 셀 시퀀스를 uint8 base64 문자열로 인코딩한다."""
    return base64.b64encode(bytes(_normalize_cell(cell) for cell in cells)).decode("ascii")


def _check_map_meta(*, width: int, height: int, resolution: float) -> None:
    if int(width) <= 0 or int(height) <= 0:
        raise ValueError(f"지도 크기가 올바르지 않습니다: {width}x{height}")
    if not math.isfinite(float(resolution)) or float(resolution) <= 0.0:
        raise ValueError(f"지도 해상도가 올바르지 않습니다: {resolution}")


def build_map_snapshot(
    *,
    width: int,
    height: int,
    resolution: float,
    origin: Mapping[str, Any],
    frame_id: str,
    cells: Sequence[Any],
    stamp_ns: int = 0,
    topic: str = "",
) -> dict[str, Any]:
    """OccupancyGrid 를 브라우저 렌더링용 스냅샷 dict 로 정규화한다."""
    _check_map_meta(width=width, height=height, resolution=resolution)
    expected = int(width) * int(height)
    if len(cells) != expected:
        raise ValueError(f"지도 셀 수가 크기와 맞지 않습니다: {len(cells)} != {expected}")
    snapshot = {
        "available": True,
        "frame_id": str(frame_id),
        "stamp_ns": int(stamp_ns),
        "width": int(width),
        "height": int(height),
        "resolution": float(resolution),
        "origin": {
            "x": float(origin.get("x", 0.0)),
            "y": float(origin.get("y", 0.0)),
            "yaw": float(origin.get("yaw", 0.0)),
        },
        "encoding": "uint8",
        "data": encode_cells_base64(cells),
    }
    if topic:
        snapshot["topic"] = str(topic)
    return snapshot


def unavailable_map(reason: str = "") -> dict[str, Any]:
    """지도 미수신 상태를 나타내는 스냅샷."""
    payload: dict[str, Any] = {"available": False}
    if reason:
        payload["reason"] = str(reason)
    return payload


def build_pose_snapshot(
    *,
    x: float,
    y: float,
    yaw: float,
    frame_id: str = "map",
    base_frame: str = "base_link",
    stamp_ns: int = 0,
) -> dict[str, Any] | None:
    """map→base 변환을 대시보드 위치 스냅샷으로 정규화한다(비유한 값은 None)."""
    for value in (x, y, yaw):
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return None
    return {
        "available": True,
        "frame_id": str(frame_id),
        "base_frame": str(base_frame),
        "x": round(float(x), 3),
        "y": round(float(y), 3),
        "yaw": round(normalize_yaw(float(yaw)), 6),
        "stamp_ns": int(stamp_ns),
    }


def unavailable_pose() -> dict[str, Any]:
    """위치 미수신 상태."""
    return {"available": False}


def _map_extent(map_meta: Mapping[str, Any] | None) -> tuple[float, float, float, float] | None:
    """지도 스냅샷에서 (min_x, min_y, max_x, max_y) 를 계산한다."""
    if not map_meta:
        return None
    try:
        width = float(map_meta["width"])
        height = float(map_meta["height"])
        resolution = float(map_meta["resolution"])
        origin = map_meta["origin"]
        origin_x = float(origin["x"])
        origin_y = float(origin["y"])
    except (KeyError, TypeError, ValueError):
        return None
    return (
        origin_x - MAP_MARGIN_M,
        origin_y - MAP_MARGIN_M,
        origin_x + width * resolution + MAP_MARGIN_M,
        origin_y + height * resolution + MAP_MARGIN_M,
    )


def _coerce_coordinate(value: Any, name: str) -> tuple[bool, str | None, float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False, f"초기 위치 {name} 값이 숫자가 아닙니다: {value!r}", 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False, f"초기 위치 {name} 값이 숫자가 아닙니다: {value!r}", 0.0
    if not math.isfinite(number):
        return False, f"초기 위치 {name} 값이 유한한 수가 아닙니다: {value!r}", 0.0
    return True, None, number


def validate_initial_pose(
    payload: Any,
    *,
    map_meta: Mapping[str, Any] | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """초기 위치 요청 본문을 검증하고 정규화된 pose 를 반환한다.

    반환값은 (ok, error, pose). pose 는 ``frame_id`` 를 포함하며, 실패 시 None 이다.
    지도가 있으면 지도 경계(+여유 1 m) 밖 좌표를 거부하고, 없으면 절대값 상한만 적용한다.
    """
    if not isinstance(payload, Mapping):
        return False, "초기 위치 요청이 JSON 객체가 아닙니다", None

    x_ok, x_error, x = _coerce_coordinate(payload.get("x"), "x")
    if not x_ok:
        return False, x_error, None
    y_ok, y_error, y = _coerce_coordinate(payload.get("y"), "y")
    if not y_ok:
        return False, y_error, None

    yaw_source = payload.get("yaw", 0.0)
    yaw_ok, yaw_error, yaw = _coerce_coordinate(yaw_source, "yaw")
    if not yaw_ok:
        return False, yaw_error, None

    if abs(x) > MAX_ABS_COORD_M or abs(y) > MAX_ABS_COORD_M:
        return (
            False,
            f"초기 위치 좌표가 허용 범위(±{MAX_ABS_COORD_M:.0f} m)를 벗어났습니다: ({x}, {y})",
            None,
        )

    extent = _map_extent(map_meta)
    if extent is not None:
        min_x, min_y, max_x, max_y = extent
        if not (min_x <= x <= max_x and min_y <= y <= max_y):
            return (
                False,
                f"초기 위치가 지도 범위를 벗어났습니다: ({x:.2f}, {y:.2f}) "
                f"허용 x[{min_x:.2f}, {max_x:.2f}] y[{min_y:.2f}, {max_y:.2f}]",
                None,
            )

    frame_id = str(payload.get("frame_id") or "map")
    return (
        True,
        None,
        {
            "x": x,
            "y": y,
            "yaw": normalize_yaw(yaw),
            "frame_id": frame_id,
        },
    )


def build_initial_pose(
    *,
    x: float,
    y: float,
    yaw: float,
    covariance: float = INITIAL_POSE_COVARIANCE,
    frame_id: str = "map",
) -> dict[str, Any]:
    """PoseWithCovarianceStamped 발행에 필요한 값만 담은 dict 를 만든다."""
    normalized_yaw = normalize_yaw(float(yaw))
    values = [0.0] * 36
    values[0] = float(covariance)
    values[7] = float(covariance)
    values[35] = float(covariance)
    return {
        "frame_id": str(frame_id),
        "x": float(x),
        "y": float(y),
        "yaw": normalized_yaw,
        "orientation_z": math.sin(normalized_yaw / 2.0),
        "orientation_w": math.cos(normalized_yaw / 2.0),
        "covariance": values,
    }
