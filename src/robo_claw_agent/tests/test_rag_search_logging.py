import json
from unittest.mock import Mock

import pytest
from robo_claw_agent.memory_manager import rag

pytestmark = pytest.mark.unit


@pytest.fixture
def searcher(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "_SEARCH_DUMP_ENABLED", True)
    monkeypatch.setattr(rag, "_SEARCH_DUMP_DIR", tmp_path)
    obj = rag.RAGMixin()
    obj._embedder = Mock()
    obj._embedder.embed_query.return_value = [0.1, 0.2]
    obj._vector_store = Mock()
    obj._vector_store.count.return_value = 2
    obj._vector_store.inspect.return_value = {"backend": "fake"}
    obj._rag_score_threshold = 0.7
    obj._metrics = Mock()
    return obj


@pytest.mark.parametrize("filters", [{"type_exclude": ["skill_lesson"]}, {"type": "skill_lesson"}])
def test_search_json_preserves_results_and_filters(searcher, tmp_path, filters):
    results = [{"id": "one", "text": "배터리", "score": 0.81, "metadata": {"type": "skill_lesson"}}]
    searcher._vector_store.search.return_value = results
    assert searcher.search_knowledge("배터리", 5, filter_metadata=filters) is results
    record = json.loads(next(tmp_path.glob("rag_search_*.json")).read_text())
    assert record["request"]["query"] == "배터리"
    assert record["request"]["filter_metadata"] == filters
    assert record["request"]["score_threshold"] == 0.7
    assert record["results"] == results
    assert record["status"] == "success"
    assert record["result_count"] == 1
    assert record["search_kind"] == ("skill_lessons" if filters.get("type") else "knowledge")


@pytest.mark.parametrize("case", ["empty", "error", "no_embedder", "empty_store"])
def test_search_json_distinguishes_empty_error_and_skipped(searcher, tmp_path, case):
    searcher._vector_store.search.return_value = []
    if case == "error":
        searcher._vector_store.search.side_effect = TimeoutError("backend timeout")
    elif case == "no_embedder":
        searcher._embedder = None
    elif case == "empty_store":
        searcher._vector_store.count.return_value = 0
    assert searcher.search_knowledge("query") == []
    record = json.loads(next(tmp_path.glob("rag_search_*.json")).read_text())
    assert record["results"] == []
    assert record["status"] == (
        "error"
        if case == "error"
        else "skipped"
        if case in ("no_embedder", "empty_store")
        else "success"
    )
    if case == "error":
        assert record["error"] == "TimeoutError: backend timeout"


def test_dump_failure_does_not_change_search_results(searcher, tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    monkeypatch.setattr(rag, "_SEARCH_DUMP_DIR", blocked)
    searcher._vector_store.search.return_value = [{"text": "ok"}]
    assert searcher.search_knowledge("query") == [{"text": "ok"}]


def test_suppressed_search_does_not_write_json(searcher, tmp_path):
    from robo_claw_agent.tracing import suppress_call_logging

    searcher._vector_store.search.return_value = []
    with suppress_call_logging():
        assert searcher.search_knowledge("probe") == []
    assert list(tmp_path.iterdir()) == []


def test_store_count_failure_is_recorded(searcher, tmp_path):
    searcher._vector_store.count.side_effect = ConnectionError("offline")
    assert searcher.search_knowledge("query") == []
    record = json.loads(next(tmp_path.glob("rag_search_*.json")).read_text())
    assert record["status"] == "error"
    assert record["error"] == "ConnectionError: offline"
