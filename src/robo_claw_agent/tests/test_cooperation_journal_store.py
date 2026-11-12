"""협동 저널 SQLite writer 테스트 (ROS2 불필요).

writer 스레드가 connection 을 소유하고 스스로 닫는지, close 타임아웃/큐 포화가
버린 건수와 오류로 노출되는지, 쓰기 실패가 rollback·카운트되는지, 새 파일이
비공개 권한(디렉터리 0700 / 파일 0600)으로 만들어지고 기존 디렉터리 권한은
건드리지 않는지 검증한다.

참고: 보존 행이 남아 재시작 후에도 조회된다는 것만 보장한다. 이벤트로부터 GUI
상태를 복원(restore)하는 기능은 없다.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import threading
import time
from pathlib import Path

import pytest
from robo_claw_agent.skills.cooperation_skill import journal_store
from robo_claw_agent.skills.cooperation_skill.journal import CooperationJournal

pytestmark = pytest.mark.unit


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _rows(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM cooperation_events").fetchone()[0])


def test_writer_thread_owns_and_closes_its_connection(tmp_path: Path):
    path = tmp_path / "cooperation.sqlite3"
    journal = CooperationJournal(local_robot_id="robot_a", path=path)
    writer = journal._writer
    assert writer is not None

    journal.record("decision", content="한 건")
    journal.close()
    journal.close()  # 두 번째 close 도 안전해야 한다.

    assert writer._connection is None
    assert writer._thread is not None and not writer._thread.is_alive()
    assert _rows(path) == 1
    assert journal.persistence_stats()["written"] == 1


def test_close_timeout_reports_error_and_counts_unflushed_records(tmp_path: Path):
    journal = CooperationJournal(
        local_robot_id="robot_a", path=tmp_path / "cooperation.sqlite3", event_queue_size=4
    )
    writer = journal._writer
    assert writer is not None
    gate = threading.Event()
    original = writer._write_event

    def blocked_write(event) -> None:  # type: ignore[no-untyped-def]
        gate.wait(timeout=5.0)
        original(event)

    writer._write_event = blocked_write  # type: ignore[method-assign]
    for index in range(8):
        journal.record("decision", content=f"e{index}")

    journal.close(timeout=0.01)

    assert writer.error is not None and "close_timeout" in writer.error
    assert writer.dropped >= 1

    gate.set()
    journal.close(timeout=2.0)
    assert writer._connection is None


def test_close_timeout_still_terminates_without_a_second_close(tmp_path: Path):
    journal = CooperationJournal(local_robot_id="robot_a", path=tmp_path / "close.sqlite3")
    writer = journal._writer
    assert writer is not None and writer._thread is not None
    entered = threading.Event()
    release = threading.Event()
    original = writer._write_event

    def blocked_write(event):
        entered.set()
        assert release.wait(timeout=2.0)
        original(event)

    writer._write_event = blocked_write
    try:
        journal.record("decision", content="in flight")
        assert entered.wait(timeout=1.0)
        journal.record("decision", content="queued")
        journal.close(timeout=0.0)
        release.set()
        writer._thread.join(timeout=0.5)
        assert not writer._thread.is_alive()
        assert writer._connection is None
        assert writer.dropped >= 1
    finally:
        release.set()
        journal.close(timeout=1.0)


def test_write_failure_rolls_back_and_is_counted(tmp_path: Path, monkeypatch):
    path = tmp_path / "cooperation.sqlite3"
    journal = CooperationJournal(local_robot_id="robot_a", path=path)
    writer = journal._writer
    assert writer is not None
    original_dump = journal_store.dump_details
    calls = {"n": 0}

    def failing_dump(details):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("serialisation exploded")
        return original_dump(details)

    monkeypatch.setattr(journal_store, "dump_details", failing_dump)
    journal.record("decision", content="첫 번째")
    assert _wait_for(lambda: journal.persistence_stats()["failures"] == 1)
    assert journal.persistence_stats()["error"]

    journal.record("decision", content="두 번째")
    assert _wait_for(lambda: journal.persistence_stats()["written"] == 1)
    journal.close()

    assert _rows(path) == 1


def test_new_state_files_are_private(tmp_path: Path):
    path = tmp_path / "nested" / "cooperation" / "robot_a.sqlite3"

    journal = CooperationJournal(local_robot_id="robot_a", path=path)
    journal.record("decision", content="권한")
    journal.close()

    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path.parent.parent).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_existing_directories_keep_their_permissions(tmp_path: Path):
    existing = tmp_path / "existing"
    existing.mkdir(mode=0o755)
    os.chmod(existing, 0o755)
    path = existing / "robot_a.sqlite3"

    journal = CooperationJournal(local_robot_id="robot_a", path=path)
    journal.record("decision", content="권한")
    journal.close()

    assert stat.S_IMODE(os.stat(existing).st_mode) == 0o755
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_persistence_stats_exposes_failures_and_pending(tmp_path: Path):
    journal = CooperationJournal(local_robot_id="robot_a", path=tmp_path / "c.sqlite3")
    stats = journal.persistence_stats()

    assert stats["failures"] == 0
    assert "pending" in stats
    assert stats["enabled"] is True
    journal.close()
