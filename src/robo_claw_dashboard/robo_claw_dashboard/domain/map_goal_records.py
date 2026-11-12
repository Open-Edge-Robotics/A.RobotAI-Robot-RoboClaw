"""지도 목표 기록/lease 규칙 — 공유 record 스키마, 전이 보호, 에이전트 신원/증거.

주의: ROS2 / rclpy / generated message import 금지. 순수 값만 다룬다.
"""

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

#: 대시보드 재시작 후 외부(에이전트) 기록을 신뢰하는 최대 시간(초).
AGENT_RECORD_TTL_SEC = 15.0
#: 외부 기록이 오래되어 상태를 확정할 수 없을 때의 사유.
REASON_STALE_AGENT_RECORD = "stale_agent_record"
#: 주행이 실제로 시작되지 않았음을 에이전트가 명시한 사유.
REASON_NAVIGATION_NOT_EXECUTED = "navigation_not_executed"

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

STATUS_QUEUED = "queued"
STATUS_PLANNING = "planning"
STATUS_RUNNING = "running"
STATUS_REACHED = "reached"
STATUS_FAILED = "failed"
STATUS_CANCEL_REQUESTED = "cancel_requested"
STATUS_CANCELLED = "cancelled"
STATUS_UNKNOWN = "unknown"

STATUSES = frozenset(
    {
        STATUS_QUEUED,
        STATUS_PLANNING,
        STATUS_RUNNING,
        STATUS_REACHED,
        STATUS_FAILED,
        STATUS_CANCEL_REQUESTED,
        STATUS_CANCELLED,
        STATUS_UNKNOWN,
    }
)
TERMINAL_STATUSES = frozenset({STATUS_REACHED, STATUS_FAILED, STATUS_CANCELLED})
#: 새 지도 목표 제출을 막는(lease 를 잡고 있는) 상태.
LEASE_STATUSES = frozenset(
    {STATUS_QUEUED, STATUS_PLANNING, STATUS_RUNNING, STATUS_CANCEL_REQUESTED, STATUS_UNKNOWN}
)


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number(value: Any, default: float = 0.0) -> float:
    if not is_number(value) or not math.isfinite(float(value)):
        return default
    return float(value)


