"""로그 패널 도메인 — ROS 로그 정규화, 링 버퍼, 필터, 직렬화.

주의: ROS2 / rclpy / generated message import 금지. 입력은 순수 Python 값만 받는다.
어댑터(`adapters/ros_adapter.py`)가 `/rosout` 메시지를 풀어 이 모듈에 넣고,
HTTP 계층은 여기서 만든 스냅샷/텍스트를 그대로 응답한다.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

#: rcl_interfaces/msg/Log.level 값 → 표시 이름(ROS 규약과 동일).
LEVEL_NAMES = {10: "debug", 20: "info", 30: "warn", 40: "error", 50: "fatal"}
#: 심각도 비교용 순위. 필터는 "이 레벨 이상"으로 해석한다.
LEVEL_RANK = {"debug": 10, "info": 20, "warn": 30, "error": 40, "fatal": 50}
#: 사용자가 warning/err 처럼 적어도 받아 주기 위한 별칭.
LEVEL_ALIASES = {
    "debug": "debug",
    "info": "info",
    "warn": "warn",
    "warning": "warn",
    "error": "error",
    "err": "error",
    "fatal": "fatal",
    "critical": "fatal",
}
#: 버퍼 기본 크기. 로봇에서 오래 켜 두어도 메모리가 무한히 늘지 않게 상한을 둔다.
DEFAULT_CAPACITY = 500
MIN_CAPACITY = 50
MAX_CAPACITY = 20000
#: 한 번에 돌려주는 최대 줄 수. 로그 폭주 시 응답 크기를 제한한다.
DEFAULT_LIMIT = 200
MAX_LIMIT = 2000


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def level_name(level: Any) -> str:
    """로그 레벨 값을 표시 이름으로 바꾼다(범위 밖 값은 가장 가까운 구간으로)."""
    try:
        value = int(level)
    except (TypeError, ValueError):
        return "info"
    for threshold in (50, 40, 30, 20):
        if value >= threshold:
            return LEVEL_NAMES[threshold]
    return "debug"


def normalize_min_level(raw: Any) -> str | None:
    """최소 레벨 필터를 표준 이름으로 정규화한다. 빈 값/알 수 없는 값은 None(전체)."""
    if raw is None:
        return None
    return LEVEL_ALIASES.get(str(raw).strip().lower())


def format_stamp(stamp_sec: Any) -> str:
    """초 단위 타임스탬프를 밀리초까지 담은 UTC ISO 문자열로 만든다."""
    try:
        moment = datetime.fromtimestamp(float(stamp_sec), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        moment = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond // 1000:03d}Z"


def normalize_entry(
    *,
    stamp_sec: Any = 0.0,
    level: Any = 20,
    node: Any = "",
    message: Any = "",
    file: Any = "",
    function: Any = "",
    line: Any = 0,
) -> dict[str, Any]:
    """`/rosout` 한 줄을 대시보드 표시용 딕셔너리로 정규화한다."""
    try:
        stamp = float(stamp_sec)
    except (TypeError, ValueError):
        stamp = 0.0
    try:
        line_no = int(line)
    except (TypeError, ValueError):
        line_no = 0
    return {
        "time": format_stamp(stamp),
        "stamp_sec": stamp,
        "level": level_name(level),
        "node": _text(node),
        "message": _text(message),
        "file": _text(file),
        "function": _text(function),
        "line": line_no,
    }


def matches_filters(entry: Any, *, min_level: Any = None, query: Any = None) -> bool:
    """레벨(이상)·검색어 조건을 만족하는 로그 줄인지 판정한다."""
    if not isinstance(entry, dict):
        return False
    canonical = normalize_min_level(min_level)
    if canonical is not None:
        entry_rank = LEVEL_RANK.get(_text(entry.get("level")), 0)
        if entry_rank < LEVEL_RANK[canonical]:
            return False
    needle = _text(query).strip().lower()
    if needle:
        haystack = f"{_text(entry.get('node'))} {_text(entry.get('message'))}".lower()
        if needle not in haystack:
            return False
    return True


def _coerce_capacity(raw: Any) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_CAPACITY
    return max(MIN_CAPACITY, min(MAX_CAPACITY, value))


def coerce_limit(raw: Any, default: int = DEFAULT_LIMIT) -> int:
    """응답 줄 수 제한을 안전 범위로 정규화한다(잘못된 값은 기본값)."""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, MAX_LIMIT)


class LogBuffer:
    """최근 로그를 고정 크기로 보관하는 링 버퍼(구독 콜백/HTTP 스레드 공용)."""

    def __init__(self, capacity: Any = DEFAULT_CAPACITY) -> None:
        self._capacity = _coerce_capacity(capacity)
        self._entries: deque[dict[str, Any]] = deque(maxlen=self._capacity)
        self._lock = threading.Lock()
        self._seq = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def append(self, entry: dict[str, Any]) -> dict[str, Any]:
        """정규화된 로그 한 줄을 추가하고 순번을 붙여 반환한다."""
        with self._lock:
            self._seq += 1
            stored = dict(entry)
            stored["seq"] = self._seq
            self._entries.append(stored)
            return stored

    def snapshot(self) -> list[dict[str, Any]]:
        """보관 중인 로그의 복사본을 오래된 순으로 반환한다."""
        with self._lock:
            return [dict(entry) for entry in self._entries]


def build_log_payload(
    entries: Sequence[Any] | None,
    *,
    capacity: Any = DEFAULT_CAPACITY,
    topic: str = "",
    min_level: Any = None,
    query: Any = None,
    limit: Any = None,
) -> dict[str, Any]:
    """로그 스냅샷을 HTTP 응답용 딕셔너리로 만든다(필터·개수·잘림 여부 포함)."""
    stored = [entry for entry in (entries or []) if isinstance(entry, dict)]
    limit_value = coerce_limit(limit)
    levels = {name: 0 for name in LEVEL_RANK}
    for entry in stored:
        name = _text(entry.get("level")) or "info"
        levels[name] = levels.get(name, 0) + 1

    selected = [
        entry for entry in stored if matches_filters(entry, min_level=min_level, query=query)
    ]
    truncated = len(selected) > limit_value
    if truncated:
        selected = selected[-limit_value:]

    return {
        "available": True,
        "topic": topic,
        "capacity": _coerce_capacity(capacity),
        "stored": len(stored),
        "returned": len(selected),
        "limit": limit_value,
        "truncated": truncated,
        "level": normalize_min_level(min_level),
        "query": _text(query).strip() or None,
        "levels": levels,
        "nodes": sorted({_text(entry.get("node")) for entry in selected if entry.get("node")}),
        "timestamp": format_stamp(time.time()),
        "entries": selected,
    }


def unavailable_logs(error: str = "") -> dict[str, Any]:
    """로그 버퍼가 없거나 조회에 실패했을 때의 안전한 기본 응답."""
    payload = build_log_payload([], limit=DEFAULT_LIMIT)
    payload["available"] = False
    payload["levels"] = {name: 0 for name in LEVEL_RANK}
    if error:
        payload["error"] = error
    return payload


def _source_ref(entry: dict[str, Any]) -> str:
    file_name = _text(entry.get("file"))
    line = entry.get("line") or 0
    if file_name:
        return f"{file_name}:{line}" if line else file_name
    return _text(entry.get("function"))


def format_log_text(entries: Sequence[Any] | None, *, header: str = "") -> str:
    """로그를 사람이 읽는 텍스트(.log)로 직렬화한다."""
    lines: list[str] = []
    if header:
        lines.append(f"# {header}")
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        level = _text(entry.get("level")) or "info"
        line = (
            f"{_text(entry.get('time'))} {level.upper():<5} "
            f"{_text(entry.get('node'))}: {_text(entry.get('message'))}"
        )
        source = _source_ref(entry)
        if source:
            line += f" ({source})"
        lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def format_log_jsonl(entries: Sequence[Any] | None) -> str:
    """로그를 JSON Lines(한 줄 = 한 객체)로 직렬화한다(기계 분석용)."""
    lines = [
        json.dumps(entry, ensure_ascii=False) for entry in entries or [] if isinstance(entry, dict)
    ]
    return "".join(line + "\n" for line in lines)
