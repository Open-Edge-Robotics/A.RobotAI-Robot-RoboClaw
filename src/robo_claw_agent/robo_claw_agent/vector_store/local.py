from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from ..observability import OperationTracker
from .base import (
    _matches_delete_filter,
    _sort_by_score_and_recency,
    _sort_entries_by_time,
    new_entry_id,
)

logger = logging.getLogger(__name__)


class LocalVectorStore:
    """기존 JSON 파일 기반 로컬 벡터 저장소."""

    def __init__(self, path: Path, *, namespace: str = "") -> None:
        self._path = path
        self._namespace = namespace
        self._lock = threading.RLock()
        self._metrics = OperationTracker()
        self._last_error = ""
        self._entries = self._load_json(path)

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
        with self._lock:
            self._entries.append(
                self._make_entry(
                    text=text,
                    metadata=metadata,
                    vector=vector,
                    timestamp=timestamp,
                    id=id,
                )
            )
            self._save_json_unlocked()
        self._metrics.record("add", success=True, duration_sec=time.monotonic() - start)
        return True

    def add_many(self, entries: list[dict[str, Any]]) -> int:
        start = time.monotonic()
        normalized: list[dict[str, Any]] = []
        for entry in entries:
            vector = entry.get("vector")
            if not isinstance(vector, list):
                continue
            normalized.append(
                self._make_entry(
                    text=str(entry.get("text", "")),
                    metadata=(entry["metadata"] if isinstance(entry.get("metadata"), dict) else {}),
                    vector=vector,
                    timestamp=str(entry.get("timestamp", "")),
                    id=str(entry.get("id")) if entry.get("id") else None,
                )
            )
        if not normalized:
            self._metrics.record("add_many", success=True, duration_sec=time.monotonic() - start)
            return 0
        with self._lock:
            self._entries.extend(normalized)
            self._save_json_unlocked()
        self._metrics.record("add_many", success=True, duration_sec=time.monotonic() - start)
        return len(normalized)

    def search(
        self,
        *,
        query_vector: list[float],
        top_k: int,
        score_threshold: float,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        start = time.monotonic()
        with self._lock:
            entries = list(self._entries)

        if not entries:
            self._metrics.record("search", success=True, duration_sec=time.monotonic() - start)
            return []

        # filter_metadata 필터링
        candidate_entries = entries
        if filter_metadata:
            candidate_entries = []
            for entry in entries:
                meta = entry.get("metadata", {})
                if not isinstance(meta, dict):
                    continue

                x_val = meta.get("x")
                y_val = meta.get("y")

                match = True
                if "x_min" in filter_metadata and x_val is not None:
                    if float(x_val) < float(filter_metadata["x_min"]):
                        match = False
                if "x_max" in filter_metadata and x_val is not None:
                    if float(x_val) > float(filter_metadata["x_max"]):
                        match = False
                if "y_min" in filter_metadata and y_val is not None:
                    if float(y_val) < float(filter_metadata["y_min"]):
                        match = False
                if "y_max" in filter_metadata and y_val is not None:
                    if float(y_val) > float(filter_metadata["y_max"]):
                        match = False

                for k, v in filter_metadata.items():
                    if k in ["x_min", "x_max", "y_min", "y_max"]:
                        continue
                    if k == "type_exclude":
                        excluded = v if isinstance(v, (list, tuple, set)) else [v]
                        if meta.get("type") in excluded:
                            match = False
                            break
                        continue
                    if meta.get(k) != v:
                        match = False
                        break

                if match:
                    candidate_entries.append(entry)

        if not candidate_entries:
            self._metrics.record("search", success=True, duration_sec=time.monotonic() - start)
            return []

        query_dim = len(query_vector)
        compatible_entries = [
            entry
            for entry in candidate_entries
            if isinstance(entry.get("vector"), list) and len(entry.get("vector", [])) == query_dim
        ]
        skipped_count = len(candidate_entries) - len(compatible_entries)
        if skipped_count:
            self._last_error = (
                f"벡터 차원 불일치 항목 {skipped_count}개 제외 (query_dim={query_dim})"
            )
            logger.warning("Local vector search: %s", self._last_error)

        if not compatible_entries:
            self._metrics.record("search", success=True, duration_sec=time.monotonic() - start)
            return []

        query_vec = np.array(query_vector)
        kb_vectors = np.array([entry["vector"] for entry in compatible_entries])

        dot_products = np.dot(kb_vectors, query_vec)
        kb_norms = np.linalg.norm(kb_vectors, axis=1)
        query_norm = np.linalg.norm(query_vec)
        similarities = dot_products / (kb_norms * query_norm + 1e-9)

        results: list[dict[str, Any]] = []
        for idx, score_value in enumerate(similarities):
            score = float(score_value)
            if score >= score_threshold:
                results.append({**compatible_entries[idx], "score": score})
        results = _sort_by_score_and_recency(results)[:top_k]
        self._metrics.record("search", success=True, duration_sec=time.monotonic() - start)
        return results

    def count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        start = time.monotonic()
        with self._lock:
            self._entries.clear()
            self._save_json_unlocked()
        self._metrics.record("clear", success=True, duration_sec=time.monotonic() - start)

    def delete_entries(
        self,
        *,
        ids: list[str] | None = None,
        source_path: str = "",
        text_exact: str = "",
    ) -> int:
        start = time.monotonic()
        id_set = set(ids or [])
        kept_entries: list[dict[str, Any]] = []
        deleted_count = 0
        with self._lock:
            for entry in self._entries:
                if _matches_delete_filter(
                    entry,
                    ids=id_set,
                    source_path=source_path,
                    text_exact=text_exact,
                ):
                    deleted_count += 1
                    continue
                kept_entries.append(entry)
            if deleted_count:
                self._entries = kept_entries
                self._save_json_unlocked()
        self._metrics.record("delete_entries", success=True, duration_sec=time.monotonic() - start)
        return deleted_count

    def list_entries(self, *, with_vectors: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            copied: list[dict[str, Any]] = []
            for entry in self._entries:
                item = dict(entry)
                if not with_vectors:
                    item.pop("vector", None)
                copied.append(item)
            # Qdrant 스토어와 동일하게 시간순(오래된 것 → 최신)으로 돌려준다.
            return _sort_entries_by_time(copied)

    def inspect(self) -> dict[str, Any]:
        with self._lock:
            dims: dict[int, int] = {}
            for entry in self._entries:
                vector = entry.get("vector")
                if isinstance(vector, list):
                    dim = len(vector)
                    dims[dim] = dims.get(dim, 0) + 1
            vector_dim = max(dims, key=dims.get) if dims else 0
            return {
                "backend": "local",
                "healthy": True,
                "path": str(self._path),
                "namespace": self._namespace,
                "count": len(self._entries),
                "vector_dimension": vector_dim,
                "vector_dimensions": dims,
                "last_error": self._last_error,
                "metrics": self._metrics.snapshot(),
            }

    def prune_vector_size(self, vector_size: int) -> int:
        """현재 임베더와 차원이 다른 로컬 벡터 항목을 제거한다."""
        start = time.monotonic()
        removed = 0
        with self._lock:
            kept = []
            for entry in self._entries:
                vector = entry.get("vector")
                if isinstance(vector, list) and len(vector) == vector_size:
                    kept.append(entry)
                else:
                    removed += 1
            if removed:
                self._entries = kept
                self._save_json_unlocked()
                self._last_error = f"Removed {removed} entries with mismatched vector dimensions (vector_size={vector_size})"
                logger.warning("Local vector store cleanup: %s", self._last_error)
        self._metrics.record(
            "prune_vector_size", success=True, duration_sec=time.monotonic() - start
        )
        return removed

    def _save_json_unlocked(self) -> None:
        """entries 를 디스크에 원자적으로 기록한다.
        호출자는 self._lock 을 반드시 보유해야 한다.
        """
        tmp_path = self._path.with_name(
            f".{self._path.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp"
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(self._entries, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            tmp_path.replace(self._path)
            self._fsync_parent_dir()
        except OSError as exc:
            self._last_error = str(exc)
            logger.error("Failed to save local vectors (%s): %s", self._path, exc)
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _make_entry(
        *,
        text: str,
        metadata: dict[str, Any],
        vector: list[float],
        timestamp: str,
        id: str | None = None,
    ) -> dict[str, Any]:
        return {
            # Qdrant 미러와 동일하게 시간 정렬 가능한 ID(UUIDv7)를 쓴다.
            "id": id or new_entry_id(),
            "text": text,
            "metadata": metadata,
            "vector": vector,
            "timestamp": timestamp,
        }

    def _load_json(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            with open(path, encoding="utf-8") as file:
                data = json.load(file)
            if not isinstance(data, list):
                return []
            normalized = []
            changed = False
            for entry in data:
                if not isinstance(entry, dict):
                    continue
                if "id" not in entry:
                    entry = {**entry, "id": new_entry_id()}
                    changed = True
                normalized.append(entry)
            if changed:
                with self._lock:
                    self._entries = normalized
                    self._save_json_unlocked()
            return normalized
        except OSError as exc:
            self._last_error = str(exc)
            logger.error("Failed to load local vectors (%s): %s", path, exc)
            return []
        except json.JSONDecodeError as exc:
            self._last_error = str(exc)
            backup_path = path.with_name(f"{path.name}.corrupt.{int(time.time())}")
            logger.error(
                "Detected corrupted local vector JSON (%s): %s. Quarantining corrupted file: %s",
                path,
                exc,
                backup_path,
            )
            try:
                path.replace(backup_path)
            except OSError as backup_exc:
                logger.error(
                    "Failed to quarantine corrupted local vector file (%s): %s", path, backup_exc
                )
            return []

    def _fsync_parent_dir(self) -> None:
        try:
            dir_fd = os.open(self._path.parent, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        finally:
            os.close(dir_fd)
