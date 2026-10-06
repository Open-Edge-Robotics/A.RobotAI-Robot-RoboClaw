from __future__ import annotations

import logging
from typing import Any

from .base import VectorStore, new_entry_id

logger = logging.getLogger(__name__)


class DualVectorStore:
    """원격(primary)과 로컬(mirror)에 동시 저장하는 이중 벡터 저장소.

    - 쓰기: 동일 id로 양쪽에 저장. 로컬은 항상 저장(백업 보장), 원격 실패는 경고 후 진행.
    - 읽기: 원격 우선, 실패 시 로컬로 폴백.
    - reconcile() 호출 시 양쪽 차이를 보충(단순 합집합 머지).
    """

    def __init__(self, primary: VectorStore, mirror: VectorStore) -> None:
        self._primary = primary
        self._mirror = mirror
        self._primary_compatible = True
        self._expected_vector_size = 0
        self._primary_vector_size = 0

    def add(
        self,
        *,
        text: str,
        metadata: dict[str, Any],
        vector: list[float],
        timestamp: str,
        id: str | None = None,
    ) -> bool:
        # ID는 시간 정렬 가능한 UUIDv7로 발급한다(qdrant/local 스토어와 동일 규약).
        # 여기서 uuid4 를 쓰면 미러가 켜진 배포에서는 양쪽 스토어에 랜덤 ID가 내려가
        # UUIDv7 도입 효과가 통째로 무효화된다(실측: 미러 ON 컬렉션의 point ID 전부 v4).
        entry_id = id or new_entry_id()
        # 로컬은 항상 먼저 저장하여 유실을 방지한다.
        self._mirror.add(
            text=text,
            metadata=metadata,
            vector=vector,
            timestamp=timestamp,
            id=entry_id,
        )
        if self._primary_compatible:
            try:
                self._primary.add(
                    text=text,
                    metadata=metadata,
                    vector=vector,
                    timestamp=timestamp,
                    id=entry_id,
                )
            except Exception as exc:
                logger.warning("Failed to store to remote vector store, written to local mirror only: %s", exc)
        return True

    def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        score_threshold: float,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        primary_results: list[dict[str, Any]] = []
        if self._primary_compatible:
            try:
                primary_results = self._primary.search(
                    query_vector=query_vector,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    filter_metadata=filter_metadata,
                )
            except Exception as exc:
                logger.warning("Remote search failed, falling back to local mirror: %s", exc)
                primary_results = []

        # 원격이 정상 응답했지만 결과가 비어 있으면, 로컬 미러에만 저장된 항목이
        # 있을 수 있으므로 미러 검색 결과를 병합한다. (원격 upsert 실패 시 로컬만
        # 저장되고 이후 원격이 정상화되면 빈 결과를 반환하는 문제 방지)
        if not primary_results:
            try:
                mirror_results = self._mirror.search(
                    query_vector=query_vector,
                    top_k=top_k,
                    score_threshold=score_threshold,
                    filter_metadata=filter_metadata,
                )
            except Exception as exc:
                logger.warning("Local mirror search failed: %s", exc)
                mirror_results = []
            if mirror_results:
                logger.info(
                    "Remote search returned empty; merged %d result(s) from local mirror",
                    len(mirror_results),
                )
                return mirror_results

        return primary_results

    def count(self) -> int:
        if self._primary_compatible:
            try:
                return self._primary.count()
            except Exception as exc:
                logger.warning("Remote count failed, falling back to local mirror: %s", exc)
        return self._mirror.count()

    def clear(self) -> None:
        try:
            self._primary.clear()
        except Exception as exc:
            logger.warning("Remote clear failed, clearing local mirror only: %s", exc)
        self._mirror.clear()

    def delete_entries(
        self,
        *,
        ids: list[str] | None = None,
        source_path: str = "",
        text_exact: str = "",
    ) -> int:
        try:
            deleted = self._primary.delete_entries(
                ids=ids, source_path=source_path, text_exact=text_exact
            )
        except Exception as exc:
            logger.warning("Remote delete failed, deleting from local mirror only: %s", exc)
            deleted = 0
        mirror_deleted = self._mirror.delete_entries(
            ids=ids, source_path=source_path, text_exact=text_exact
        )
        return max(deleted, mirror_deleted)

    # 병합 주의: 양쪽 개선을 모두 유지한다.
    #  - origin/main: 원격이 정상 응답했지만 비어 있으면 로컬 미러로 폴백
    #                 (원격 upsert 실패로 로컬에만 남은 항목이 조회되도록)
    #  - using_gemma4_e4b: with_vectors 파라미터 (기본 False)
    #                 스캔 용도에서 1024-dim 벡터를 끌어와 타임아웃 나는 것 방지
    def list_entries(self, *, with_vectors: bool = False) -> list[dict[str, Any]]:
        primary_entries: list[dict[str, Any]] = []
        if self._primary_compatible:
            try:
                primary_entries = self._primary.list_entries(with_vectors=with_vectors)
            except Exception as exc:
                logger.warning("Remote list_entries failed, falling back to local mirror: %s", exc)
                primary_entries = []

        # 원격이 정상 응답했지만 비어 있으면 로컬 미러 항목을 사용한다.
        # (원격 upsert 실패로 로컬에만 저장된 항목이 조회되도록 보장)
        if not primary_entries:
            try:
                return self._mirror.list_entries(with_vectors=with_vectors)
            except Exception as exc:
                logger.warning("Local mirror list_entries failed: %s", exc)
                return []
        return primary_entries

    def inspect(self) -> dict[str, Any]:
        primary_status = self._safe_inspect(self._primary)
        mirror_status = self._safe_inspect(self._mirror)
        return {
            "backend": "dual",
            "healthy": bool(primary_status.get("healthy") or mirror_status.get("healthy")),
            "primary_healthy": bool(primary_status.get("healthy")),
            "count": int(
                primary_status.get("count", 0)
                if primary_status.get("healthy")
                else mirror_status.get("count", 0)
            ),
            "vector_dimension": int(
                mirror_status.get("vector_dimension", 0)
                if not self._primary_compatible
                else primary_status.get("vector_dimension", 0)
                or mirror_status.get("vector_dimension", 0)
            ),
            "primary_compatible": self._primary_compatible,
            "expected_vector_size": self._expected_vector_size,
            "primary_vector_size": self._primary_vector_size,
            "primary": primary_status,
            "mirror": mirror_status,
        }

    def reconcile(self) -> dict[str, int]:
        """양쪽 저장소의 id 차이를 보충한다(합집합 머지). 반환: pushed/pulled 카운트."""
        # 머지는 실제로 벡터를 옮겨야 하므로 여기서만 with_vectors=True 로 읽는다.
        try:
            primary_entries = self._primary.list_entries(with_vectors=True)
        except Exception as exc:
            logger.warning("Skipping reconcile due to remote being unreachable: %s", exc)
            return {"pushed": 0, "pulled": 0}

        mirror_entries = self._mirror.list_entries(with_vectors=True)
        primary_ids = {str(e.get("id")) for e in primary_entries if e.get("id")}
        mirror_ids = {str(e.get("id")) for e in mirror_entries if e.get("id")}

        pushed = 0
        if self._primary_compatible:
            for entry in mirror_entries:
                entry_id = str(entry.get("id"))
                if entry_id and entry_id not in primary_ids and entry.get("vector"):
                    try:
                        self._primary.add(
                            text=entry.get("text", ""),
                            metadata=entry.get("metadata", {}),
                            vector=entry["vector"],
                            timestamp=entry.get("timestamp", ""),
                            id=entry_id,
                        )
                        pushed += 1
                    except Exception as exc:
                        logger.warning("Reconcile remote push failed (id=%s): %s", entry_id, exc)
        else:
            logger.warning("Skipping mirror -> primary push due to remote vector dimension mismatch.")

        pull_entries: list[dict[str, Any]] = []
        for entry in primary_entries:
            entry_id = str(entry.get("id"))
            if entry_id and entry_id not in mirror_ids and entry.get("vector"):
                pull_entries.append(
                    {
                        "id": entry_id,
                        "text": entry.get("text", ""),
                        "metadata": entry.get("metadata", {}),
                        "vector": entry["vector"],
                        "timestamp": entry.get("timestamp", ""),
                    }
                )

        pulled = self._add_mirror_entries(pull_entries)

        if pushed or pulled:
            logger.info("RAG reconcile complete: pushed=%d, pulled=%d", pushed, pulled)
        return {"pushed": pushed, "pulled": pulled}

    def _add_mirror_entries(self, entries: list[dict[str, Any]]) -> int:
        if not entries:
            return 0
        add_many = getattr(self._mirror, "add_many", None)
        if callable(add_many):
            return int(add_many(entries))
        pulled = 0
        for entry in entries:
            self._mirror.add(
                text=entry.get("text", ""),
                metadata=entry.get("metadata", {}),
                vector=entry["vector"],
                timestamp=entry.get("timestamp", ""),
                id=str(entry.get("id")),
            )
            pulled += 1
        return pulled

    def validate_connection(self) -> None:
        validate = getattr(self._primary, "validate_connection", None)
        if callable(validate):
            validate()

    def validate_vector_size(self, vector_size: int) -> None:
        self._expected_vector_size = int(vector_size)
        validate = getattr(self._primary, "validate_vector_size", None)
        if callable(validate):
            try:
                validate(vector_size)
                self._primary_compatible = True
                self._primary_vector_size = int(vector_size)
                prune = getattr(self._mirror, "prune_vector_size", None)
                if callable(prune):
                    prune(vector_size)
            except Exception:
                self._primary_compatible = False
                self._primary_vector_size = self._inspect_primary_vector_size()
                prune = getattr(self._mirror, "prune_vector_size", None)
                if callable(prune) and self._primary_vector_size > 0:
                    prune(self._primary_vector_size)
                raise

    def _inspect_primary_vector_size(self) -> int:
        try:
            inspect = getattr(self._primary, "inspect", None)
            if callable(inspect):
                return int(inspect().get("vector_dimension", 0) or 0)
        except Exception:
            return 0
        return 0

    @staticmethod
    def _safe_inspect(store: VectorStore) -> dict[str, Any]:
        try:
            return store.inspect()
        except Exception as exc:
            return {"healthy": False, "last_error": str(exc)}
