"""지도 목표 지오메트리 — 월드↔그리드 변환, 셀 판정, 제출/위임 재검증.

주의: ROS2 / rclpy / generated message import 금지. 지도 지문(geometry_id)은
localization.compute_geometry_id 를 단일 구현으로 재사용한다.
"""

import base64
import math
from collections.abc import Mapping
from typing import Any

from .localization import compute_geometry_id

#: 목표로 인정하는 OccupancyGrid 셀 값(확인된 자유 공간).
FREE_CELL = 0


def _map_meta(
    map_snapshot: Mapping[str, Any] | None,
) -> tuple[int, int, float, float, float, float] | None:
    if not isinstance(map_snapshot, Mapping) or not map_snapshot.get("available"):
        return None
    try:
        origin = map_snapshot["origin"]
        width = int(map_snapshot["width"])
        height = int(map_snapshot["height"])
        resolution = float(map_snapshot["resolution"])
        origin_x = float(origin["x"])
        origin_y = float(origin["y"])
        yaw = float(origin.get("yaw", 0.0))
    except (KeyError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0 or not math.isfinite(resolution) or resolution <= 0.0:
        return None
    if not all(math.isfinite(value) for value in (origin_x, origin_y, yaw)):
        return None
    return width, height, resolution, origin_x, origin_y, yaw


def world_to_cell(
    map_snapshot: Mapping[str, Any] | None, x: float, y: float
) -> tuple[int, int] | None:
    """월드 좌표를 반열린(half-open) 그리드 셀로 변환한다.

    지도 원점 회전을 역변환하므로 yaw≠0 인 지도에서도 셀이 정확히 맞는다.
    지도 밖 좌표는 None.
    """
    meta = _map_meta(map_snapshot)
    if meta is None:
        return None
    width, height, resolution, origin_x, origin_y, yaw = meta
    cos_yaw, sin_yaw = math.cos(yaw), math.sin(yaw)
    dx, dy = float(x) - origin_x, float(y) - origin_y
    local_x = cos_yaw * dx + sin_yaw * dy
    local_y = -sin_yaw * dx + cos_yaw * dy
    col = math.floor(local_x / resolution)
    row = math.floor(local_y / resolution)
    if not (0 <= col < width and 0 <= row < height):
        return None
    return int(col), int(row)


def cell_value(map_snapshot: Mapping[str, Any] | None, col: int, row: int) -> int | None:
    """정규화된 셀 값을 돌려준다(지도/데이터가 깨졌으면 None)."""
    meta = _map_meta(map_snapshot)
    if meta is None:
        return None
    width, height, _resolution, _ox, _oy, _yaw = meta
    if not (0 <= int(col) < width and 0 <= int(row) < height):
        return None
    try:
        data = base64.b64decode(str(map_snapshot["data"]), validate=False)
    except (KeyError, TypeError, ValueError):
        return None
    if len(data) != width * height:
        return None
    return int(data[int(row) * width + int(col)])


def validate_map_goal_against_map(
    goal: Mapping[str, Any] | None,
    map_snapshot: Mapping[str, Any] | None,
    agent_identity: Mapping[str, Any] | None,
) -> tuple[bool, str | None, str]:
    """현재 지도/에이전트 기준으로 목표를 재검증한다.

    반환값은 (ok, error, kind). kind 는 ``ok`` / ``invalid``(400) /
    ``unavailable``(503) 이다.
    """
    if not isinstance(goal, Mapping):
        return False, "목표 정보가 없습니다", "invalid"
    meta = _map_meta(map_snapshot)
    if meta is None:
        return False, "지도를 아직 수신하지 못했습니다", "unavailable"
    assert map_snapshot is not None
    geometry_id = compute_geometry_id(map_snapshot)
    if geometry_id is None:
        return False, "지도 지오메트리를 확인할 수 없습니다", "unavailable"
    if str(goal.get("geometry_id") or "") != geometry_id:
        return False, "지도 지오메트리가 변경되었습니다. 지도를 다시 선택하세요", "invalid"

    frame_id = str(map_snapshot.get("frame_id") or "")
    if str(goal.get("frame_id") or "") != frame_id:
        return False, f"지원하지 않는 목표 프레임입니다: {goal.get('frame_id')!r}", "invalid"

    if not isinstance(agent_identity, Mapping):
        return False, "에이전트 상태를 확인할 수 없습니다", "unavailable"
    agent_id = str(agent_identity.get("agent_id") or "")
    if not agent_id:
        return False, "에이전트 로봇 ID 를 확인할 수 없습니다", "unavailable"
    if str(goal.get("robot_id") or "") != agent_id:
        return False, "요청한 로봇 ID 가 현재 에이전트와 일치하지 않습니다", "invalid"
    nav_frame = str(agent_identity.get("navigation_frame") or "")
    if nav_frame and nav_frame != frame_id:
        return False, f"에이전트 주행 프레임({nav_frame})과 목표 프레임이 다릅니다", "invalid"

    cell = world_to_cell(map_snapshot, float(goal.get("x", 0.0)), float(goal.get("y", 0.0)))
    if cell is None:
        return False, "목표가 지도 범위를 벗어났습니다", "invalid"
    value = cell_value(map_snapshot, *cell)
    if value is None:
        return False, "지도 셀 데이터를 확인할 수 없습니다", "unavailable"
    if value != FREE_CELL:
        return False, f"목표 셀이 확인된 자유 공간이 아닙니다(값={value})", "invalid"
    return True, None, "ok"


def validate_dispatch_target(
    goal: Mapping[str, Any], map_snapshot: Mapping[str, Any] | None
) -> tuple[bool, str, str]:
    """첫 실제 위임 직전에 최신 지도 지오메트리/프레임/셀을 다시 확인한다."""
    geometry_id = compute_geometry_id(map_snapshot)
    if geometry_id is None:
        return False, "map_unavailable", "지도를 확인할 수 없어 목표를 보내지 않았습니다"
    if geometry_id != str(goal.get("geometry_id") or ""):
        return False, "geometry_changed", "지도 지오메트리가 변경되어 목표를 보내지 않았습니다"
    if str(map_snapshot.get("frame_id") or "") != str(goal.get("frame_id") or ""):
        return False, "frame_changed", "지도 프레임이 변경되어 목표를 보내지 않았습니다"
    cell = world_to_cell(map_snapshot, float(goal["x"]), float(goal["y"]))
    if cell is None:
        return False, "outside_map", "목표가 지도 범위를 벗어났습니다"
    if cell_value(map_snapshot, *cell) != FREE_CELL:
        return False, "cell_not_free", "목표 셀이 더 이상 자유 공간이 아닙니다"
    return True, "", ""
