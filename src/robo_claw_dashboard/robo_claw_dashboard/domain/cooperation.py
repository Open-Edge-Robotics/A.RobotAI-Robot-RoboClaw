"""협동 관측 저널 도메인 — 방어적 정규화, 커서 필터, 파일 내보내기.

에이전트가 QueryState 로 주는 ``cooperation`` 객체는 신뢰 경계 밖 입력이다. 여기서
타입/크기를 정규화하고 시크릿과 긴 문자열을 잘라 대시보드 응답과 내보내기에 그대로
흘러가지 않게 한다. 관측은 로컬 기준이며 상대 로봇의 실제 동작을 보증하지 않는다.
정규화 결과는 아래 명시한 키만 담는 화이트리스트이며, 원본의 사설 필드는 복사하지 않는다.

주의: rclpy / ROS 메시지 import 금지. 입력은 순수 dict/str 이고, 대시보드는
``/api/status`` 가 이미 받은 QueryState 응답만 캐시한다(추가 ROS 질의 없음).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .config_view import MASK, SECRET_HINTS

#: 응답에 싣는 최근 이벤트/태스크 상한(에이전트 저널 상한과 같은 값).
MAX_EVENTS = 100
MAX_TASKS = 50
#: 표시용 문자열 상한.
MAX_TEXT_CHARS = 96
MAX_EVENT_TYPE_CHARS = 48
MAX_CONTENT_CHARS = 512
#: details 는 화면에서 펼쳐 보는 값이므로 깊이/항목/키/문자 수를 모두 제한한다.
MAX_DETAIL_ITEMS = 20
MAX_DETAIL_DEPTH = 3
MAX_DETAIL_CHARS = 200
MAX_KEY_CHARS = 64
#: 한 번에 돌려주는 이벤트 수.
DEFAULT_LIMIT = 100
MAX_LIMIT = 200
#: 이 시간(ms)보다 오래된 캐시는 화면에서 "오래된 관측"으로 표시한다.
STALE_AFTER_MS = 15_000

KNOWN_STATUSES = frozenset(
    {
        "queued",
        "received",
        "running",
        "completed",
        "failed",
        "denied",
        "partial",
        "unknown",
        #: 로컬 정리/시연 진행 상태. 완료를 뜻하지 않으며 화면에서도 중립/경고로만 표시한다.
        "motion_requested",
        "waiting_for_peer",
        "cancel_requested",
        "reported_to_maestro",
    }
)
#: 결과가 상대 로봇 보고에서 온 것임을 뜻하는 origin 값들.
OBSERVED_ORIGINS = frozenset({"peer_report", "peer-reported", "peer", "remote", "observed"})
#: 스냅샷 활동 출처. 이 밖의 값은 표시하지 않는다(저널 사설 값을 화면으로 흘리지 않음).
ACTIVITY_SOURCES = frozenset({"tidy_home", "cleanup_demo", "autonomous_cooperate"})
#: activity 를 투영한 뷰 태스크의 source. 실제 원격 실행 태스크가 아님을 표시한다.
ACTIVITY_TASK_SOURCE = "local_activity"

#: 협동 관측에서만 추가로 숨기는 키 조각(인증 헤더 계열).
_COOPERATION_SECRET_HINTS = SECRET_HINTS + (
    "auth",
    "authorization",
    "cookie",
    "bearer",
    "privatekey",
)

#: ``http_readonly_token=...``, ``authorization: ...`` 처럼 본문에 섞인 시크릿을 마스킹한다.
_INLINE_SECRET_RE = re.compile(
    r"(?i)([A-Za-z0-9_\-]*(?:authorization|private[_-]?key|token|secret|password|passwd|api[_-]?key"
    r"|bearer|cookie|auth)[A-Za-z0-9_\-]*)(\s*[:=]\s*)(?!bearer\b)\S+"
)
#: ``Authorization: Bearer <값>`` 형태의 자격 증명도 값만 마스킹한다.
_BEARER_RE = re.compile(r"(?i)\b(bearer)(\s+)([A-Za-z0-9._~+/\-]{4,}=*)")


def _text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    """값을 표시용 문자열로 바꾼다(초과분은 말줄임표로 잘라 길이를 지킨다)."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if limit > 0 and len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def redact_text(text: str) -> str:
    """문자열 안에 섞인 ``key=value``/``Bearer <값>`` 형태 시크릿을 마스킹한다."""
    masked = _BEARER_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{MASK}", text)
    return _INLINE_SECRET_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{MASK}", masked)


