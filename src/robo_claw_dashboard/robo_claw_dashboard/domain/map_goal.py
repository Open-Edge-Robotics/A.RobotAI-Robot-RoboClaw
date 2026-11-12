"""지도 목표 계약 — 정규 메시지, 컨텍스트, 좌표/셀 검증, 요청 기록 정규화.

주의: ROS2 / rclpy / generated message import 금지. 모든 함수는 순수 값만 다루며
브라우저와 에이전트가 독립적으로 구현하는 계약을 이 모듈에서 고정한다.
"""

import json
import math
import re
from collections.abc import Mapping
from typing import Any

from .map_geometry import (
    FREE_CELL,
    cell_value,
    validate_dispatch_target,
    validate_map_goal_against_map,
    world_to_cell,
)
from .map_goal_records import (
    AGENT_RECORD_TTL_SEC,
    LEASE_STATUSES,
    REASON_NAVIGATION_NOT_EXECUTED,
    REASON_STALE_AGENT_RECORD,
    REQUEST_ID_PATTERN,
    STATUS_CANCEL_REQUESTED,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PLANNING,
    STATUS_QUEUED,
    STATUS_REACHED,
    STATUS_RUNNING,
    STATUS_UNKNOWN,
    STATUSES,
    TERMINAL_STATUSES,
    adopt_external_record,
    agent_identity_from_state_json,
    can_submit,
    evidence_from_skill_results,
    is_number,
    is_verified_not_executed,
    merge_records,
    normalize_record,
    records_compatible,
    targets_match,
)

#: ExecuteTask.context_json 의 type 값.
MAP_GOAL_CONTEXT_TYPE = "dashboard_map_goal_v1"
#: 선택 후 실제 주행 시작 전까지 유효한 시간(초).
SELECTION_TTL_SEC = 60.0
#: 목표 좌표 절대값 상한(m). 초기 위치(±1000 m)와 달리 선택 목표는 훨씬 넓게 허용한다.
MAX_ABS_GOAL_COORD_M = 1_000_000.0
#: UI/메타데이터 좌표 소수점 자릿수.
CANONICAL_COORD_DECIMALS = 6

ROBOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
FRAME_ID_PATTERN = re.compile(r"^[A-Za-z0-9_./-]{1,64}$")
GEOMETRY_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")

CHAT_KEYS = frozenset({"message", "request_id", "map_goal"})
MAP_GOAL_KEYS = frozenset({"x", "y", "frame_id", "robot_id", "geometry_id"})

CANONICAL_MESSAGE_TEMPLATE = (
    "지도에서 선택한 목표로 이동해 주세요. 로봇: {robot_id}. 기준 프레임: {frame_id}. "
    "목표 좌표: x={x:.6f} m, y={y:.6f} m. "
    "필요한 주행 안전 절차를 먼저 적용하고, 목표 좌표와 프레임을 변경하지 마세요."
)


def canonical_coordinate(value: Any) -> float:
    """좌표를 6자리로 반올림하고 음의 0 을 0.0 으로 정규화한다."""
    rounded = round(float(value), CANONICAL_COORD_DECIMALS)
    return 0.0 if rounded == 0.0 else rounded


def canonical_message(robot_id: str, frame_id: str, x: Any, y: Any) -> str:
    """지도 선택 목표의 정규 자연어 메시지를 만든다(스킬 지시문 아님)."""
    return CANONICAL_MESSAGE_TEMPLATE.format(
        robot_id=str(robot_id),
        frame_id=str(frame_id),
        x=canonical_coordinate(x),
        y=canonical_coordinate(y),
    )


