import pytest

pytest.importorskip("qdrant_client")

"""
MemoryManager 단위 테스트
"""

import os
import tempfile

import pytest
from qdrant_client import QdrantClient
from robo_claw_agent.memory_manager import MemoryManager
from robo_claw_agent.vector_store import QdrantVectorStore


class DummyEmbedder:
    def embed(self, text: str):
        if "앞" in text:
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]


@pytest.fixture
def tmp_memory():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = f.name
    os.unlink(path)  # 파일 없는 상태에서 시작
    mgr = MemoryManager(storage_path=path, max_short_term=5)
    yield mgr
    if os.path.exists(path):
        os.unlink(path)


def test_add_and_get_recent(tmp_memory):
    tmp_memory.add_event("test", {"val": 1})
    recent = tmp_memory.get_recent(10)
    assert len(recent) == 1
    assert recent[0]["type"] == "test"


def test_max_short_term_flushes_to_long(tmp_memory):
    for i in range(6):  # max=5이므로 첫 번째 항목은 장기로 이전
        tmp_memory.add_event("evt", {"i": i})
    assert len(tmp_memory._short_term) == 5
    assert len(tmp_memory._long_term) == 1


def test_flush_persists(tmp_memory):
    path = tmp_memory._storage_path
    tmp_memory.add_event("persist", {"x": 99})
    tmp_memory.flush()
    assert os.path.exists(path)


def test_clear_all(tmp_memory):
    tmp_memory.add_event("a", {})
    tmp_memory.clear_all()
    assert tmp_memory.get_recent(10) == []


def test_summary(tmp_memory):
    tmp_memory.add_event("x", {})
    s = tmp_memory.summary()
    assert "short_term_count" in s
    assert s["short_term_count"] == 1


def test_knowledge_search_with_local_vector_store(tmp_path):
    mgr = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    assert mgr.add_knowledge("앞쪽 복도는 비어 있다", {"kind": "experience"})

    results = mgr.search_knowledge("앞으로 가도 돼?", top_k=1)
    assert len(results) == 1
    assert results[0]["text"] == "앞쪽 복도는 비어 있다"


def test_local_rag_namespace_keeps_collections_isolated(tmp_path):
    storage_path = str(tmp_path / "memory.json")
    collection_a = MemoryManager(
        storage_path=storage_path,
        embedder=DummyEmbedder(),
        local_rag_namespace="collection/a",
        rag_score_threshold=0.1,
    )
    collection_b = MemoryManager(
        storage_path=storage_path,
        embedder=DummyEmbedder(),
        local_rag_namespace="collection_b",
        rag_score_threshold=0.1,
    )

    assert collection_a.add_knowledge("앞쪽 복도는 collection A 전용이다", {})
    assert collection_a._vector_store.count() == 1
    assert collection_b._vector_store.count() == 0
    assert collection_b.search_knowledge("앞으로 가도 돼?", top_k=1) == []

    inspected_a = collection_a._vector_store.inspect()
    inspected_b = collection_b._vector_store.inspect()
    assert inspected_a["namespace"] == "collection/a"
    assert inspected_b["namespace"] == "collection_b"
    assert inspected_a["path"] != inspected_b["path"]


def test_local_rag_namespace_is_persistent_and_legacy_local_path_is_unchanged(tmp_path):
    storage_path = str(tmp_path / "memory.json")
    first = MemoryManager(
        storage_path=storage_path,
        embedder=DummyEmbedder(),
        local_rag_namespace="robot_a",
    )
    assert first.add_knowledge("앞쪽 로봇 A 지식", {})

    restored = MemoryManager(
        storage_path=storage_path,
        embedder=DummyEmbedder(),
        local_rag_namespace="robot_a",
    )
    assert restored._vector_store.count() == 1

    legacy = MemoryManager(storage_path=storage_path, embedder=DummyEmbedder())
    assert legacy._vector_store.inspect()["path"].endswith("memory.kb.json")


def test_knowledge_search_with_qdrant_store(tmp_path):
    store = QdrantVectorStore(
        QdrantClient(location=":memory:"),
        "knowledge",
        timeout_sec=5.0,
    )
    mgr = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        vector_store=store,
        rag_score_threshold=0.1,
    )

    assert mgr.add_knowledge("왼쪽 통로가 더 안전하다", {"kind": "experience"})
    results = mgr.search_knowledge("왼쪽으로 가자", top_k=1)

    assert len(results) == 1
    assert results[0]["text"] == "왼쪽 통로가 더 안전하다"


def test_rag_status_and_reindex(tmp_path):
    mgr = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    assert mgr.add_knowledge("앞쪽 출입문이 열려 있다", {"kind": "experience"})

    status = mgr.rag_status()
    assert status["enabled"] is True
    assert status["vector_store"]["count"] == 1

    result = mgr.reindex_knowledge()
    assert result["success"] is True
    assert result["reindexed_count"] == 1


def test_add_knowledge_file_and_delete_by_source(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text(
        "첫 번째 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다.", encoding="utf-8"
    )
    mgr = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    added = mgr.add_knowledge_file(str(source), chunk_size=12, chunk_overlap=2)
    assert added["success"] is True
    assert added["stored_count"] >= 2
    assert mgr.rag_status()["vector_store"]["count"] == added["stored_count"]

    deleted = mgr.delete_knowledge(source_path=str(source))
    assert deleted["success"] is True
    assert deleted["deleted_count"] == added["stored_count"]
    assert mgr.rag_status()["vector_store"]["count"] == 0


def test_delete_knowledge_by_record_id(tmp_path):
    mgr = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    assert mgr.add_knowledge("앞쪽 창문이 열려 있다", {"kind": "experience"})
    entry = mgr._vector_store.list_entries()[0]

    deleted = mgr.delete_knowledge(record_id=str(entry["id"]))
    assert deleted["success"] is True
    assert deleted["deleted_count"] == 1