def _is_secret_key(key: str) -> bool:
    lowered = str(key).lower()
    return any(hint in lowered for hint in _COOPERATION_SECRET_HINTS)


def _sanitize_value(value: Any, depth: int) -> Any:
    if depth >= MAX_DETAIL_DEPTH:
        # 화면에서 펼치지 않는 깊은 구조는 값을 노출하지 않고 생략한다.
        return MASK
    if isinstance(value, Mapping):
        return sanitize_details(value, depth=depth)
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, depth + 1) for item in list(value)[:MAX_DETAIL_ITEMS]]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return redact_text(_text(value, MAX_DETAIL_CHARS))


def sanitize_details(details: Any, *, depth: int = 0) -> dict[str, Any]:
    """``details`` 를 화면/내보내기용으로 정리한다(시크릿 마스킹 + 크기 상한)."""
    if not isinstance(details, Mapping) or depth > MAX_DETAIL_DEPTH:
        return {}
    sanitized: dict[str, Any] = {}
    for index, (key, value) in enumerate(details.items()):
        if index >= MAX_DETAIL_ITEMS:
            break
        name = _text(key, MAX_KEY_CHARS)
        if not name:
            continue
        if _is_secret_key(name):
            sanitized[name] = MASK
            continue
        sanitized[name] = _sanitize_value(value, depth + 1)
    return sanitized


def normalize_status(raw: Any, *, allow_blank: bool = False) -> str:
    """알려진 상태로 정규화한다.

    session/decision/policy 이벤트는 상태 개념이 없어 빈 문자열로 오므로 ``allow_blank``
    로 빈 값을 보존한다(화면에서 상태 칩을 그리지 않기 위함).
    """
    status = _text(raw, MAX_TEXT_CHARS).strip().lower()
    if not status:
        return "" if allow_blank else "unknown"
    return status if status in KNOWN_STATUSES else "unknown"


def _observed_flag(raw: Mapping[str, Any]) -> bool | None:
    """결과의 출처(로컬 실행 vs 상대 보고) 메타데이터를 읽는다. 없으면 None(모호)."""
    value = raw.get("observed")
    if isinstance(value, bool):
        return value
    details = raw.get("details")
    if isinstance(details, Mapping) and isinstance(details.get("observed"), bool):
        return details["observed"]
    origin = raw.get("origin")
    if isinstance(origin, str) and origin.strip().lower() in OBSERVED_ORIGINS:
        return True
    return None


def normalize_activity_source(raw: Any) -> str:
    """스냅샷 활동 출처를 화이트리스트로 제한한다(모르는 값은 빈 문자열)."""
    source = _text(raw, MAX_TEXT_CHARS).strip().lower()
    return source if source in ACTIVITY_SOURCES else ""


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def normalize_activity(raw: Any) -> dict[str, Any] | None:
    """백엔드 ``activity`` 현재 상태를 화면/내보내기용 화이트리스트로 정규화한다.

    ``activity`` 는 로컬 정리/시연의 현재 상태이며 ``journal.tasks`` 가 비어 있어도 이 값으로
    뷰 태스크를 투영해 보여준다. 모르는 키는 버리고 문자열 길이/시크릿/숫자 유한성을 적용한다.
    """
    if not isinstance(raw, Mapping):
        return None
    source = normalize_activity_source(raw.get("source"))
    run_id = _text(raw.get("run_id"))
    task_id = _text(raw.get("task_id"))
    session_id = _text(raw.get("session_id"))
    goal = redact_text(_text(raw.get("goal"), MAX_CONTENT_CHARS))
    phase = _text(raw.get("phase"))
    status = normalize_status(raw.get("status"), allow_blank=True)
    if not any((source, run_id, task_id, session_id, goal, phase, status)):
        return None
    return {
        "source": source,
        "run_id": run_id,
        "session_id": session_id,
        "robot_id": _text(raw.get("robot_id")),
        "task_id": task_id,
        "goal": goal,
        "phase": phase,
        "phase_label": redact_text(_text(raw.get("phase_label"))),
        "place": redact_text(_text(raw.get("place"))),
        "target": redact_text(_text(raw.get("target"))),
        "peer": _text(raw.get("peer")),
        "transport": _text(raw.get("transport")),
        "status": status,
        "task_status": normalize_status(raw.get("task_status"), allow_blank=True),
        "result": redact_text(_text(raw.get("result"), MAX_CONTENT_CHARS)),
        "failure_reason": redact_text(_text(raw.get("failure_reason"), MAX_CONTENT_CHARS)),
        "observed": _optional_bool(raw.get("observed")),
        "physical_cleanup_confirmed": raw.get("physical_cleanup_confirmed") is True,
        "motion_completion_confirmed": raw.get("motion_completion_confirmed") is True,
        "active": raw.get("active") is True,
        "started_at": _number(raw.get("started_at")),
        "updated_at": _number(raw.get("updated_at")),
        "finished_at": _number(raw.get("finished_at")),
        "duration_ms": _number(raw.get("duration_ms")),
    }


