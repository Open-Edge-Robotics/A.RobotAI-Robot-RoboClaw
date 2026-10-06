"""
SQLite 백엔드 및 대화 히스토리 추가 테스트
"""

import pytest
from robo_claw_agent.memory_manager import MemoryManager


@pytest.fixture
def sqlite_memory(tmp_path):
    path = str(tmp_path / "mem.db")
    mgr = MemoryManager(storage_path=path, backend="sqlite", max_short_term=3)
    yield mgr


@pytest.fixture
def json_memory(tmp_path):
    path = str(tmp_path / "mem.json")
    mgr = MemoryManager(storage_path=path, backend="json", max_short_term=3)
    yield mgr


class TestSQLiteBackend:
    def test_add_and_get_recent(self, sqlite_memory):
        sqlite_memory.add_event("test", {"val": 1})
        recent = sqlite_memory.get_recent(10)
        assert len(recent) == 1
        assert recent[0]["type"] == "test"

    def test_overflow_to_db(self, sqlite_memory):
        """단기 메모리 한도 초과 시 SQLite에 저장"""
        for i in range(5):
            sqlite_memory.add_event("evt", {"i": i})
        # 단기는 max_short_term=3 유지
        assert len(sqlite_memory._short_term) == 3
        # DB에 2개 저장
        assert sqlite_memory._count_sqlite() == 2

    def test_flush(self, sqlite_memory):
        sqlite_memory.add_event("x", {"v": 1})
        sqlite_memory.flush()
        assert len(sqlite_memory._short_term) == 0
        assert sqlite_memory._count_sqlite() == 1

    def test_clear_all(self, sqlite_memory):
        sqlite_memory.add_event("a", {})
        sqlite_memory.flush()
        sqlite_memory.clear_all()
        assert sqlite_memory._count_sqlite() == 0
        assert len(sqlite_memory._short_term) == 0

    def test_summary_backend_field(self, sqlite_memory):
        s = sqlite_memory.summary()
        assert s["backend"] == "sqlite"


class TestConversationHistory:
    def test_empty_history(self, json_memory):
        hist = json_memory.get_conversation_history(5)
        assert hist == []

    def test_single_turn(self, json_memory):
        json_memory.add_event("task_received", {"instruction": "앞으로 가줘"})
        json_memory.add_event("task_completed", {"result": "이동 완료"})
        hist = json_memory.get_conversation_history(5)
        assert len(hist) == 2
        assert hist[0]["role"] == "user"
        assert hist[0]["content"] == "앞으로 가줘"
        assert hist[1]["role"] == "assistant"
        assert hist[1]["content"] == "이동 완료"

    def test_ignores_non_task_events(self, json_memory):
        json_memory.add_event("skill_executed", {"skill_name": "navigate"})
        json_memory.add_event("task_received", {"instruction": "정지해줘"})
        hist = json_memory.get_conversation_history(5)
        # skill_executed 이벤트는 대화 히스토리에 포함되지 않음
        assert len(hist) == 1
        assert hist[0]["role"] == "user"

    def test_history_limit(self, json_memory):
        for i in range(5):
            json_memory.add_event("task_received", {"instruction": f"명령{i}"})
            json_memory.add_event("task_completed", {"result": f"완료{i}"})
        # n=2 → 최대 4개 메시지(2턴)
        hist = json_memory.get_conversation_history(2)
        assert len(hist) <= 4

    def test_sqlite_conversation_history(self, sqlite_memory):
        sqlite_memory.add_event("task_received", {"instruction": "회전해줘"})
        sqlite_memory.add_event("task_completed", {"result": "회전 완료"})
        hist = sqlite_memory.get_conversation_history(5)
        assert any(m["role"] == "user" for m in hist)
        assert any(m["role"] == "assistant" for m in hist)


def test_rag_ttl_expiration_and_archiving(tmp_path):
    """RAG 지식 중 7일이 경과한 'observation' 데이터를 만료시키고 SQLite 콜드 스토리지에 아카이빙하는 기능 검증"""
    import json
    import sqlite3
    from datetime import datetime, timedelta
    from unittest.mock import MagicMock

    from robo_claw_agent.memory_manager import MemoryManager

    # 1. MemoryManager 생성 및 모킹용 임베더 주입
    path = str(tmp_path / "mem.json")
    mgr = MemoryManager(storage_path=path, backend="json")

    mock_embedder = MagicMock()
    mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
    mgr.set_embedder(mock_embedder)

    # 2. RAG 지식베이스에 복수의 지식 적재
    # 지식 1: 10일 전 생성된 관찰 데이터 (만료 대상)
    ten_days_ago = (datetime.now() - timedelta(days=10)).isoformat()
    mgr._vector_store.add(
        text="10일 전 발견된 쓰레기통 위치 x=1.0 y=1.0",
        metadata={"type": "observation", "x": 1.0, "y": 1.0},
        vector=[0.1, 0.2, 0.3],
        timestamp=ten_days_ago
    )

    # 지식 2: 오늘 생성된 관찰 데이터 (보관 대상)
    today = datetime.now().isoformat()
    mgr._vector_store.add(
        text="오늘 감지된 테이블 위치 x=2.0 y=2.0",
        metadata={"type": "observation", "x": 2.0, "y": 2.0},
        vector=[0.1, 0.2, 0.3],
        timestamp=today
    )

    # 지식 3: 10일 전 생성된 매뉴얼 가이드 (보관 대상 - metadata.type이 'observation'이 아님)
    mgr._vector_store.add(
        text="로봇 하드웨어 안전 수칙",
        metadata={"type": "manual"},
        vector=[0.1, 0.2, 0.3],
        timestamp=ten_days_ago
    )

    # 초기 상태 검증
    assert mgr._vector_store.count() == 3

    # 3. 7일 기준 만료 및 아카이빙 실행
    result = mgr.consolidate_and_evict_knowledge(ttl_days=7)

    assert result["success"] is True
    assert result["evicted_count"] == 1

    # Active 인덱스 크기는 2여야 함 (오늘 관찰 + 10일 전 매뉴얼)
    assert mgr._vector_store.count() == 2

    # 4. SQLite 콜드 아카이브 파일(memory.cold.db) 데이터 적재 검증
    cold_db_path = result["cold_db_path"]
    with sqlite3.connect(cold_db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, text, metadata, timestamp FROM cold_knowledge_backup")
        rows = cursor.fetchall()

        assert len(rows) == 1
        assert rows[0][1] == "10일 전 발견된 쓰레기통 위치 x=1.0 y=1.0"

        meta = json.loads(rows[0][2])
        assert meta["type"] == "observation"
        assert meta["x"] == 1.0
        assert rows[0][3] == ten_days_ago

