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

logger = logging.getLogger(__name__)


class MemoryManager(StorageMixin, EpisodicMixin, RAGMixin, SemanticMixin, SkillLearningMixin):
    """
    에이전트 메모리 및 지식/공간 관리자.
    - 에피소드 메모리: 최근 이벤트 및 대화 히스토리
    - 지식 베이스(RAG): 임베딩 기반 유사 경험 검색
    - 시맨틱 맵: 객체 명칭 기반의 위치 좌표 관리
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
        self._semantic_path = self._artifact_base.with_suffix(".semantic.json")

        self._long_term = self._load_json(self._event_path) if self._backend == "json" else []
        self._vector_store = vector_store or LocalVectorStore(
            self._kb_path,
            namespace=local_rag_namespace,
        )

        if self._backend == "sqlite":
            self._init_sqlite()

        # 시맨틱 맵: 이름 기반 O(1) 조회를 위해 딕셔너리로 관리
        loaded_semantic = self._load_json(self._semantic_path)
        if isinstance(loaded_semantic, list):
            self._semantic_map = {obj["name"]: obj for obj in loaded_semantic}
        else:
            self._semantic_map = loaded_semantic

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
        self._semantic_map.clear()
        self._save_data(self._semantic_path, [])

    def summary(self) -> dict[str, Any]:
        persisted_count = len(self._long_term) if self._backend == "json" else self._count_sqlite()
        knowledge_count = self._vector_store.count()
        return {
            "backend": self._backend,
            "short_term": len(self._short_term),
            "long_term": persisted_count,
            "knowledge": knowledge_count,
            "semantic_objects": len(self._semantic_map),
            # 하위 호환용 별칭 (test_memory_manager 등 기존 코드 호환)
            "short_term_count": len(self._short_term),
            "long_term_count": persisted_count,
            "knowledge_count": knowledge_count,
            "semantic_object_count": len(self._semantic_map),
            "rag": self.rag_status(),
        }
