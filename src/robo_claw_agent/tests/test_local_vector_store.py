import json
from concurrent.futures import ThreadPoolExecutor

from robo_claw_agent.vector_store import DualVectorStore, LocalVectorStore


def test_local_vector_store_quarantines_corrupt_json(tmp_path):
    path = tmp_path / "kb.json"
    path.write_text('[{"text": "깨진 데이터"}', encoding="utf-8")

    store = LocalVectorStore(path)

    assert store.count() == 0
    assert not path.exists()
    assert len(list(tmp_path.glob("kb.json.corrupt.*"))) == 1


def test_local_vector_store_concurrent_adds_keep_valid_json(tmp_path):
    path = tmp_path / "kb.json"
    store = LocalVectorStore(path)

    def add_entry(index: int) -> None:
        store.add(
            text=f"지식 {index}",
            metadata={"index": index},
            vector=[1.0, 0.0, 0.0],
            timestamp="2026-01-01T00:00:00",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(add_entry, range(40)))

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 40
    assert store.count() == 40


def test_local_vector_store_add_many_writes_valid_json(tmp_path):
    path = tmp_path / "kb.json"
    store = LocalVectorStore(path)

    added = store.add_many(
        [
            {
                "id": f"id-{index}",
                "text": f"지식 {index}",
                "metadata": {"index": index},
                "vector": [1.0, 0.0, 0.0],
                "timestamp": "2026-01-01T00:00:00",
            }
            for index in range(10)
        ]
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    assert added == 10
    assert len(data) == 10
    assert store.count() == 10


def test_local_vector_store_prefers_recent_memory_when_scores_are_similar(tmp_path):
    store = LocalVectorStore(tmp_path / "kb.json")
    store.add(
        text="창고는 예전 위치에 있다",
        metadata={"kind": "location", "name": "창고"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    store.add(
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


def test_local_vector_store_ignores_mismatched_vector_dimensions(tmp_path):
    store = LocalVectorStore(tmp_path / "kb.json")
    store.add(
        text="이전 임베더 지식",
        metadata={},
        vector=[1.0, 0.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    store.add(
        text="현재 임베더 지식",
        metadata={},
        vector=[1.0, 0.0],
        timestamp="2026-01-02T00:00:00",
    )

    results = store.search(query_vector=[1.0, 0.0], top_k=3, score_threshold=0.1)

    assert [r["text"] for r in results] == ["현재 임베더 지식"]
    assert "차원 불일치" in store.inspect()["last_error"]


def test_local_vector_store_prunes_mismatched_vector_dimensions(tmp_path):
    store = LocalVectorStore(tmp_path / "kb.json")
    store.add(
        text="이전 임베더 지식",
        metadata={},
        vector=[1.0, 0.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    store.add(
        text="현재 임베더 지식",
        metadata={},
        vector=[1.0, 0.0],
        timestamp="2026-01-02T00:00:00",
    )

    removed = store.prune_vector_size(2)

    assert removed == 1
    assert store.count() == 1
    assert store.list_entries()[0]["text"] == "현재 임베더 지식"


def test_dual_vector_store_reconcile_uses_mirror_batch_add():
    class PrimaryStore:
        def list_entries(self, **kwargs):
            return [
                {
                    "id": "primary-only",
                    "text": "원격 지식",
                    "metadata": {},
                    "vector": [1.0, 0.0, 0.0],
                    "timestamp": "2026-01-01T00:00:00",
                }
            ]

        def add(self, **kwargs):
            return True

    class MirrorStore:
        def __init__(self):
            self.add_many_called = False

        def list_entries(self, **kwargs):
            return []

        def add_many(self, entries):
            self.add_many_called = True
            return len(entries)

        def add(self, **kwargs):
            raise AssertionError("add_many를 사용해야 합니다")

    mirror = MirrorStore()
    store = DualVectorStore(PrimaryStore(), mirror)

    result = store.reconcile()

    assert result == {"pushed": 0, "pulled": 1}
    assert mirror.add_many_called is True


def test_dual_vector_store_disables_incompatible_primary_and_prunes_mirror(tmp_path):
    class IncompatiblePrimary:
        def __init__(self):
            self.add_called = False

        def validate_vector_size(self, vector_size):
            raise ValueError("Qdrant 컬렉션 벡터 차원 불일치")

        def add(self, **kwargs):
            self.add_called = True
            raise AssertionError("차원 불일치 원격에는 저장하지 않아야 합니다")

        def search(self, **kwargs):
            raise AssertionError("차원 불일치 원격에는 검색하지 않아야 합니다")

        def list_entries(self, **kwargs):
            return []

        def inspect(self):
            return {"healthy": True, "count": 10, "vector_dimension": 4}

    primary = IncompatiblePrimary()
    mirror = LocalVectorStore(tmp_path / "kb.json")
    mirror.add(
        text="이전 임베더 지식",
        metadata={},
        vector=[1.0, 0.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    store = DualVectorStore(primary, mirror)

    try:
        store.validate_vector_size(2)
    except ValueError:
        pass

    assert mirror.count() == 1
    assert store.inspect()["primary_compatible"] is False
    assert store.inspect()["primary_vector_size"] == 4
    assert store.reconcile() == {"pushed": 0, "pulled": 0}
    assert store.add(
        text="현재 임베더 지식",
        metadata={},
        vector=[1.0, 0.0],
        timestamp="2026-01-02T00:00:00",
    )
    assert primary.add_called is False
    assert mirror.count() == 2
