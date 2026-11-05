import hashlib
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

from robo_claw_agent.observability import OperationTracker
from robo_claw_agent.vector_store import LocalVectorStore, VectorStore

from .base import BackendType, Embedder
from .episodic import EpisodicMixin
from .rag import RAGMixin
from .semantic import SemanticMixin
from .skill_learning import SkillLearningMixin
from .storage import StorageMixin
from .structured import STRUCTURED_TYPES, StructuredMemoryStore

logger = logging.getLogger(__name__)


class MemoryManager(StorageMixin, EpisodicMixin, RAGMixin, SemanticMixin, SkillLearningMixin):
    """
    에이전트 메모리 및 지식/공간 관리자.
    - 에피소드 메모리: 최근 이벤트 및 대화 히스토리
    - 지식 베이스(RAG): 교훈·장면 본문의 의미 검색
    - 구조화 메모리(SQLite): 장소·별칭·좌표·스킬 실행 경험 관리
    """

    def __init__(
        self,
        storage_path: str | None = None,
        max_short_term: int = 50,
        backend: BackendType = "json",
        embedder: Embedder | None = None,
        vector_store: VectorStore | None = None,
        local_rag_namespace: str = "",
        rag_score_threshold: float = 0.7,
        skill_learning_success_sample_rate: float = 0.1,
    ) -> None:
        self._max_short_term = max_short_term
        self._backend = backend
        self._short_term: list[dict[str, Any]] = []
        self._embedder = embedder
        self._rag_score_threshold = rag_score_threshold
        self._skill_learning_success_sample_rate = float(skill_learning_success_sample_rate)
        self._metrics = OperationTracker()

        if storage_path is None:
            home = Path.home() / ".robo_claw"
            home.mkdir(parents=True, exist_ok=True)
            default_name = "memory.json" if backend == "json" else "memory.db"
            storage_path = str(home / default_name)

        self._storage_path = str(Path(storage_path))
        self._event_path = Path(self._storage_path)
        self._artifact_base = (
            self._event_path if self._event_path.suffix else self._event_path.with_suffix(".data")
        )
        self._kb_path = self._resolve_kb_path(local_rag_namespace)

        self._long_term = self._load_json(self._event_path) if self._backend == "json" else []
        self._vector_store = vector_store or LocalVectorStore(
            self._kb_path,
            namespace=local_rag_namespace,
        )

        if self._backend == "sqlite":
            self._init_sqlite()

        # JSON 이벤트 백엔드에서도 구조화 데이터는 namespace별 SQLite로 관리합니다.
        self._structured_store = StructuredMemoryStore(self._kb_path.with_suffix(".structured.db"))

    def list_knowledge_entries(self, *, entry_type=None, skill_name="", success=None, limit=None):
        structured = self._structured_store.records(
            entry_type=entry_type, skill_name=skill_name, success=success, limit=limit
        )
        if entry_type in STRUCTURED_TYPES:
            return structured
        vectors = [
            e
            for e in self._vector_store.list_entries()
            if e.get("metadata", {}).get("type") not in STRUCTURED_TYPES
            and (not entry_type or e.get("metadata", {}).get("type") == entry_type)
        ]
        return structured + vectors

    def find_location_entries(self, names):
        return [
            e
            for e in self._structured_store.records(names=names)
            if e["metadata"]["type"] in ("location", "navigated_coordinate")
        ]

    def _resolve_kb_path(self, namespace: str) -> Path:
        """로컬 RAG를 선택적 namespace별 파일로 분리한다.

        namespace가 없는 기존 local backend는 기존 경로를 그대로 사용한다.
        Qdrant mirror는 collection 이름을 namespace로 받아 별도 파일을 사용하며,
        사람이 확인할 수 있는 이름과 해시를 함께 사용해 경로 충돌과 traversal을 막는다.
        """
        if not namespace:
            return self._artifact_base.with_suffix(".kb.json")

        readable = re.sub(r"[^A-Za-z0-9._-]+", "_", namespace).strip("._")
        readable = readable or "collection"
        digest = hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:12]
        collection_dir = self._artifact_base.parent / f"{self._artifact_base.name}.kb.collections"
        return collection_dir / f"{readable}--{digest}.json"

    # ── 공통 유틸리티 ──────────────────────────────────────

    def flush(self) -> None:
        for entry in self._short_term:
            self._persist_event(entry)
        self._short_term.clear()

    def clear_all(self) -> None:
        self._short_term.clear()
        if self._backend == "json":
            self._long_term.clear()
            self._save_data(self._event_path, self._long_term)
        else:
            with sqlite3.connect(self._storage_path) as conn:
                conn.execute("DELETE FROM events")
                conn.commit()
        self._vector_store.clear()
        self._structured_store.clear()

    def summary(self) -> dict[str, Any]:
        persisted_count = len(self._long_term) if self._backend == "json" else self._count_sqlite()
        knowledge_count = self._vector_store.count() + self._structured_store.count()
        return {
            "backend": self._backend,
            "short_term": len(self._short_term),
            "long_term": persisted_count,
            "knowledge": knowledge_count,
            "semantic_objects": len(self.get_all_objects()),
            # 하위 호환용 별칭 (test_memory_manager 등 기존 코드 호환)
            "short_term_count": len(self._short_term),
            "long_term_count": persisted_count,
            "knowledge_count": knowledge_count,
            "semantic_object_count": len(self.get_all_objects()),
            "rag": self.rag_status(),
        }
