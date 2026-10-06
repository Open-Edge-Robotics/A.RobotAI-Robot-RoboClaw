from __future__ import annotations

import logging
import time
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models

from ..observability import OperationTracker
from .base import (
    _matches_delete_filter,
    _sort_by_score_and_recency,
    _sort_entries_by_time,
    new_entry_id,
)

logger = logging.getLogger(__name__)


def _as_vector(raw: Any) -> list[float] | None:
    """Qdrant 레코드의 vector 필드를 float 리스트로 정규화한다.

    이 구현은 단일(무명) 벡터 컬렉션만 쓰므로 보통 list 로 온다. 다만 클라이언트/서버
    버전에 따라 이름있는 벡터 형태({"": [...]})로 오는 경우가 있어 단일 항목 dict 도 받는다.
    """
    if isinstance(raw, list):
        try:
            return [float(value) for value in raw]
        except (TypeError, ValueError):
            return None
    if isinstance(raw, dict) and len(raw) == 1:
        return _as_vector(next(iter(raw.values())))
    return None


class QdrantVectorStore:
    """Qdrant 기반 RAG 벡터 저장소."""

    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        *,
        timeout_sec: float = 5.0,
        endpoint: str = "",
    ) -> None:
        self._client = client
        self._collection_name = collection_name
        self._timeout = int(timeout_sec)
        self._endpoint = endpoint or "<embedded>"
        self._metrics = OperationTracker()
        self._last_error = ""

    @classmethod
    def from_url(
        cls,
        *,
        url: str,
        collection_name: str,
        api_key: str = "",
        timeout_sec: float = 5.0,
    ) -> QdrantVectorStore:
        client = QdrantClient(
            url=url,
            api_key=api_key or None,
            timeout=int(timeout_sec),
        )
        return cls(
            client,
            collection_name,
            timeout_sec=timeout_sec,
            endpoint=url,
        )

    def validate_connection(self) -> None:
        start = time.monotonic()
        try:
            self._client.get_collections()
        except Exception as exc:
            self._last_error = str(exc)
            self._metrics.record(
                "validate_connection",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            raise
        self._metrics.record(
            "validate_connection",
            success=True,
            duration_sec=time.monotonic() - start,
        )

    def validate_vector_size(self, vector_size: int) -> None:
        if self._client.collection_exists(self._collection_name):
            self._validate_collection_size(vector_size)

    def add(
        self,
        *,
        text: str,
        metadata: dict[str, Any],
        vector: list[float],
        timestamp: str,
        id: str | None = None,
    ) -> bool:
        start = time.monotonic()
        try:
            self._ensure_collection(len(vector))
            # 시간 정렬 가능한 ID(UUIDv7): Qdrant scroll/대시보드는 point ID 순으로 반환하므로
            # ID가 시간순이면 저장 목록도 시간순으로 보인다(랜덤 uuid4는 뒤섞임).
            point = models.PointStruct(
                id=id or new_entry_id(),
                vector=vector,
                payload={
                    "text": text,
                    "metadata": metadata,
                    "timestamp": timestamp,
                },
            )
            self._client.upsert(
                self._collection_name,
                [point],
                wait=True,
                timeout=self._timeout,
            )
        except Exception as exc:
            self._last_error = str(exc)
            self._metrics.record(
                "add",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            raise
        self._metrics.record("add", success=True, duration_sec=time.monotonic() - start)
        return True

    def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        score_threshold: float,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        start = time.monotonic()
        try:
            if not self._client.collection_exists(self._collection_name):
                self._metrics.record(
                    "search", success=True, duration_sec=time.monotonic() - start
                )
                return []

            self._validate_collection_size(len(query_vector))

            query_filter = None
            if filter_metadata:
                must_conditions = []
                must_not_conditions = []
                for k, v in filter_metadata.items():
                    if k == "type_exclude":
                        excluded = v if isinstance(v, (list, tuple, set)) else [v]
                        for t in excluded:
                            must_not_conditions.append(
                                models.FieldCondition(
                                    key="metadata.type",
                                    match=models.MatchValue(value=t),
                                )
                            )
                    elif k == "x_min":
                        must_conditions.append(
                            models.FieldCondition(
                                key="metadata.x",
                                range=models.Range(gte=float(v))
                            )
                        )
                    elif k == "x_max":
                        must_conditions.append(
                            models.FieldCondition(
                                key="metadata.x",
                                range=models.Range(lte=float(v))
                            )
                        )
                    elif k == "y_min":
                        must_conditions.append(
                            models.FieldCondition(
                                key="metadata.y",
                                range=models.Range(gte=float(v))
                            )
                        )
                    elif k == "y_max":
                        must_conditions.append(
                            models.FieldCondition(
                                key="metadata.y",
                                range=models.Range(lte=float(v))
                            )
                        )
                    else:
                        must_conditions.append(
                            models.FieldCondition(
                                key=f"metadata.{k}",
                                match=models.MatchValue(value=v)
                            )
                        )
                if must_conditions or must_not_conditions:
                    query_filter = models.Filter(
                        must=must_conditions or None,
                        must_not=must_not_conditions or None,
                    )

            fetch_limit = max(top_k * 4, top_k + 8)
            response = self._client.query_points(
                self._collection_name,
                query=query_vector,
                limit=fetch_limit,
                with_payload=True,
                score_threshold=score_threshold,
                query_filter=query_filter,
                timeout=self._timeout,
            )
        except Exception as exc:
            self._last_error = str(exc)
            self._metrics.record(
                "search",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            raise

        results: list[dict[str, Any]] = []
        for point in response.points:
            payload = point.payload or {}
            results.append(
                {
                    "text": payload.get("text", ""),
                    "metadata": payload.get("metadata", {}),
                    "timestamp": payload.get("timestamp", ""),
                    "score": float(point.score),
                    "id": str(point.id),
                }
            )
        self._metrics.record(
            "search", success=True, duration_sec=time.monotonic() - start
        )
        return _sort_by_score_and_recency(results)[:top_k]

    def count(self) -> int:
        if not self._client.collection_exists(self._collection_name):
            return 0
        return int(
            self._client.count(
                self._collection_name,
                exact=True,
            ).count
        )

    def clear(self) -> None:
        start = time.monotonic()
        if self._client.collection_exists(self._collection_name):
            self._client.delete_collection(self._collection_name)
        self._metrics.record(
            "clear", success=True, duration_sec=time.monotonic() - start
        )

    def delete_entries(
        self,
        *,
        ids: list[str] | None = None,
        source_path: str = "",
        text_exact: str = "",
    ) -> int:
        start = time.monotonic()
        try:
            matched_ids = set(ids or [])
            if source_path or text_exact:
                for entry in self.list_entries():
                    if _matches_delete_filter(
                        entry,
                        ids=matched_ids,
                        source_path=source_path,
                        text_exact=text_exact,
                    ):
                        entry_id = entry.get("id")
                        if entry_id:
                            matched_ids.add(str(entry_id))
            if not matched_ids:
                self._metrics.record(
                    "delete_entries",
                    success=True,
                    duration_sec=time.monotonic() - start,
                )
                return 0
            self._client.delete(
                self._collection_name,
                models.PointIdsList(points=list(matched_ids)),
                wait=True,
                timeout=self._timeout,
            )
            self._metrics.record(
                "delete_entries", success=True, duration_sec=time.monotonic() - start
            )
            return len(matched_ids)
        except Exception as exc:
            self._last_error = str(exc)
            self._metrics.record(
                "delete_entries",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            raise

    def list_entries(self, *, with_vectors: bool = False) -> list[dict[str, Any]]:
        """컬렉션 전체를 스크롤해 항목 목록을 돌려준다.

        ``with_vectors=False``(기본)면 벡터를 가져오지 않는다. 위치 조회/dedup/목록 같은
        스캔은 메타데이터·텍스트만 보는데, 1024-dim 벡터를 전부 실어오면 컬렉션이 커질수록
        페이로드가 폭증해 ``self._timeout`` 을 넘기고, 호출부가 예외를 폴백으로 처리하면서
        "데이터는 있는데 못 찾는" 열화로 이어진다. 벡터가 필요한 호출만 True 로 부른다.
        """
        start = time.monotonic()
        try:
            if not self._client.collection_exists(self._collection_name):
                self._metrics.record(
                    "list_entries", success=True, duration_sec=time.monotonic() - start
                )
                return []

            entries: list[dict[str, Any]] = []
            offset = None
            while True:
                records, offset = self._client.scroll(
                    self._collection_name,
                    limit=128,
                    with_payload=True,
                    with_vectors=with_vectors,
                    offset=offset,
                    timeout=self._timeout,
                )
                for record in records:
                    payload = record.payload or {}
                    entry = {
                        "id": str(record.id),
                        "text": payload.get("text", ""),
                        "metadata": payload.get("metadata", {}),
                        "timestamp": payload.get("timestamp", ""),
                    }
                    if with_vectors:
                        vector = _as_vector(record.vector)
                        if vector is None:
                            # 벡터를 요청했는데 못 받은 항목은 호출부(reconcile/재색인)가
                            # 쓸 수 없다. 조용히 버리지 않고 남긴다.
                            logger.warning(
                                "Skipping point without a usable vector: collection=%s id=%s",
                                self._collection_name,
                                entry["id"],
                            )
                            continue
                        entry["vector"] = vector
                    entries.append(entry)
                if offset is None:
                    self._metrics.record(
                        "list_entries",
                        success=True,
                        duration_sec=time.monotonic() - start,
                    )
                    # 목록은 시간순(오래된 것 → 최신)으로 돌려준다. scroll 은 ID 순이라
                    # 레거시 uuid4 항목이 섞여 있어도 사람이 읽는 순서를 시간순으로 맞춘다.
                    return _sort_entries_by_time(entries)
        except Exception as exc:
            self._last_error = str(exc)
            self._metrics.record(
                "list_entries",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            raise

    def inspect(self) -> dict[str, Any]:
        try:
            collection_exists = self._client.collection_exists(self._collection_name)
            vector_dim = 0
            points_count = 0
            if collection_exists:
                info = self._client.get_collection(self._collection_name)
                vectors = info.config.params.vectors
                if isinstance(vectors, models.VectorParams):
                    vector_dim = int(vectors.size)
                points_count = int(
                    self._client.count(self._collection_name, exact=True).count
                )
            return {
                "backend": "qdrant",
                "healthy": True,
                "endpoint": self._endpoint,
                "collection_name": self._collection_name,
                "collection_exists": collection_exists,
                "count": points_count,
                "vector_dimension": vector_dim,
                "timeout_sec": self._timeout,
                "last_error": self._last_error,
                "metrics": self._metrics.snapshot(),
            }
        except Exception as exc:
            self._last_error = str(exc)
            return {
                "backend": "qdrant",
                "healthy": False,
                "endpoint": self._endpoint,
                "collection_name": self._collection_name,
                "collection_exists": False,
                "count": 0,
                "vector_dimension": 0,
                "timeout_sec": self._timeout,
                "last_error": str(exc),
                "metrics": self._metrics.snapshot(),
            }

    def _ensure_collection(self, vector_size: int) -> None:
        if not self._client.collection_exists(self._collection_name):
            self._client.create_collection(
                self._collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                ),
            )
            return
        self._validate_collection_size(vector_size)

    def _validate_collection_size(self, vector_size: int) -> None:
        info = self._client.get_collection(self._collection_name)
        vectors = info.config.params.vectors
        if not isinstance(vectors, models.VectorParams):
            raise ValueError("이 구현은 단일 벡터 컬렉션만 지원합니다.")
        if int(vectors.size) != vector_size:
            raise ValueError(
                f"Qdrant 컬렉션 벡터 차원 불일치: expected={vectors.size}, actual={vector_size}"
            )
