import pytest

pytest.importorskip("qdrant_client")

from uuid import uuid4

from qdrant_client import QdrantClient
from robo_claw_agent.vector_store import (
    DualVectorStore,
    LocalVectorStore,
    QdrantVectorStore,
)


class _FailingStore:
    """원격 다운을 흉내내는 스텁: 모든 호출에서 예외를 던진다."""

    def add(self, **kwargs):
        raise RuntimeError("remote down")

    def search(self, **kwargs):
        raise RuntimeError("remote down")

    def count(self):
        raise RuntimeError("remote down")

    def clear(self):
        raise RuntimeError("remote down")

    def delete_entries(self, **kwargs):
        raise RuntimeError("remote down")

    def list_entries(self, **kwargs):
        raise RuntimeError("remote down")

    def inspect(self):
        raise RuntimeError("remote down")


def test_local_vector_store_roundtrip(tmp_path):
    store = LocalVectorStore(tmp_path / "kb.json")
    assert store.add(
        text="컵은 테이블 위에 있다",
        metadata={"kind": "experience"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )

    results = store.search(
        query_vector=[1.0, 0.0, 0.0],
        top_k=1,
        score_threshold=0.1,
    )

    assert len(results) == 1
    assert results[0]["text"] == "컵은 테이블 위에 있다"
    assert store.count() == 1
    assert store.inspect()["backend"] == "local"
    assert len(store.list_entries()) == 1
    entry_id = store.list_entries()[0]["id"]
    assert store.delete_entries(ids=[entry_id]) == 1
    assert store.count() == 0


def test_qdrant_vector_store_roundtrip():
    client = QdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "knowledge", timeout_sec=5.0)

    store.validate_connection()
    assert store.add(
        text="충전기는 오른쪽 선반에 있다",
        metadata={"kind": "experience"},
        vector=[0.0, 1.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )

    results = store.search(
        query_vector=[0.0, 1.0, 0.0],
        top_k=3,
        score_threshold=0.1,
    )

    assert len(results) == 1
    assert results[0]["text"] == "충전기는 오른쪽 선반에 있다"
    assert store.count() == 1
    inspected = store.inspect()
    assert inspected["backend"] == "qdrant"
    assert inspected["collection_exists"] is True
    entries = store.list_entries()
    assert len(entries) == 1
    assert store.delete_entries(ids=[entries[0]["id"]]) == 1
    assert store.count() == 0

    store.clear()
    assert store.count() == 0


def test_qdrant_vector_store_prefers_recent_memory_when_scores_are_similar():
    client = QdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "knowledge_recent", timeout_sec=5.0)

    assert store.add(
        text="창고는 예전 위치에 있다",
        metadata={"kind": "location", "name": "창고"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    assert store.add(
        text="창고는 새 위치에 있다",
        metadata={"kind": "location", "name": "창고"},
        vector=[0.999, 0.045, 0.0],
        timestamp="2026-01-02T00:00:00",
    )

    results = store.search(
        query_vector=[1.0, 0.0, 0.0],
        top_k=1,
        score_threshold=0.1,
        filter_metadata={"name": "창고"},
    )

    assert len(results) == 1
    assert results[0]["text"] == "창고는 새 위치에 있다"


def test_dual_vector_store_roundtrip(tmp_path):
    """primary(Qdrant)/mirror(Local) 동시 저장 및 동일 id 보장."""
    client = QdrantClient(location=":memory:")
    primary = QdrantVectorStore(client, "knowledge", timeout_sec=5.0)
    mirror = LocalVectorStore(tmp_path / "kb.json")
    store = DualVectorStore(primary, mirror)

    assert store.add(
        text="충전기는 오른쪽 선반에 있다",
        metadata={"kind": "experience"},
        vector=[0.0, 1.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )

    # 양쪽 모두 동일한 id로 저장되어야 한다.
    assert primary.count() == 1
    assert mirror.count() == 1
    assert primary.list_entries()[0]["id"] == mirror.list_entries()[0]["id"]

    results = store.search(query_vector=[0.0, 1.0, 0.0], top_k=3, score_threshold=0.1)
    assert len(results) == 1
    assert store.inspect()["backend"] == "dual"


def test_dual_vector_store_remote_down(tmp_path):
    """원격 다운 시 로컬에 저장되고 검색이 로컬로 폴백된다."""
    mirror = LocalVectorStore(tmp_path / "kb.json")
    store = DualVectorStore(_FailingStore(), mirror)

    # add: 예외 전파 없이 로컬에 저장
    assert store.add(
        text="문은 왼쪽에 있다",
        metadata={"kind": "experience"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    assert mirror.count() == 1

    # search: 로컬 폴백 결과 반환
    results = store.search(query_vector=[1.0, 0.0, 0.0], top_k=1, score_threshold=0.1)
    assert len(results) == 1
    assert results[0]["text"] == "문은 왼쪽에 있다"
    assert store.count() == 1


def test_dual_vector_store_merges_mirror_when_primary_empty(tmp_path):
    """원격이 정상 응답하지만 빈 결과를 반환할 때 로컬 미러 결과를 병합한다.

    원격 upsert 실패로 로컬에만 저장된 항목이, 이후 원격이 정상화되면
    검색에서 누락되는 문제를 방지한다.
    """
    mirror = LocalVectorStore(tmp_path / "kb.json")
    # primary는 정상 동작하지만 항상 빈 결과를 반환하는 스텁
    class _EmptyPrimary:
        def add(self, **kwargs):
            return True

        def search(self, **kwargs):
            return []

        def count(self):
            return 0

        def clear(self):
            pass

        def delete_entries(self, **kwargs):
            return 0

        def list_entries(self):
            return []

        def inspect(self):
            return {"backend": "empty", "healthy": True, "count": 0}

    store = DualVectorStore(_EmptyPrimary(), mirror)
    assert store.add(
        text="충전대는 왼쪽에 있다",
        metadata={"kind": "location", "name": "충전대"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )

    # primary가 빈 결과를 반환해도 로컬 미러 결과가 반환되어야 한다.
    results = store.search(query_vector=[1.0, 0.0, 0.0], top_k=1, score_threshold=0.1)
    assert len(results) == 1
    assert results[0]["text"] == "충전대는 왼쪽에 있다"

    # list_entries도 primary가 비어 있으면 로컬 미러 항목을 반환한다.
    entries = store.list_entries()
    assert len(entries) == 1
    assert entries[0]["text"] == "충전대는 왼쪽에 있다"


def test_dual_vector_store_reconcile(tmp_path):
    """양쪽 차이를 합집합 머지로 보충한다."""
    client = QdrantClient(location=":memory:")
    primary = QdrantVectorStore(client, "knowledge", timeout_sec=5.0)
    mirror = LocalVectorStore(tmp_path / "kb.json")

    p_id = str(uuid4())
    m_id = str(uuid4())
    # primary에만 존재
    primary.add(
        text="원격 전용 지식",
        metadata={},
        vector=[0.0, 1.0, 0.0],
        timestamp="2026-01-01T00:00:00",
        id=p_id,
    )
    # mirror에만 존재
    mirror.add(
        text="로컬 전용 지식",
        metadata={},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
        id=m_id,
    )

    store = DualVectorStore(primary, mirror)
    result = store.reconcile()
    assert result == {"pushed": 1, "pulled": 1}
    assert primary.count() == 2
    assert mirror.count() == 2
    primary_ids = {e["id"] for e in primary.list_entries()}
    mirror_ids = {e["id"] for e in mirror.list_entries()}
    assert primary_ids == mirror_ids == {p_id, m_id}


def test_local_vector_store_spatial_filter(tmp_path):
    """LocalVectorStore에서 기하 공간(x, y 범위) 필터링이 정상적으로 작동하는지 검증"""
    store = LocalVectorStore(tmp_path / "kb.json")

    # 1. 2D 좌표 메타데이터를 포함한 복수 관찰 적재
    # A: 로봇과 인접한 위치 (1.2, 0.8)
    store.add(
        text="식탁 주변 쓰레기",
        metadata={"x": 1.2, "y": 0.8, "type": "observation"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    # B: 멀리 떨어진 위치 (5.0, 5.0)
    store.add(
        text="침실 근처 먼지",
        metadata={"x": 5.0, "y": 5.0, "type": "observation"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )

    # 2. 반경 필터 구성 (중심 1.0, 1.0 / 반경 1.0 -> x: [0.0, 2.0], y: [0.0, 2.0])
    # A는 범위에 포함되지만 B는 포함되지 않아야 함
    filters = {
        "x_min": 0.0,
        "x_max": 2.0,
        "y_min": 0.0,
        "y_max": 2.0
    }

    results = store.search(
        query_vector=[1.0, 0.0, 0.0],
        top_k=2,
        score_threshold=0.1,
        filter_metadata=filters
    )

    assert len(results) == 1
    assert results[0]["text"] == "식탁 주변 쓰레기"


def test_qdrant_vector_store_spatial_filter():
    """QdrantVectorStore에서 기하 공간(x, y 범위) 필터링이 정상적으로 작동하는지 검증"""
    client = QdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "knowledge_spatial", timeout_sec=5.0)

    # 1. 관찰 적재
    # A: (1.2, 0.8)
    store.add(
        text="식탁 주변 쓰레기",
        metadata={"x": 1.2, "y": 0.8, "type": "observation"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    # B: (5.0, 5.0)
    store.add(
        text="침실 근처 먼지",
        metadata={"x": 5.0, "y": 5.0, "type": "observation"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )

    # 2. 반경 필터 구성
    filters = {
        "x_min": 0.0,
        "x_max": 2.0,
        "y_min": 0.0,
        "y_max": 2.0
    }

    results = store.search(
        query_vector=[1.0, 0.0, 0.0],
        top_k=2,
        score_threshold=0.1,
        filter_metadata=filters
    )

    assert len(results) == 1
    assert results[0]["text"] == "식탁 주변 쓰레기"