def _target(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    x, y = raw.get("x"), raw.get("y")
    if not is_number(x) or not is_number(y):
        return None
    if not math.isfinite(float(x)) or not math.isfinite(float(y)):
        return None
    frame_id = str(raw.get("frame_id") or "")
    return {"x": float(x), "y": float(y), "frame_id": frame_id}


def normalize_record(raw: Any) -> dict[str, Any] | None:
    """에이전트/내부 기록을 공유 스키마로 방어 정규화한다(request_id 없으면 None)."""
    if not isinstance(raw, Mapping):
        return None
    request_id = raw.get("request_id")
    if not isinstance(request_id, str) or not REQUEST_ID_PATTERN.fullmatch(request_id):
        return None
    status = str(raw.get("status") or STATUS_UNKNOWN).strip().lower()
    if status not in STATUSES:
        status = STATUS_UNKNOWN
    progress: int | None = None
    raw_progress = raw.get("progress")
    if is_number(raw_progress):
        progress = max(0, min(100, int(raw_progress)))
    created_at = _number(raw.get("created_at"))
    return {
        "request_id": request_id,
        "robot_id": str(raw.get("robot_id") or ""),
        "status": status,
        "message": str(raw.get("message") or ""),
        "target": _target(raw.get("target")),
        "geometry_id": str(raw.get("geometry_id") or ""),
        "execution_started": bool(raw.get("execution_started")),
        "cancel_confirmed": bool(raw.get("cancel_confirmed")),
        "reason": str(raw.get("reason") or ""),
        "progress": progress,
        "created_at": created_at,
        "updated_at": _number(raw.get("updated_at"), created_at),
        "can_submit": bool(raw.get("can_submit")),
    }


def targets_match(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None) -> bool:
    """두 목표(target)가 같은 좌표/프레임인지 확인한다(반올림 오차 1e-6 허용)."""
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return False
    if str(first.get("frame_id") or "") != str(second.get("frame_id") or ""):
        return False
    for axis in ("x", "y"):
        left, right = first.get(axis), second.get(axis)
        if not is_number(left) or not is_number(right):
            return False
        if abs(float(left) - float(right)) > 1e-6:
            return False
    return True


def records_compatible(first: Mapping[str, Any] | None, second: Mapping[str, Any] | None) -> bool:
    """전이를 허용해도 되는 같은 요청인지(request_id/target/geometry) 확인한다."""
    if not isinstance(first, Mapping) or not isinstance(second, Mapping):
        return False
    if str(first.get("request_id") or "") != str(second.get("request_id") or ""):
        return False
    if str(first.get("robot_id") or "") != str(second.get("robot_id") or ""):
        return False
    if not targets_match(first.get("target"), second.get("target")):
        return False
    left = str(first.get("geometry_id") or "")
    right = str(second.get("geometry_id") or "")
    return left == right


def adopt_external_record(record: dict[str, Any] | None, *, now: float) -> dict[str, Any] | None:
    """대시보드가 모르는(재시작 등) 에이전트 기록을 보수적으로 채택한다.

    non-terminal 기록이 TTL 보다 오래됐으면 running/planning 을 주장하지 않고
    unknown 으로 낮춘다. terminal 기록은 그대로 둔다(도달/실패를 새로 만들지 않음).
    """
    if record is None:
        return None
    adopted = dict(record)
    if adopted.get("status") not in LEASE_STATUSES:
        return adopted
    updated_at = _number(adopted.get("updated_at"), _number(adopted.get("created_at")))
    if updated_at <= 0.0 or float(now) - updated_at > AGENT_RECORD_TTL_SEC:
        adopted["status"] = STATUS_UNKNOWN
        adopted["reason"] = REASON_STALE_AGENT_RECORD
        adopted["execution_started"] = False
        adopted["message"] = str(
            adopted.get("message") or "에이전트 진행 상태가 오래되어 확인이 필요합니다"
        )
    return adopted


def is_verified_not_executed(record: Mapping[str, Any] | None) -> bool:
    """에이전트가 '실행되지 않음'을 명시한 실패 기록인지 확인한다."""
    if not isinstance(record, Mapping):
        return False
    return (
        str(record.get("status") or "") == STATUS_FAILED
        and not bool(record.get("execution_started"))
        and str(record.get("reason") or "") == REASON_NAVIGATION_NOT_EXECUTED
    )


def merge_records(
    local: dict[str, Any] | None, agent: dict[str, Any] | None
) -> dict[str, Any] | None:
    """같은 request/target 의 권위 있는 에이전트 기록을 로컬 기록에 병합한다."""
    if agent is None:
        return local
    if local is None:
        return dict(agent)
    if not records_compatible(local, agent):
        return local

    merged = dict(local)
    local_terminal = local.get("status") in TERMINAL_STATUSES
    agent_terminal = agent.get("status") in TERMINAL_STATUSES
    if local_terminal or (
        local.get("status") in {STATUS_UNKNOWN, STATUS_CANCEL_REQUESTED} and not agent_terminal
    ):
        return merged
    local_updated = _number(local.get("updated_at"), _number(local.get("created_at")))
    agent_updated = _number(agent.get("updated_at"), _number(agent.get("created_at")))
    # Feedback/cancel-ACK timestamps are not navigation outcome authority.
    # A terminal result for this immutable request/robot/target wins over them.
    if agent_terminal and (
        agent_updated <= 0.0 or agent_updated < _number(agent.get("created_at"))
    ):
        return merged
    for key in (
        "status",
        "message",
        "reason",
        "progress",
        "execution_started",
        "cancel_confirmed",
        "robot_id",
    ):
        value = agent.get(key)
        if value is not None and value != "":
            merged[key] = value
        elif key in ("status", "message"):
            merged[key] = value
    if merged.get("status") in (STATUS_QUEUED, STATUS_PLANNING):
        # planning/queued 는 실제 guarded 주행 효과의 증거가 아니다.
        merged["execution_started"] = False
    merged["updated_at"] = max(local_updated, agent_updated)
    return merged


def can_submit(record: Mapping[str, Any] | None, lease_held: bool) -> bool:
    """새 지도 목표를 제출할 수 있는지(다른 lease 가 없으면 True)."""
    return not lease_held


def agent_identity_from_state_json(state_json: Any) -> dict[str, Any] | None:
    """QueryState state_json 에서 로봇 ID/주행 프레임/진행 기록을 뽑는다."""
    if not isinstance(state_json, str):
        return None
    try:
        state = json.loads(state_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(state, Mapping):
        return None
    agent_id = str(state.get("agent_id") or "")
    if not agent_id:
        return None
    identity: dict[str, Any] = {
        "agent_id": agent_id,
        "navigation_frame": str(state.get("navigation_frame") or ""),
        "map_navigation_request": normalize_record(state.get("map_navigation_request")),
    }
    return identity


def evidence_from_skill_results(
    skill_results: Any,
    *,
    request_id: str,
    target: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """실제 navigate_to SkillResult.result_json 의 map_goal_request 증거만 인정한다."""
    if not isinstance(skill_results, Sequence):
        return None
    for item in skill_results:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("skill_name") or "")
        validation = name == "map_goal_validation"
        if name != "navigate_to" and not validation:
            continue
        if validation and item.get("code") == 0:
            continue
        raw = item.get("result_json")
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                continue
        else:
            data = raw
        if not isinstance(data, Mapping):
            continue
        proof = data.get("map_goal_rejection" if validation else "map_goal_request")
        if isinstance(proof, Mapping) and "target" not in proof:
            # NavigateToSkill emits a flat execution proof, not a status record.
            target_proof = _target(proof)
            if target_proof is None:
                continue
            timestamp = _number(proof.get("recorded_at"))
            proof = {
                **proof,
                "target": target_proof,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        record = normalize_record(proof)
        if record is None or record["request_id"] != request_id:
            continue
        if validation and not is_verified_not_executed(record):
            continue
        if not targets_match(record.get("target"), target):
            continue
        return record
    return None
