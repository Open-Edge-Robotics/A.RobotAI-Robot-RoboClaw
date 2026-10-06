from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

RECENCY_SCORE_EPSILON = 0.02


class VectorStore(Protocol):
    """RAG 지식 저장/검색용 최소 인터페이스."""

    def add(
        self,
        *,
        text: str,
        metadata: dict[str, Any],
        vector: list[float],
        timestamp: str,
        id: str | None = None,
    ) -> bool: ...

    def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        score_threshold: float,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...

    def count(self) -> int: ...

    def clear(self) -> None: ...

    def delete_entries(
        self,
        *,
        ids: list[str] | None = None,
        source_path: str = "",
        text_exact: str = "",
    ) -> int: ...

    # with_vectors=False 가 기본이다. 목록/스캔 용도(메타데이터·텍스트만 필요)가 압도적으로
    # 많은데 벡터까지 실어오면 원격 스토어에서 컬렉션 전체 크기의 페이로드를 매번 끌어와
    # 타임아웃을 유발한다. 벡터가 실제로 필요한 곳(reconcile, 재색인 복구)만 True 로 부른다.
    def list_entries(self, *, with_vectors: bool = False) -> list[dict[str, Any]]: ...

    def inspect(self) -> dict[str, Any]: ...


def _matches_delete_filter(
    entry: dict[str, Any],
    *,
    ids: set[str] | None = None,
    source_path: str = "",
    text_exact: str = "",
) -> bool:
    if ids and str(entry.get("id", "")) in ids:
        return True
    metadata = entry.get("metadata", {})
    if source_path and isinstance(metadata, dict):
        if str(metadata.get("source_path", "")) == source_path:
            return True
    if text_exact and str(entry.get("text", "")) == text_exact:
        return True
    return False


def _sort_by_score_and_recency(
    results: list[dict[str, Any]],
    *,
    score_epsilon: float = RECENCY_SCORE_EPSILON,
) -> list[dict[str, Any]]:
    if len(results) <= 1:
        return results

    score_sorted = sorted(
        results,
        key=lambda item: float(item.get("score", 0.0)),
        reverse=True,
    )
    sorted_results: list[dict[str, Any]] = []
    group: list[dict[str, Any]] = []
    group_top_score = 0.0

    for item in score_sorted:
        score = float(item.get("score", 0.0))
        if not group:
            group = [item]
            group_top_score = score
            continue
        if group_top_score - score <= score_epsilon:
            group.append(item)
            continue
        sorted_results.extend(_sort_recency_group(group))
        group = [item]
        group_top_score = score

    sorted_results.extend(_sort_recency_group(group))
    return sorted_results


def _is_stable_fact(entry: dict[str, Any]) -> bool:
    """변동성 항목(관찰/스킬 로그)과 달리 시간이 지나도 유효한 사실인지 판정한다.

    위치(type/kind == "location")는 안정적 사실로 본다. 이런 항목은 점수 동률
    구간에서 최신 관찰·에피소드에 밀려나면 안 된다.
    """
    meta = entry.get("metadata", {})
    if not isinstance(meta, dict):
        return False
    return meta.get("type") == "location" or meta.get("kind") == "location"


def _sort_recency_group(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 점수 동률 구간에서는 안정적 사실(위치)을 변동성 항목보다 우선하고,
    # 같은 부류 안에서만 최신 항목을 우선한다. (사실이 최신 관찰에 밀리는 문제 방지)
    return sorted(
        group,
        key=lambda item: (
            _is_stable_fact(item),
            _timestamp_to_epoch(item.get("timestamp", "")),
        ),
        reverse=True,
    )


def _timestamp_to_epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return 0.0
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


_MS_MASK = 0xFFFFFFFFFFFF  # 48비트
_COUNTER_MAX = 0xFFF       # 12비트 (같은 ms 안에서 4096건까지 단조 증가)
_ID_LOCK = threading.Lock()
_last_ms = 0
_counter = 0


def new_entry_id() -> str:
    """시간 정렬이 가능한 UUID(v7 형식)를 만든다.

    왜: Qdrant 는 삽입 순서를 보존하지 않고 scroll/대시보드가 **point ID 순**으로 반환한다.
    랜덤 uuid4 를 쓰면 저장 목록이 시간과 무관하게 섞여 보여 디버깅이 어렵다. UUIDv7 은
    상위 48비트가 Unix ms 타임스탬프라 ID 순서가 곧 시간순이 된다(형식은 표준 UUID라
    Qdrant point ID 로 그대로 사용 가능).

    레이아웃: [48b unix_ms][4b version=7][12b counter][2b variant=0b10][62b random]
    rand_a 자리를 **단조 카운터**로 쓴다(RFC 9562 권장). 같은 밀리초에 여러 건을 저장할 때
    (예: 파일 청크 적재) 랜덤 비트로 순서가 뒤집히는 것을 막고, 시계가 뒤로 가도 단조성을
    유지한다. 상위 64비트가 항상 증가하므로 문자열/정수 정렬 모두 생성 순서와 일치한다.
    """
    global _last_ms, _counter
    with _ID_LOCK:
        ms = int(time.time() * 1000) & _MS_MASK
        if ms > _last_ms:
            _last_ms, _counter = ms, 0
        else:
            # 같은 ms 이거나 시계 역행 → 카운터로 단조 증가 보장
            _counter += 1
            if _counter > _COUNTER_MAX:
                _last_ms += 1
                _counter = 0
        ms, counter = _last_ms, _counter
    value = (
        (ms << 80) | (0x7 << 76) | (counter << 64) | (0b10 << 62) | secrets.randbits(62)
    )
    return str(UUID(int=value))


def _sort_entries_by_time(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """저장 항목을 timestamp 오름차순(오래된 것 → 최신)으로 정렬한다.

    검색 랭킹과 무관한 '목록 조회' 전용 정렬이다(사람이 읽는 순서를 시간순으로 맞춤).
    timestamp 가 같거나 없으면 id 로 안정 정렬한다.
    """
    return sorted(
        entries,
        key=lambda e: (_timestamp_to_epoch(e.get("timestamp")), str(e.get("id") or "")),
    )
