"""마지막으로 파악한 로봇 위치(마지막 위치)를 RAG 에 유지한다.

AMCL 초기 위치를 잃었을 때(네비게이션 재기동 등) "마지막 위치를 초기 위치로"
요청을 처리하려면 로봇이 마지막으로 파악한 map 프레임 pose(x, y, yaw)가 남아 있어야
한다. `navigate_to` 가 남기는 `navigated_coordinate` 는 **목적지** 좌표이고 yaw 가
없으므로 마지막 pose 로 쓸 수 없다. 그래서 전용 레코드를 `type="last_pose"` 로
**1건만 upsert** 해서 유지한다(항목이 늘어나면 RAG 검색이 오염되고 스캔도 느려진다).

last_pose 레코드는 장소 지식이 아니므로 `semantic._FWD_SKIP_TYPES` 와
`rag._NON_LOCATION_TYPES` 에서 제외한다. 그렇게 하지 않으면 텍스트 앞의 "마지막"이
장소명으로 추출되어 위치 라우팅이 "마지막 위치"를 장소명으로 오인한다.
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

LAST_POSE_TYPE = "last_pose"
#: 사용자가 "마지막 위치"를 가리킬 때 쓰는 표현(정규화 후 비교: 공백 제거 + 소문자).
LAST_POSE_DISPLAY_NAME = "마지막 위치"
LAST_POSE_ALIASES: frozenset[str] = frozenset(
    {
        "마지막위치",
        "마지막장소",
        "마지막지점",
        "마지막포즈",
        "마지막자리",
        "마지막으로있던곳",
        "마지막으로있던위치",
        "직전위치",
        "직전장소",
        "방금전위치",
        "lastpose",
        "lastposition",
    }
)

_NUM = r"(-?\d+(?:\.\d+)?)"
_X_RE = re.compile(r"\bx\s*[:=]\s*" + _NUM)
_Y_RE = re.compile(r"\by\s*[:=]\s*" + _NUM)
_YAW_RE = re.compile(r"\byaw\s*[:=]\s*" + _NUM)


def is_last_pose_request(name: str) -> bool:
    """장소명 자리에 '마지막 위치'류 표현이 왔는지 판정한다."""
    normalized = re.sub(r"\s+", "", str(name or "")).lower()
    return normalized in LAST_POSE_ALIASES


def format_last_pose_text(x: float, y: float, yaw: float, place_name: str = "") -> str:
    """RAG 에 저장할 사람이 읽는 문장을 만든다."""
    suffix = f" ({place_name} 부근)" if place_name else ""
    return (
        f"{LAST_POSE_DISPLAY_NAME}: x={x:.2f}, y={y:.2f}, "
        f"yaw={yaw:.2f}rad ({math.degrees(yaw):.1f}°){suffix}"
    )


def _vector_store(memory: Any) -> Any | None:
    store = getattr(memory, "_vector_store", None)
    if store is None or not hasattr(store, "list_entries"):
        return None
    return store


def _entry_pose(entry: dict[str, Any]) -> dict[str, Any] | None:
    """last_pose 항목에서 pose 를 뽑는다(metadata 우선, 없으면 텍스트 파싱)."""
    meta = entry.get("metadata", {})
    meta = meta if isinstance(meta, dict) else {}
    text = str(entry.get("text", ""))

    def _num(key: str, pattern: re.Pattern[str], default: float | None) -> float | None:
        raw = meta.get(key)
        if raw is None:
            match = pattern.search(text)
            raw = match.group(1) if match else None
        if raw is None:
            return default
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return default
        return value if math.isfinite(value) else default

    x = _num("x", _X_RE, None)
    y = _num("y", _Y_RE, None)
    if x is None or y is None:
        return None
    yaw = _num("yaw", _YAW_RE, 0.0)
    return {
        "x": x,
        "y": y,
        "yaw": yaw if yaw is not None else 0.0,
        "frame": str(meta.get("frame") or "map"),
        "near_place": str(meta.get("near_place") or ""),
        "timestamp": str(meta.get("timestamp") or entry.get("timestamp") or ""),
        "id": entry.get("id"),
    }


def read_last_pose(memory: Any) -> dict[str, Any] | None:
    """저장된 마지막 위치를 읽는다. 없으면 None."""
    store = _vector_store(memory)
    if store is None:
        return None
    try:
        entries = store.list_entries()
    except Exception as exc:  # noqa: BLE001
        logger.warning("마지막 위치 조회 실패 — 벡터스토어 목록 오류: %s", exc)
        return None

    for entry in reversed(entries or []):
        meta = entry.get("metadata", {})
        if not isinstance(meta, dict) or meta.get("type") != LAST_POSE_TYPE:
            continue
        pose = _entry_pose(entry)
        if pose is not None:
            return pose
    return None


def remember_last_pose(
    memory: Any,
    *,
    x: float,
    y: float,
    yaw: float,
    frame: str = "map",
    near_place: str = "",
    timestamp: str | None = None,
) -> bool:
    """마지막 위치를 1건으로 유지하며 저장한다(기존 last_pose 항목은 먼저 제거).

    임베더/벡터스토어가 없으면(예: RAG 비활성) 조용히 False 를 돌려준다. 호출부는
    이 실패로 로봇 동작을 막지 않는다.
    """
    if not all(math.isfinite(float(v)) for v in (x, y, yaw)):
        return False
    store = _vector_store(memory)
    if store is None or not hasattr(memory, "add_knowledge"):
        return False

    stale_ids: list[str] = []
    try:
        for entry in store.list_entries():
            meta = entry.get("metadata", {})
            if not isinstance(meta, dict) or meta.get("type") != LAST_POSE_TYPE:
                continue
            if entry.get("id") is not None:
                stale_ids.append(str(entry["id"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("마지막 위치 기존 항목 조회 실패(계속 진행): %s", exc)

    if stale_ids:
        try:
            store.delete_entries(ids=stale_ids)
        except Exception as exc:  # noqa: BLE001
            logger.warning("마지막 위치 기존 항목 삭제 실패(계속 진행): %s", exc)

    stamp = timestamp or datetime.now().isoformat()
    metadata: dict[str, Any] = {
        "type": LAST_POSE_TYPE,
        "x": round(float(x), 3),
        "y": round(float(y), 3),
        "yaw": round(float(yaw), 4),
        "frame": str(frame or "map"),
        # 'place_name' 은 쓰지 않는다 — semantic 의 장소명 키로 읽혀 장소 지식으로 오인된다.
        "near_place": str(near_place or ""),
        "timestamp": stamp,
    }
    text = format_last_pose_text(float(x), float(y), float(yaw), near_place)
    try:
        return bool(memory.add_knowledge(text, metadata))
    except Exception as exc:  # noqa: BLE001
        logger.error("마지막 위치 저장 실패: %s", exc)
        return False
