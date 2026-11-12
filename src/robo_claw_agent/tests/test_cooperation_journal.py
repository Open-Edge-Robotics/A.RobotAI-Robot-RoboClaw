"""협동 관측 저널 테스트 (ROS2 불필요).

메모리 링·커서·태스크 상태, 민감정보 마스킹과 크기 상한, 비동기 SQLite 저장의
재시작/보존 한도, 큐 포화/디스크 실패 시 비차단 동작을 검증한다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from robo_claw_agent.skills.cooperation_skill.journal import (
    CooperationJournal,
    configure_cooperation_journal,
    default_cooperation_state_path,
    get_cooperation_journal,
    reset_cooperation_journal,
    safe_robot_id,
)
from robo_claw_agent.skills.cooperation_skill.sanitize import (
    MAX_CONTENT_CHARS,
    MAX_DETAIL_CHARS,
    MAX_DETAIL_ITEMS,
    sanitize_details,
    sanitize_text,
)

pytestmark = pytest.mark.unit

COOPERATION_KEYS = {
    "available",
    "enabled",
    "active",
    "local_robot_id",
    "session_id",
    "goal",
    "stream_id",
    "events",
    "tasks",
    "next_cursor",
    "oldest_cursor",
    "dropped_events",
    "persistence",
}
EVENT_KEYS = {
    "sequence",
    "event_type",
    "timestamp",
    "robot_id",
    "peer_id",
    "session_id",
    "task_id",
    "message_id",
    "reply_to",
    "status",
    "content",
    "details",
}
TASK_KEYS = {
    "task_id",
    "session_id",
    "peer_id",
    "description",
    "status",
    "created_at",
    "updated_at",
    "result",
    "delivery_confirmed",
    "observed",
    "origin",
    "outbound_message_id",
}


@pytest.fixture(autouse=True)
def _reset_global_journal():
    reset_cooperation_journal()
    yield
    reset_cooperation_journal()


# ---------------------------------------------------------------------------
# 메모리 링 / 커서
# ---------------------------------------------------------------------------


def test_memory_journal_is_available_without_storage():
    journal = CooperationJournal(local_robot_id="robot_a", path=None)
    snapshot = journal.snapshot()

    assert set(snapshot) == COOPERATION_KEYS
    assert snapshot["available"] is True
    assert snapshot["local_robot_id"] == "robot_a"
    assert snapshot["events"] == []
    assert snapshot["tasks"] == []
    assert snapshot["oldest_cursor"] == 0
    assert snapshot["next_cursor"] == 1
    assert snapshot["dropped_events"] == 0
    assert snapshot["persistence"] == {"enabled": False, "error": None}
    journal.close()


def test_event_shape_matches_contract():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.record(
        "message_received",
        peer_id="robot_b",
        task_id="task-1",
        message_id="msg-1",
        status="received",
        content="안녕",
        details={"action": "respond"},
    )

    event = journal.snapshot()["events"][0]

    assert set(event) == EVENT_KEYS
    assert event["sequence"] == 1
    assert event["event_type"] == "message_received"
    assert event["robot_id"] == "robot_a"
    assert event["peer_id"] == "robot_b"
    assert event["task_id"] == "task-1"
    assert event["message_id"] == "msg-1"
    assert event["status"] == "received"
    assert event["details"] == {"action": "respond"}
    journal.close()


def test_event_ring_is_bounded_and_counts_dropped_events():
    journal = CooperationJournal(local_robot_id="robot_a", max_events=3)
    for index in range(5):
        journal.record("decision", content=f"e{index}")

    snapshot = journal.snapshot()

    assert [e["content"] for e in snapshot["events"]] == ["e2", "e3", "e4"]
    assert snapshot["dropped_events"] == 2
    assert snapshot["oldest_cursor"] == 3
    assert snapshot["next_cursor"] == 6
    journal.close()


def test_snapshot_is_a_copy_of_internal_state():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.record("decision", details={"nested": {"value": 1}})
    journal.upsert_task("task-1", peer_id="robot_b", description="컵 옮기기", status="queued")

    snapshot = journal.snapshot()
    snapshot["events"][0]["details"]["nested"]["value"] = 99
    snapshot["tasks"][0]["status"] = "completed"

    fresh = journal.snapshot()
    assert fresh["events"][0]["details"]["nested"]["value"] == 1
    assert fresh["tasks"][0]["status"] == "queued"
    journal.close()


def test_concurrent_snapshots_and_records_are_thread_safe():
    journal = CooperationJournal(local_robot_id="robot_a", max_events=200)
    errors: list[BaseException] = []
    snapshots: list[dict] = []

    def record_worker(worker: int) -> None:
        try:
            for index in range(25):
                journal.record("decision", content=f"{worker}-{index}", details={"w": worker})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def snapshot_worker() -> None:
        try:
            for _ in range(50):
                snapshots.append(journal.snapshot())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=record_worker, args=(n,)) for n in range(4)]
    threads += [threading.Thread(target=snapshot_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10.0)

    assert errors == []
    sequences = [event["sequence"] for event in journal.snapshot()["events"]]
    assert sequences == sorted(set(sequences))
    assert len(sequences) == 100
    assert all(set(snapshot) == COOPERATION_KEYS for snapshot in snapshots)
    journal.close()


# ---------------------------------------------------------------------------
# 세션 / 태스크 상태
# ---------------------------------------------------------------------------


def test_session_start_and_stop_record_events_semantically():
    journal = CooperationJournal(local_robot_id="robot_a")

    session_id = journal.start_session(goal="거실 정리")
    snapshot = journal.snapshot()

    assert session_id
    assert snapshot["session_id"] == session_id
    assert snapshot["goal"] == "거실 정리"
    assert snapshot["active"] is True
    assert [e["event_type"] for e in snapshot["events"]] == ["session_started"]

    journal.stop_session(reason="user_stop")
    snapshot = journal.snapshot()

    assert snapshot["active"] is False
    assert [e["event_type"] for e in snapshot["events"]] == ["session_started", "session_stopped"]
    journal.close()


def test_session_stop_does_not_mark_running_task_cancelled():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.start_session(goal="정리")
    journal.upsert_task("task-1", peer_id="robot_b", description="거실로 이동", status="running")

    journal.stop_session(reason="local_stop")

    assert journal.task_status("task-1") == "running"
    assert journal.task("task-1")["status"] == "running"
    journal.close()


def test_task_shape_and_status_transitions():
    journal = CooperationJournal(local_robot_id="robot_a")

    journal.upsert_task(
        "task-1",
        peer_id="robot_b",
        session_id="session-1",
        description="컵 옮기기",
        status="queued",
    )
    task = journal.snapshot()["tasks"][0]

    assert set(task) == TASK_KEYS
    assert task["description"] == "컵 옮기기"
    assert task["status"] == "queued"
    assert task["delivery_confirmed"] is False
    assert task["observed"] is False
    assert task["origin"] == ""
    assert task["outbound_message_id"] == ""
    assert task["created_at"] <= task["updated_at"]

    journal.update_task("task-1", status="running")
    journal.update_task("task-1", status="completed", result="완료")
    task = journal.task("task-1")

    assert task["status"] == "completed"
    assert task["result"] == "완료"
    journal.close()


def test_task_store_is_bounded_and_snapshot_limits_tasks():
    journal = CooperationJournal(local_robot_id="robot_a", max_tasks=5, max_task_records=6)
    for index in range(10):
        journal.upsert_task(f"task-{index}", peer_id="robot_b", status="queued")

    snapshot = journal.snapshot()

    assert len(journal.snapshot(max_tasks=1000)["tasks"]) <= 6
    assert len(snapshot["tasks"]) == 5
    journal.close()


def test_maestro_disabled_snapshot_is_explicit():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.start_session(goal="정리")

    snapshot = journal.snapshot(enabled=False)

    assert snapshot["available"] is True
    assert snapshot["enabled"] is False
    assert snapshot["active"] is False
    journal.close()


# ---------------------------------------------------------------------------
# 민감정보 마스킹 / 크기 상한
# ---------------------------------------------------------------------------


def test_sensitive_detail_keys_are_masked_recursively():
    details = sanitize_details(
        {
            "auth_token": "secret-token",
            "password": "p",
            "nested": {"private_key": "k", "api_key": "a", "ok": "v"},
            "items": [{"refresh_token": "r"}],
        }
    )

    assert details["auth_token"] == "***"
    assert details["password"] == "***"
    assert details["nested"]["private_key"] == "***"
    assert details["nested"]["api_key"] == "***"
    assert details["nested"]["ok"] == "v"
    assert details["items"][0]["refresh_token"] == "***"


def test_sensitive_text_patterns_are_masked():
    masked = sanitize_text("Authorization: Bearer abc.def token=plain-secret 끝")

    assert "abc.def" not in masked
    assert "plain-secret" not in masked
    assert masked.endswith("끝")


def test_content_and_details_are_bounded():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.record(
        "decision",
        content="가" * 10000,
        details={
            "deep": {"a": {"b": {"c": {"d": {"e": {"f": "너무 깊음"}}}}}},
            "many": list(range(500)),
        },
    )

    event = journal.snapshot()["events"][0]

    assert len(event["content"]) <= MAX_CONTENT_CHARS
    assert len(json.dumps(event["details"], ensure_ascii=False)) <= MAX_DETAIL_CHARS
    assert len(event["details"]["many"]) <= MAX_DETAIL_ITEMS
    journal.close()


# ---------------------------------------------------------------------------
# 비동기 SQLite 저장
# ---------------------------------------------------------------------------


def _count_rows(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM cooperation_events").fetchone()[0])


def test_events_are_persisted_asynchronously(tmp_path: Path):
    path = tmp_path / "cooperation.sqlite3"
    journal = CooperationJournal(local_robot_id="robot_a", path=path)

    journal.record("message_received", peer_id="robot_b", content="안녕", details={"token": "x"})
    journal.close()

    assert _count_rows(path) == 1
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT event_type, peer_id, content, details FROM cooperation_events"
        ).fetchone()
    assert row[0] == "message_received"
    assert row[1] == "robot_b"
    assert row[2] == "안녕"
    assert "x" != json.loads(row[3])["token"]


def test_persistence_disabled_without_path_keeps_error_clear():
    journal = CooperationJournal(local_robot_id="robot_a", path=None)

    assert journal.persistence_stats()["enabled"] is False
    assert journal.snapshot()["persistence"] == {"enabled": False, "error": None}
    journal.close()


def test_restart_keeps_previous_rows(tmp_path: Path):
    path = tmp_path / "cooperation.sqlite3"
    first = CooperationJournal(local_robot_id="robot_a", path=path)
    first.record("session_started", content="first")
    first.close()

    second = CooperationJournal(local_robot_id="robot_a", path=path)
    second.record("session_started", content="second")
    second.close()

    assert _count_rows(path) == 2


def test_retention_is_bounded(tmp_path: Path):
    path = tmp_path / "cooperation.sqlite3"
    journal = CooperationJournal(local_robot_id="robot_a", path=path, retention_rows=5)
    for index in range(20):
        journal.record("decision", content=f"e{index}")
    journal.close()

    assert _count_rows(path) == 5


def test_queue_overflow_is_non_blocking_and_counted(tmp_path: Path):
    path = tmp_path / "cooperation.sqlite3"
    journal = CooperationJournal(local_robot_id="robot_a", path=path, event_queue_size=1)
    gate = threading.Event()
    original_write = journal._writer._write_event

    def blocked_write(event) -> None:  # type: ignore[no-untyped-def]
        gate.wait(timeout=5.0)
        original_write(event)

    journal._writer._write_event = blocked_write  # type: ignore[method-assign]
    started = time.monotonic()
    for index in range(50):
        journal.record("decision", content=f"e{index}")
    elapsed = time.monotonic() - started
    gate.set()
    journal.close()

    assert elapsed < 2.0
    assert journal.snapshot()["dropped_events"] > 0
    assert len(journal.snapshot()["events"]) == 50


def test_storage_failure_is_reported_without_blocking(tmp_path: Path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    journal = CooperationJournal(local_robot_id="robot_a", path=blocker / "cooperation.sqlite3")

    sequence = journal.record("decision", content="실패해도 기록")
    snapshot = journal.snapshot()

    assert sequence == 1
    assert snapshot["persistence"]["enabled"] is False
    assert snapshot["persistence"]["error"]
    assert snapshot["events"][0]["content"] == "실패해도 기록"
    journal.close()


def test_writer_error_is_exposed_without_blocking(tmp_path: Path):
    path = tmp_path / "cooperation.sqlite3"
    journal = CooperationJournal(local_robot_id="robot_a", path=path)

    def failing_write(event) -> None:  # type: ignore[no-untyped-def]
        raise sqlite3.OperationalError("disk I/O error")

    journal._writer._write_event = failing_write  # type: ignore[method-assign]
    journal.record("decision", content="저장 실패")
    deadline = time.monotonic() + 5.0
    while journal.snapshot()["persistence"]["error"] is None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert journal.snapshot()["persistence"]["error"]
    assert journal.persistence_stats()["written"] == 0
    journal.close()


# ---------------------------------------------------------------------------
# 경로 / 전역 저널
# ---------------------------------------------------------------------------


def test_state_path_uses_xdg_state_home():
    path = default_cooperation_state_path("robot a/b", env={"XDG_STATE_HOME": "/tmp/state-home"})

    assert path == Path("/tmp/state-home/robo-claw/cooperation/robot_a_b.sqlite3")


def test_state_path_falls_back_to_local_state():
    path = default_cooperation_state_path("robot_a", env={"HOME": "/home/tester"})

    assert path == Path("/home/tester/.local/state/robo-claw/cooperation/robot_a.sqlite3")


def test_safe_robot_id_bounds_unsafe_characters():
    assert safe_robot_id("") == "robot"
    assert safe_robot_id("../../../etc/passwd") == "etc_passwd"
    assert safe_robot_id("Former 0047") == "Former_0047"


def test_global_journal_defaults_to_memory_and_can_be_reconfigured(tmp_path: Path):
    default_journal = get_cooperation_journal()
    assert default_journal.snapshot()["persistence"]["enabled"] is False

    configured = configure_cooperation_journal(
        local_robot_id="robot_a", path=tmp_path / "cooperation.sqlite3"
    )

    assert get_cooperation_journal() is configured
    assert configured.snapshot()["local_robot_id"] == "robot_a"
    assert configured.snapshot()["persistence"]["enabled"] is True
    assert configured.stream_id != default_journal.stream_id
    reset_cooperation_journal()
