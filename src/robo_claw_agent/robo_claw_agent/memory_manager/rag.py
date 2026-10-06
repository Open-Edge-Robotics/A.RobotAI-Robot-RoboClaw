import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from robo_claw_agent.vector_store import VectorStore

from .base import Embedder

logger = logging.getLogger(__name__)


class RAGMixin:
    """RAG (Retrieval-Augmented Generation) 지식베이스 전담"""

    _embedder: Embedder | None
    _vector_store: VectorStore
    _rag_score_threshold: float
    # OperationTracker 타입 힌트는 구조 단순화를 위해 Any로 둡니다.
    _metrics: Any

    def set_embedder(self, embedder: Embedder | None) -> None:
        self._embedder = embedder

    def add_knowledge(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        if not self._embedder:
            return False
        start = time.monotonic()
        try:
            vector = self._embedder.embed(text)
            stored = self._vector_store.add(
                text=text,
                metadata=metadata or {},
                vector=vector,
                timestamp=datetime.now().isoformat(),
            )
            self._metrics.record(
                "add_knowledge", success=stored, duration_sec=time.monotonic() - start
            )
            return stored
        except Exception as e:
            self._metrics.record(
                "add_knowledge",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(e),
            )
            logger.error("Failed to store knowledge: %s", e)
            return False

    def add_knowledge_file(
        self,
        file_path: str,
        *,
        metadata: dict[str, Any] | None = None,
        chunk_size: int = 1000,
        chunk_overlap: int = 100,
        encoding: str = "utf-8",
    ) -> dict[str, Any]:
        if not self._embedder:
            return {
                "success": False,
                "message": "임베더가 없어 파일 기반 지식을 저장할 수 없습니다.",
                "stored_count": 0,
            }
        if chunk_size <= 0:
            return {
                "success": False,
                "message": "chunk_size는 1 이상이어야 합니다.",
                "stored_count": 0,
            }
        if chunk_overlap < 0 or chunk_overlap >= chunk_size:
            return {
                "success": False,
                "message": "chunk_overlap은 0 이상이고 chunk_size보다 작아야 합니다.",
                "stored_count": 0,
            }

        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return {
                "success": False,
                "message": f"파일을 찾을 수 없습니다: {file_path}",
                "stored_count": 0,
            }

        start = time.monotonic()
        try:
            raw_text = path.read_text(encoding=encoding)
        except UnicodeDecodeError as exc:
            self._metrics.record(
                "add_knowledge_file",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            return {
                "success": False,
                "message": f"텍스트 파일 디코딩 실패: {exc}",
                "stored_count": 0,
            }
        except OSError as exc:
            self._metrics.record(
                "add_knowledge_file",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            return {
                "success": False,
                "message": f"파일 읽기 실패: {exc}",
                "stored_count": 0,
            }

        normalized_text = raw_text.strip()
        if not normalized_text:
            self._metrics.record(
                "add_knowledge_file",
                success=False,
                duration_sec=time.monotonic() - start,
                error="empty file",
            )
            return {
                "success": False,
                "message": "파일 내용이 비어 있습니다.",
                "stored_count": 0,
            }

        chunks = self._split_text(normalized_text, chunk_size, chunk_overlap)
        base_metadata = {
            **(metadata or {}),
            "source_path": str(path),
            "source_name": path.name,
        }

        stored_count = 0
        for index, chunk in enumerate(chunks):
            stored = self.add_knowledge(
                chunk,
                {
                    **base_metadata,
                    "chunk_index": index,
                    "chunk_count": len(chunks),
                },
            )
            if not stored:
                self._metrics.record(
                    "add_knowledge_file",
                    success=False,
                    duration_sec=time.monotonic() - start,
                    error=f"chunk {index} store failure",
                )
                return {
                    "success": False,
                    "message": f"{index + 1}번째 청크 저장에 실패했습니다.",
                    "stored_count": stored_count,
                }
            stored_count += 1

        self._metrics.record(
            "add_knowledge_file",
            success=True,
            duration_sec=time.monotonic() - start,
        )
        return {
            "success": True,
            "message": f"파일 지식 {stored_count}건을 저장했습니다.",
            "stored_count": stored_count,
            "source_path": str(path),
            "chunk_count": len(chunks),
        }

    def delete_knowledge(
        self,
        *,
        record_id: str = "",
        source_path: str = "",
        text_exact: str = "",
    ) -> dict[str, Any]:
        if not any([record_id, source_path, text_exact]):
            return {
                "success": False,
                "message": "record_id, source_path, text_exact 중 하나는 필요합니다.",
                "deleted_count": 0,
            }

        start = time.monotonic()
        try:
            deleted_count = self._vector_store.delete_entries(
                ids=[record_id] if record_id else None,
                source_path=source_path,
                text_exact=text_exact,
            )
        except Exception as exc:
            self._metrics.record(
                "delete_knowledge",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            return {
                "success": False,
                "message": f"지식 삭제 실패: {exc}",
                "deleted_count": 0,
            }

        self._metrics.record(
            "delete_knowledge",
            success=True,
            duration_sec=time.monotonic() - start,
        )
        return {
            "success": True,
            "message": f"지식 {deleted_count}건을 삭제했습니다.",
            "deleted_count": deleted_count,
        }

    def search_knowledge(
        self,
        query: str,
        top_k: int = 3,
        score_threshold: float | None = None,
        filter_metadata: dict[str, Any] | None = None,
        current_pose: tuple[float, float] | None = None,
        radius_m: float | None = None,
    ) -> list[dict[str, Any]]:
        if not self._embedder or self._vector_store.count() == 0:
            return []
        start = time.monotonic()
        try:
            # 공간 필터 정보가 제공된 경우 메타데이터 필터로 치환
            filters = dict(filter_metadata) if filter_metadata else {}
            if current_pose and radius_m is not None and radius_m > 0:
                cx, cy = current_pose
                filters["x_min"] = cx - radius_m
                filters["x_max"] = cx + radius_m
                filters["y_min"] = cy - radius_m
                filters["y_max"] = cy + radius_m

            # 검색은 쿼리 전용 임베딩(비대칭). instruction-tuned 임베더는 지시문을
            # 부착하고, 그 외(또는 embed_query 미구현 임베더)에는 대칭 임베딩으로 폴백한다.
            embed_query = getattr(self._embedder, "embed_query", None)
            query_vec = embed_query(query) if callable(embed_query) else self._embedder.embed(query)
            results = self._vector_store.search(
                query_vector=query_vec,
                top_k=top_k,
                score_threshold=(
                    self._rag_score_threshold
                    if score_threshold is None
                    else score_threshold
                ),
                filter_metadata=filters if filters else None,
            )
            self._metrics.record(
                "search_knowledge",
                success=True,
                duration_sec=time.monotonic() - start,
            )
            # 검색 진단 로그: 결과 수·점수·타입·백엔드를 남겨 "결과 없음"이
            # 실제 미스인지 임계값/백엔드 문제인지 구분할 수 있게 한다.
            try:
                store_status = self._vector_store.inspect()
                backend = store_status.get("backend", "unknown")
            except Exception:
                backend = "unknown"
            if results:
                top = results[0]
                meta = top.get("metadata") or {}
                logger.info(
                    "RAG search: query=%r top_k=%d threshold=%.2f backend=%s "
                    "hits=%d top_score=%.4f top_type=%s",
                    query,
                    top_k,
                    score_threshold if score_threshold is not None else self._rag_score_threshold,
                    backend,
                    len(results),
                    float(top.get("score", 0.0)),
                    meta.get("type", "") if isinstance(meta, dict) else "",
                )
            else:
                logger.info(
                    "RAG search: query=%r top_k=%d threshold=%.2f backend=%s hits=0",
                    query,
                    top_k,
                    score_threshold if score_threshold is not None else self._rag_score_threshold,
                    backend,
                )
            return results
        except Exception as e:
            self._metrics.record(
                "search_knowledge",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(e),
            )
            logger.error("Failed to search knowledge: %s", e)
            return []

    def rag_status(self) -> dict[str, Any]:
        try:
            store_status = self._vector_store.inspect()
        except Exception as exc:
            logger.error("Failed to query RAG status: %s", exc)
            store_status = {
                "backend": "unknown",
                "healthy": False,
                "count": 0,
                "last_error": str(exc),
                "metrics": {},
            }
        return {
            "enabled": self._embedder is not None,
            "score_threshold": self._rag_score_threshold,
            "embedder_ready": self._embedder is not None,
            "vector_store": store_status,
            "metrics": self._metrics.snapshot(),
        }

    def reindex_knowledge(self) -> dict[str, Any]:
        if not self._embedder:
            return {
                "success": False,
                "message": "임베더가 없어 RAG 재색인을 수행할 수 없습니다.",
                "reindexed_count": 0,
            }

        start = time.monotonic()
        try:
            # 재색인 실패 시 원본 벡터로 되돌려야 하므로(아래 복구 경로) 벡터까지 읽는다.
            entries = self._vector_store.list_entries(with_vectors=True)
        except Exception as exc:
            self._metrics.record(
                "reindex_knowledge",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            return {
                "success": False,
                "message": f"현재 인덱스 읽기 실패: {exc}",
                "reindexed_count": 0,
            }
        if not entries:
            self._metrics.record(
                "reindex_knowledge",
                success=True,
                duration_sec=time.monotonic() - start,
            )
            return {
                "success": True,
                "message": "재색인할 지식이 없습니다.",
                "reindexed_count": 0,
            }

        rebuilt_entries: list[dict[str, Any]] = []
        try:
            # 전체 텍스트를 한 번에 임베딩 (개별 호출 → 배치 호출로 비용 절감)
            texts = [entry.get("text", "") for entry in entries]
            if hasattr(self._embedder, "embed_batch"):
                vectors = self._embedder.embed_batch(texts)
            else:
                vectors = [self._embedder.embed(text) for text in texts]
            for entry, vector in zip(entries, vectors, strict=False):
                rebuilt_entries.append(
                    {
                        "text": entry.get("text", ""),
                        "metadata": entry.get("metadata", {}),
                        "timestamp": entry.get("timestamp")
                        or datetime.now().isoformat(),
                        "vector": vector,
                    }
                )
        except Exception as exc:
            self._metrics.record(
                "reindex_knowledge",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            logger.error("RAG reindex embedding failed: %s", exc)
            return {
                "success": False,
                "message": f"재색인 임베딩 실패: {exc}",
                "reindexed_count": 0,
            }

        try:
            self._vector_store.clear()
            for entry in rebuilt_entries:
                self._vector_store.add(
                    text=entry["text"],
                    metadata=entry["metadata"],
                    vector=entry["vector"],
                    timestamp=entry["timestamp"],
                )
        except Exception as exc:
            logger.error("RAG reindex failed, attempting to restore existing index: %s", exc)
            try:
                self._vector_store.clear()
                for entry in entries:
                    self._vector_store.add(
                        text=entry.get("text", ""),
                        metadata=entry.get("metadata", {}),
                        vector=entry.get("vector", []),
                        timestamp=entry.get("timestamp") or datetime.now().isoformat(),
                    )
            except Exception as restore_exc:
                logger.error("Failed to restore RAG index: %s", restore_exc)
            self._metrics.record(
                "reindex_knowledge",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(exc),
            )
            return {
                "success": False,
                "message": f"재색인 실패: {exc}",
                "reindexed_count": 0,
            }

        self._metrics.record(
            "reindex_knowledge", success=True, duration_sec=time.monotonic() - start
        )
        return {
            "success": True,
            "message": f"지식 {len(rebuilt_entries)}건을 재색인했습니다.",
            "reindexed_count": len(rebuilt_entries),
        }

    def consolidate_and_evict_knowledge(self, ttl_days: int = 7) -> dict[str, Any]:
        """
        생성 후 ttl_days일이 경과한 RAG 지식(관찰 데이터)을 액티브 인덱스에서 만료 처리하고
        SQLite 콜드 백업 데이터베이스(memory.cold.db)에 아카이빙합니다.
        """
        start = time.monotonic()
        try:
            # 1. 아카이브 DB 파일 경로 정의 (self._storage_path 디렉토리에 memory.cold.db 생성)
            cold_db_path = str(Path(self._storage_path).parent / "memory.cold.db")

            # 2. SQLite 아카이브 테이블 생성
            with sqlite3.connect(cold_db_path) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS cold_knowledge_backup (
                        id TEXT PRIMARY KEY,
                        text TEXT,
                        metadata TEXT,
                        timestamp TEXT,
                        archived_at TEXT
                    )
                    """
                )
                conn.commit()

            # 3. 만료 대상 확인
            entries = self._vector_store.list_entries()
            if not entries:
                return {"success": True, "message": "만료 대상 지식이 없습니다.", "evicted_count": 0}

            now = datetime.now()
            threshold_date = now - timedelta(days=ttl_days)

            evict_ids = []
            evict_entries = []

            for entry in entries:
                ts_str = entry.get("timestamp", "")
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    continue

                meta = entry.get("metadata", {})
                if isinstance(meta, dict) and meta.get("type") == "observation":
                    if ts < threshold_date:
                        evict_ids.append(entry["id"])
                        evict_entries.append(entry)

            if not evict_ids:
                return {"success": True, "message": "만료 대상 관찰 지식이 없습니다.", "evicted_count": 0}

            # 4. SQLite 콜드 아카이브에 백업
            archived_at_str = datetime.now().isoformat()
            with sqlite3.connect(cold_db_path) as conn:
                for entry in evict_entries:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO cold_knowledge_backup (id, text, metadata, timestamp, archived_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            entry["id"],
                            entry["text"],
                            json.dumps(entry.get("metadata", {}), ensure_ascii=False),
                            entry.get("timestamp", ""),
                            archived_at_str
                        )
                    )
                conn.commit()

            # 5. 액티브 벡터 스토어에서 완전 제거
            deleted = self._vector_store.delete_entries(ids=evict_ids)

            self._metrics.record(
                "consolidate_and_evict",
                success=True,
                duration_sec=time.monotonic() - start
            )
            return {
                "success": True,
                "message": f"오래된 관찰 지식 {deleted}건을 콜드 스토리지에 아카이빙하고 액티브 인덱스에서 제거했습니다.",
                "evicted_count": deleted,
                "cold_db_path": cold_db_path
            }
        except Exception as e:
            self._metrics.record(
                "consolidate_and_evict",
                success=False,
                duration_sec=time.monotonic() - start,
                error=str(e)
            )
            logger.error("Failed to expire and archive knowledge: %s", e)
            return {
                "success": False,
                "message": f"아카이빙 중 오류 발생: {e}",
                "evicted_count": 0
            }

    def _split_text(self, text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
        if len(text) <= chunk_size:
            return [text]

        chunks: list[str] = []
        step = chunk_size - chunk_overlap
        cursor = 0
        while cursor < len(text):
            chunk = text[cursor : cursor + chunk_size].strip()
            if chunk:
                chunks.append(chunk)
            cursor += step
        return chunks