def _activity_status_value(raw: Any) -> str:
    """투영 태스크 상태로 쓸 수 있는 유효 상태만 돌려준다(unknown/빈 값은 제외)."""
    status = normalize_status(raw, allow_blank=True)
    return status if status and status != "unknown" else ""


def _activity_task_status(activity: Mapping[str, Any]) -> str:
    """activity 의 태스크 표시 상태를 정한다(task_status 우선, 유효 status fallback)."""
    explicit = _activity_status_value(activity.get("task_status"))
    if explicit:
        return explicit
    status = _activity_status_value(activity.get("status"))
    if status:
        # waiting_for_peer 는 실제로 대기 중일 때만 쓴다(종료 후 미확인 결과를 대기로 표시 금지).
        if status == "waiting_for_peer" and activity.get("active") is not True:
            return "unknown"
        return status
    if activity.get("failure_reason"):
        return "failed"
    if activity.get("active") is True:
        return "running"
    if (
        activity.get("physical_cleanup_confirmed") is True
        and activity.get("motion_completion_confirmed") is not True
    ):
        return "partial"
    return "unknown"


def _project_activity_task(activity: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """현재 activity 를 "로컬 활동" 뷰 태스크로 투영한다(원격 실행 태스크 아님)."""
    if not isinstance(activity, Mapping):
        return None
    run_id = _text(activity.get("run_id"))
    task_id = _text(activity.get("task_id")) or run_id
    if not task_id:
        return None
    description = _text(activity.get("goal"), MAX_CONTENT_CHARS)
    for value in (_text(activity.get("place")), _text(activity.get("target"))):
        if value and value not in description:
            description = f"{description} · {value}" if description else value
    return {
        "task_id": task_id,
        "session_id": _text(activity.get("session_id")) or run_id,
        "peer_id": _text(activity.get("peer")),
        "description": description,
        "status": _activity_task_status(activity),
        "observed": _optional_bool(activity.get("observed")),
        "delivery_confirmed": False,
        "outbound_message_id": "",
        "created_at": _number(activity.get("started_at")),
        "updated_at": _number(activity.get("updated_at")),
        "result": _text(activity.get("result"), MAX_CONTENT_CHARS),
        "source": ACTIVITY_TASK_SOURCE,
        "projected": True,
        "activity_source": _text(activity.get("source")),
        "physical_cleanup_confirmed": activity.get("physical_cleanup_confirmed") is True,
        "motion_completion_confirmed": activity.get("motion_completion_confirmed") is True,
    }


def normalize_event(raw: Any) -> dict[str, Any] | None:
    """이벤트 한 건을 정규화한다. sequence 가 없으면 순서를 보장할 수 없어 버린다."""
    if not isinstance(raw, Mapping):
        return None
    sequence = _int(raw.get("sequence"))
    if sequence is None or sequence < 1:
        return None
    return {
        "sequence": sequence,
        "event_type": _text(raw.get("event_type"), MAX_EVENT_TYPE_CHARS).strip() or "unknown",
        "timestamp": _number(raw.get("timestamp")),
        "robot_id": _text(raw.get("robot_id")),
        "peer_id": _text(raw.get("peer_id")),
        "session_id": _text(raw.get("session_id")),
        "task_id": _text(raw.get("task_id")),
        "message_id": _text(raw.get("message_id")),
        "reply_to": _text(raw.get("reply_to")),
        "status": normalize_status(raw.get("status"), allow_blank=True),
        "observed": _observed_flag(raw),
        "delivery_confirmed": raw.get("delivery_confirmed") is True,
        "outbound_message_id": _text(raw.get("outbound_message_id")),
        "content": redact_text(_text(raw.get("content"), MAX_CONTENT_CHARS)),
        "details": sanitize_details(raw.get("details")),
    }


def normalize_task(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    task_id = _text(raw.get("task_id"))
    if not task_id:
        return None
    return {
        "task_id": task_id,
        "session_id": _text(raw.get("session_id")),
        "peer_id": _text(raw.get("peer_id")),
        "description": redact_text(_text(raw.get("description"), MAX_CONTENT_CHARS)),
        "status": normalize_status(raw.get("status")),
        "observed": _observed_flag(raw),
        "delivery_confirmed": raw.get("delivery_confirmed") is True,
        "outbound_message_id": _text(raw.get("outbound_message_id")),
        "created_at": _number(raw.get("created_at")),
        "updated_at": _number(raw.get("updated_at")),
        "result": redact_text(_text(raw.get("result"), MAX_CONTENT_CHARS)),
        "source": _text(raw.get("source")),
        "projected": raw.get("projected") is True,
        "activity_source": normalize_activity_source(raw.get("activity_source")),
        "physical_cleanup_confirmed": raw.get("physical_cleanup_confirmed") is True,
        "motion_completion_confirmed": raw.get("motion_completion_confirmed") is True,
    }


def _normalize_persistence(raw: Any) -> dict[str, Any]:
    """저장 상태를 정규화한다.

    ``reported`` 는 저널이 저장 상태를 실제로 보고했는지 여부다. 보고가 없으면
    화면이 "저장 비활성"이라고 단정하지 않도록 False 로 둔다.
    """
    if not isinstance(raw, Mapping):
        return {"enabled": False, "error": None, "reported": False}
    error = raw.get("error")
    error_text = ""
    if error is not None and error != "":
        error_text = redact_text(_text(error, MAX_CONTENT_CHARS))
    return {"enabled": bool(raw.get("enabled")), "error": error_text or None, "reported": True}


def unavailable_cooperation(reason: str = "") -> dict[str, Any]:
    """협동 관측 정보가 없을 때의 안전한 기본 응답."""
    return {
        "available": False,
        "enabled": False,
        "active": False,
        "activity_source": "",
        "direct_cooperation_enabled": False,
        "activity": None,
        "local_robot_id": "",
        "session_id": "",
        "goal": "",
        "stream_id": "",
        "events": [],
        "tasks": [],
        "next_cursor": 0,
        "oldest_cursor": 0,
        "dropped_events": 0,
        "invalid_events": 0,
        "persistence": {"enabled": False, "error": None, "reported": False},
        "error": _text(reason, MAX_CONTENT_CHARS) or "협동 관측 정보를 사용할 수 없습니다",
    }


def _normalize_events(raw: Any) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return [], 0
    events: list[dict[str, Any]] = []
    invalid = 0
    for item in raw:
        normalized = normalize_event(item)
        if normalized is None:
            invalid += 1
            continue
        events.append(normalized)
    events.sort(key=lambda item: item["sequence"])
    return events, invalid


def _normalize_tasks(raw: Any, activity: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        tasks: list[dict[str, Any]] = []
    else:
        tasks = [task for task in (normalize_task(item) for item in raw) if task is not None]
    projected = _project_activity_task(activity)
    if projected is not None and all(task["task_id"] != projected["task_id"] for task in tasks):
        # 저널 태스크 ID 와 겹치면 원래 저널 태스크를 유지하고 투영을 건너뛴다.
        tasks.append(projected)
    tasks.sort(key=lambda item: item["updated_at"], reverse=True)
    return tasks[:MAX_TASKS]


def normalize_cooperation(raw: Any) -> dict[str, Any]:
    """에이전트 저널 스냅샷을 대시보드 표시용 구조로 정규화한다.

    ``activity_source``/``direct_cooperation_enabled`` 는 로컬 정리/시연 관측과 직접 협동을
    구분하기 위한 화이트리스트 메타데이터다(모르는 출처는 빈 문자열로 버린다).
    ``journal.tasks`` 가 비어 있어도 ``activity`` 가 있으면 "로컬 활동" 뷰 태스크를 투영한다.
    """
    if not isinstance(raw, Mapping) or raw.get("available") is not True:
        reason = raw.get("error") if isinstance(raw, Mapping) else ""
        return unavailable_cooperation(_text(reason, MAX_CONTENT_CHARS))

    enabled = raw.get("enabled") is True
    activity = normalize_activity(raw.get("activity"))
    events, invalid = _normalize_events(raw.get("events"))
    overflow = max(0, len(events) - MAX_EVENTS)
    if overflow:
        events = events[-MAX_EVENTS:]
    dropped = max(0, _int(raw.get("dropped_events")) or 0) + overflow
    oldest_cursor = max(0, _int(raw.get("oldest_cursor")) or 0)
    next_cursor = max(0, _int(raw.get("next_cursor")) or 0)

    return {
        "available": True,
        #: Maestro 관제로 협동이 꺼지면 active 도 강제로 내린다(우회 없음).
        "enabled": enabled,
        "active": enabled and raw.get("active") is True,
        #: 로컬 정리/시연 관측은 enabled=true, direct_cooperation_enabled=false 로 올 수 있다.
        "activity_source": normalize_activity_source(raw.get("activity_source")),
        "direct_cooperation_enabled": raw.get("direct_cooperation_enabled") is True,
        "activity": activity,
        "local_robot_id": _text(raw.get("local_robot_id")),
        "session_id": _text(raw.get("session_id")),
        "goal": redact_text(_text(raw.get("goal"), MAX_CONTENT_CHARS)),
        "stream_id": _text(raw.get("stream_id")),
        "events": events,
        "tasks": _normalize_tasks(raw.get("tasks"), activity),
        "next_cursor": next_cursor,
        "oldest_cursor": oldest_cursor,
        "dropped_events": dropped,
        "invalid_events": invalid,
        "persistence": _normalize_persistence(raw.get("persistence")),
        "error": "",
    }


def coerce_cursor(raw: Any) -> int:
    """쿼리/서비스의 커서 값을 0 이상의 정수로 해석한다(형식 오류는 ValueError)."""
    if isinstance(raw, bool) or raw is None:
        raise ValueError("after 커서는 0 이상의 정수여야 합니다")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
    else:
        raise ValueError("after 커서는 0 이상의 정수여야 합니다")
    if value < 0:
        raise ValueError("after 커서는 0 이상의 정수여야 합니다")
    return value


def coerce_limit(raw: Any) -> int:
    """이벤트 개수 제한을 1..MAX_LIMIT 으로 정규화한다(형식 오류는 ValueError)."""
    if isinstance(raw, bool) or raw is None:
        raise ValueError("limit 은 1 이상의 정수여야 합니다")
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.strip().isdigit():
        value = int(raw.strip())
    else:
        raise ValueError("limit 은 1 이상의 정수여야 합니다")
    if value <= 0:
        raise ValueError("limit 은 1 이상의 정수여야 합니다")
    return min(value, MAX_LIMIT)


def slice_events(
    events: Sequence[Any] | None,
    *,
    after: int | None = None,
    limit: int = DEFAULT_LIMIT,
    oldest_cursor: int = 0,
) -> dict[str, Any]:
    """커서 이후 이벤트를 잘라 반환하고 유실 구간을 알린다.

    ``after`` 를 지정하면 "가장 오래된 미처리"부터 ``limit`` 건을 돌려준다(순차 페이징,
    중간 기록을 건너뛰지 않음). ``after`` 가 없으면 최근 ``limit`` 건을 돌려준다.
    ``after`` 가 보관 범위보다 앞서면 ``gap`` 으로 유실 구간을 알리고 보관된 가장 오래된
    이벤트부터 반환한다.
    """
    ordered = [item for item in (events or []) if isinstance(item, Mapping)]
    gap: dict[str, int] | None = None
    if after is not None and oldest_cursor > 0 and after < oldest_cursor - 1:
        gap = {"from": after + 1, "to": oldest_cursor - 1, "missed": oldest_cursor - 1 - after}
    window = [
        item for item in ordered if after is None or (_int(item.get("sequence")) or 0) > after
    ]
    truncated = len(window) > limit
    if truncated:
        # 커서가 있으면 오래된 것부터(순차), 없으면 최근 것부터(스냅샷).
        window = window[:limit] if after is not None else window[-limit:]
    return {"events": window, "gap": gap, "truncated": truncated}


def cooperation_payload(
    cached: Mapping[str, Any] | None,
    *,
    after: int | None = None,
    stream_id: str | None = None,
    limit: int | None = None,
    age_ms: int | None = None,
) -> dict[str, Any]:
    """캐시된 협동 저널로 ``/api/cooperation`` 응답을 만든다.

    ``after`` 는 "이 sequence 다음부터"를 뜻하며 응답의 ``cursor`` 를 다음 ``after`` 로
    넘기면 보관된 이벤트를 순서대로 끝까지 읽을 수 있다. ``after`` 가 없으면 최근
    ``limit`` 건만 돌려주는 스냅샷 모드다. ``stream_id`` 가 바뀌었으면 클라이언트에
    ``reset`` 을 알려 화면을 초기화하게 한다.
    """
    window_limit = DEFAULT_LIMIT if limit is None else coerce_limit(limit)
    if after is not None:
        after = coerce_cursor(after)

    if not isinstance(cached, Mapping) or cached.get("available") is not True:
        reason = cached.get("error") if isinstance(cached, Mapping) else ""
        payload = unavailable_cooperation(_text(reason, MAX_CONTENT_CHARS))
        payload.update(
            {
                "source": "status-cache",
                "events": [],
                "tasks": [],
                "returned": 0,
                "limit": window_limit,
                "truncated": False,
                "cursor": 0,
                "after": after,
                "gap": None,
                "reset": False,
                "stale": True,
                "age_ms": age_ms,
                "stale_after_ms": STALE_AFTER_MS,
            }
        )
        return payload

    cached_stream = _text(cached.get("stream_id"))
    requested_stream = _text(stream_id)
    reset = bool(requested_stream and cached_stream and requested_stream != cached_stream)
    effective_after = None if reset else after
    window = slice_events(
        cached.get("events"),
        after=effective_after,
        limit=window_limit,
        oldest_cursor=max(0, _int(cached.get("oldest_cursor")) or 0),
    )
    returned = window["events"]
    cursor = max(
        (int(item.get("sequence") or 0) for item in returned),
        default=effective_after or 0,
    )
    stale = age_ms is not None and age_ms > STALE_AFTER_MS

    return {
        "available": True,
        "enabled": bool(cached.get("enabled")),
        "active": bool(cached.get("active")),
        "activity_source": normalize_activity_source(cached.get("activity_source")),
        "direct_cooperation_enabled": cached.get("direct_cooperation_enabled") is True,
        "activity": (
            dict(cached.get("activity")) if isinstance(cached.get("activity"), Mapping) else None
        ),
        "local_robot_id": _text(cached.get("local_robot_id")),
        "session_id": _text(cached.get("session_id")),
        "goal": _text(cached.get("goal"), MAX_CONTENT_CHARS),
        "stream_id": cached_stream,
        "source": "status-cache",
        "events": returned,
        "tasks": [dict(item) for item in cached.get("tasks") or [] if isinstance(item, Mapping)],
        "returned": len(returned),
        "limit": window_limit,
        "truncated": window["truncated"],
        "cursor": cursor,
        "after": effective_after,
        "next_cursor": max(0, _int(cached.get("next_cursor")) or 0),
        "oldest_cursor": max(0, _int(cached.get("oldest_cursor")) or 0),
        "dropped_events": max(0, _int(cached.get("dropped_events")) or 0),
        "gap": window["gap"],
        "reset": reset,
        "stale": stale,
        "age_ms": age_ms,
        "stale_after_ms": STALE_AFTER_MS,
        "persistence": dict(cached.get("persistence") or {"enabled": False, "error": None}),
    }


def format_cooperation_jsonl(cached: Mapping[str, Any] | None) -> str:
    """최근 관측 이벤트를 JSON Lines 로 직렬화한다(시크릿 마스킹 유지)."""
    events = cached.get("events") if isinstance(cached, Mapping) else None
    lines: list[str] = []
    for item in events or []:
        record = normalize_event(item)
        if record is None:
            continue
        lines.append(json.dumps(record, ensure_ascii=False))
    return "".join(line + "\n" for line in lines)