def _safe_token(value: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    return value if pattern.fullmatch(value) else None


def parse_map_goal_request(payload: Any) -> tuple[bool, str | None, dict[str, Any] | None]:
    """POST /api/chat 의 map_goal 모드 본문을 검증하고 정규화한다.

    ``message`` 는 서버가 계산한 정규 메시지와 정확히 같아야 한다. 컨텍스트/시스템
    프롬프트 같은 임의 필드는 받지 않는다.
    """
    if not isinstance(payload, Mapping):
        return False, "지도 목표 요청이 JSON 객체가 아닙니다", None
    extra = set(payload) - CHAT_KEYS
    if extra:
        return False, f"허용되지 않는 요청 필드입니다: {sorted(extra)}", None

    raw_goal = payload.get("map_goal")
    if not isinstance(raw_goal, Mapping):
        return False, "map_goal 은 JSON 객체여야 합니다", None
    missing = MAP_GOAL_KEYS - set(raw_goal)
    extra_goal = set(raw_goal) - MAP_GOAL_KEYS
    if missing or extra_goal:
        return False, "map_goal 필드가 올바르지 않습니다", None

    request_id = _safe_token(payload.get("request_id"), REQUEST_ID_PATTERN)
    if request_id is None:
        return False, "request_id 는 1~64자의 [A-Za-z0-9_-] 이어야 합니다", None

    x, y = raw_goal.get("x"), raw_goal.get("y")
    for name, value in (("x", x), ("y", y)):
        if not is_number(value) or not math.isfinite(float(value)):
            return False, f"목표 {name} 값이 유한한 숫자가 아닙니다", None
        if abs(float(value)) > MAX_ABS_GOAL_COORD_M:
            return (
                False,
                f"목표 {name} 값이 허용 범위(±{MAX_ABS_GOAL_COORD_M:.0f})를 벗어났습니다",
                None,
            )

    robot_id = _safe_token(raw_goal.get("robot_id"), ROBOT_ID_PATTERN)
    if robot_id is None:
        return False, "robot_id 가 올바르지 않습니다", None
    frame_id = _safe_token(raw_goal.get("frame_id"), FRAME_ID_PATTERN)
    if frame_id is None:
        return False, "frame_id 가 올바르지 않습니다", None
    geometry_id = _safe_token(raw_goal.get("geometry_id"), GEOMETRY_ID_PATTERN)
    if geometry_id is None:
        return False, "geometry_id 는 64자 소문자 hex 여야 합니다", None

    message = payload.get("message")
    if not isinstance(message, str) or not message:
        return False, "메시지가 비어 있습니다.", None
    goal_x, goal_y = canonical_coordinate(x), canonical_coordinate(y)
    if message != canonical_message(robot_id, frame_id, goal_x, goal_y):
        return False, "메시지가 정규 지도 목표 형식과 일치하지 않습니다", None

    return (
        True,
        None,
        {
            "request_id": request_id,
            "message": message,
            "robot_id": robot_id,
            "x": goal_x,
            "y": goal_y,
            "frame_id": frame_id,
            "geometry_id": geometry_id,
            "target": {"x": goal_x, "y": goal_y, "frame_id": frame_id},
        },
    )


def goal_signature(goal: Mapping[str, Any]) -> str:
    """멱등성 판정용 목표 서명(요청 ID 는 제외한다)."""
    payload = [
        str(goal.get("robot_id") or ""),
        canonical_coordinate(goal.get("x", 0.0)),
        canonical_coordinate(goal.get("y", 0.0)),
        str(goal.get("frame_id") or ""),
        str(goal.get("geometry_id") or ""),
    ]
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def context_json_for(
    goal: Mapping[str, Any],
    *,
    map_topic: str,
    created_at: float,
) -> str:
    """ExecuteTask.context_json 을 계약된 고정 스키마로 만든다(임의 프롬프트 금지)."""
    created = round(float(created_at), 3)
    payload = {
        "type": MAP_GOAL_CONTEXT_TYPE,
        "request_id": str(goal["request_id"]),
        "robot_id": str(goal["robot_id"]),
        "x": canonical_coordinate(goal["x"]),
        "y": canonical_coordinate(goal["y"]),
        "frame_id": str(goal["frame_id"]),
        "geometry_id": str(goal["geometry_id"]),
        "map_topic": str(map_topic or ""),
        "created_at": created,
        "expires_at": round(created + SELECTION_TTL_SEC, 3),
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def new_record(
    *,
    request_id: str,
    robot_id: str,
    target: Mapping[str, Any],
    geometry_id: str,
    created_at: float,
    message: str = "지도 목표 요청을 접수했습니다",
) -> dict[str, Any]:
    """공유 기록(record) 스키마의 초기 queued 기록을 만든다."""
    return {
        "request_id": str(request_id),
        "robot_id": str(robot_id),
        "status": STATUS_QUEUED,
        "message": str(message),
        "target": {
            "x": canonical_coordinate(target["x"]),
            "y": canonical_coordinate(target["y"]),
            "frame_id": str(target["frame_id"]),
        },
        "geometry_id": str(geometry_id),
        "execution_started": False,
        "cancel_confirmed": False,
        "reason": "",
        "progress": None,
        "created_at": float(created_at),
        "updated_at": float(created_at),
        "can_submit": False,
    }


__all__ = [
    "FREE_CELL",
    "AGENT_RECORD_TTL_SEC",
    "LEASE_STATUSES",
    "MAP_GOAL_CONTEXT_TYPE",
    "REASON_NAVIGATION_NOT_EXECUTED",
    "REASON_STALE_AGENT_RECORD",
    "STATUS_CANCELLED",
    "STATUS_CANCEL_REQUESTED",
    "STATUS_FAILED",
    "STATUS_PLANNING",
    "STATUS_QUEUED",
    "STATUS_REACHED",
    "STATUS_RUNNING",
    "STATUS_UNKNOWN",
    "STATUSES",
    "TERMINAL_STATUSES",
    "adopt_external_record",
    "agent_identity_from_state_json",
    "can_submit",
    "canonical_coordinate",
    "canonical_message",
    "cell_value",
    "context_json_for",
    "evidence_from_skill_results",
    "goal_signature",
    "is_number",
    "is_verified_not_executed",
    "merge_records",
    "new_record",
    "normalize_record",
    "parse_map_goal_request",
    "records_compatible",
    "targets_match",
    "validate_dispatch_target",
    "validate_map_goal_against_map",
    "world_to_cell",
]
