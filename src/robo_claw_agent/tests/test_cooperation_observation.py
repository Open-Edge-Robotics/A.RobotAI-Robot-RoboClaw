"""협동 관측(로그/스냅샷/상관 메모리/상태 표기) 테스트 (ROS2 불필요).

저널이 남기는 관측이 (a) 서버 로그로도 sanitized 되어 나오고, (b) 관측 API 에는
압축되어 나가며, (c) 태스크 상관 정보와 전달 확인을 내부에 기억하는지 검증한다.
"""

from __future__ import annotations

import json
import logging

import pytest
from robo_claw_agent.skills.cooperation_skill.journal import (
    CooperationJournal,
    reset_cooperation_journal,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "field", ["peer_name", "message_id", "task_id", "session_id", "reply_to", "result"]
)
def test_oversized_service_metadata_is_rejected_before_queueing(field):
    from robo_claw_agent.skills.cooperation_skill.inbox import PeerMessageInbox
    from robo_claw_agent.skills.cooperation_skill.receive import receive_peer_message

    inbox = PeerMessageInbox()
    instance = CooperationJournal()
    arguments = {"peer_name": "robot-b", "message": "inspect", field: "x" * 5000}
    try:
        outcome = receive_peer_message(**arguments, inbox=inbox, journal=instance)
        assert outcome.accepted is False
        assert inbox.drain_messages() == []
        assert instance.snapshot()["tasks"] == []
    finally:
        instance.close()


def test_event_identity_fields_are_bounded_and_masked():
    instance = CooperationJournal(local_robot_id="robot-a")
    try:
        instance.record(
            "x" * 5000,
            peer_id="token=fake-sensitive-name",
            task_id="t" * 5000,
            session_id="s" * 5000,
            message_id="m" * 5000,
            reply_to="r" * 5000,
        )
        event = instance.snapshot()["events"][0]
        assert len(event["event_type"]) <= 48
        assert all(
            len(event[key]) <= 64 for key in ["task_id", "session_id", "message_id", "reply_to"]
        )
        assert "fake-sensitive-name" not in json.dumps(event)
    finally:
        instance.close()


def test_event_log_failure_cannot_raise_even_if_diagnostic_logging_is_broken(monkeypatch):
    from robo_claw_agent.skills.cooperation_skill import journal_event

    def broken(*args, **kwargs):
        raise RuntimeError("logging sink unavailable")

    monkeypatch.setattr(journal_event.logger, "info", broken)
    monkeypatch.setattr(journal_event.logger, "debug", broken)
    instance = CooperationJournal(local_robot_id="robot-a")
    try:
        instance.record("message_received", content="still recorded")
        assert instance.snapshot()["events"][0]["content"] == "still recorded"
    finally:
        instance.close()


LOG_PREFIX = "[CooperationEvent]"


@pytest.fixture(autouse=True)
def _reset_global_journal():
    reset_cooperation_journal()
    yield
    reset_cooperation_journal()


def _event_json(line: str) -> dict:
    start = line.index(LOG_PREFIX) + len(LOG_PREFIX)
    return json.loads(line[start:].strip())


# ---------------------------------------------------------------------------
# 로그 기록
# ---------------------------------------------------------------------------


def test_record_emits_sanitized_structured_log_line(caplog: pytest.LogCaptureFixture):
    journal = CooperationJournal(local_robot_id="robot_a")

    with caplog.at_level(logging.INFO):
        journal.record(
            "message_received",
            peer_id="robot_b",
            task_id="task-1",
            message_id="msg-1",
            reply_to="msg-0",
            session_id="session-1",
            status="received",
            content="Bearer supersecret-token " + ("가" * 4000),
            details={"auth_token": "supersecret-token", "reason": "ok"},
        )

    line = next(
        record.getMessage() for record in caplog.records if LOG_PREFIX in record.getMessage()
    )
    payload = _event_json(line)

    assert payload["event_type"] == "message_received"
    assert payload["peer_id"] == "robot_b"
    assert payload["task_id"] == "task-1"
    assert payload["message_id"] == "msg-1"
    assert payload["reply_to"] == "msg-0"
    assert payload["session_id"] == "session-1"
    assert payload["status"] == "received"
    assert payload["details"]["auth_token"] == "***"
    assert payload["details"]["reason"] == "ok"
    assert "supersecret-token" not in line
    assert len(payload["content"]) <= 2048
    journal.close()


def test_record_survives_broken_logging_handler():
    journal = CooperationJournal(local_robot_id="robot_a")

    class BrokenHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise RuntimeError("handler exploded")

    handler = BrokenHandler()
    logger = logging.getLogger("robo_claw_agent.skills.cooperation_skill.journal_event")
    logger.addHandler(handler)
    previous_disable = logging.raiseExceptions
    logging.raiseExceptions = True
    try:
        sequence = journal.record("decision", content="핸들러가 깨져도 기록")
    finally:
        logging.raiseExceptions = previous_disable
        logger.removeHandler(handler)

    assert sequence == 1
    assert journal.snapshot()["events"][0]["content"] == "핸들러가 깨져도 기록"
    journal.close()


# ---------------------------------------------------------------------------
# 스냅샷 압축 / 커서
# ---------------------------------------------------------------------------


def test_snapshot_oldest_cursor_tracks_returned_window():
    journal = CooperationJournal(local_robot_id="robot_a")
    for index in range(5):
        journal.record("decision", content=f"e{index}")

    snapshot = journal.snapshot(max_events=2)

    assert [event["content"] for event in snapshot["events"]] == ["e3", "e4"]
    assert snapshot["oldest_cursor"] == snapshot["events"][0]["sequence"] == 4
    assert snapshot["next_cursor"] == 6
    journal.close()


def test_snapshot_compacts_api_content_and_details_but_keeps_raw_event():
    journal = CooperationJournal(local_robot_id="robot_a")
    for index in range(3):
        journal.record(
            "decision",
            content=f"{index}-" + ("가" * 2000),
            details={"note": "나" * 1500, "reason": "ok"},
        )

    compact = journal.snapshot(content_limit=512, detail_budget=512)
    raw = journal.snapshot(content_limit=2048, detail_budget=2048)

    assert len(compact["events"]) == 3
    for event in compact["events"]:
        assert len(event["content"]) <= 512
        assert len(json.dumps(event["details"], ensure_ascii=False)) <= 512
    assert len(raw["events"][0]["content"]) > 512
    assert len(json.dumps(raw["events"][0]["details"], ensure_ascii=False)) > 512
    journal.close()


# ---------------------------------------------------------------------------
# 태스크 메타데이터 불변 / 상관 메모리
# ---------------------------------------------------------------------------


def test_upsert_task_does_not_overwrite_existing_peer_session_or_description():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.upsert_task(
        "task-1",
        peer_id="robot_b",
        session_id="session-b",
        description="거실 정리",
        status="queued",
    )

    journal.upsert_task(
        "task-1",
        peer_id="robot_c",
        session_id="session-c",
        description="다른 설명",
    )

    task = journal.task("task-1")
    assert task["peer_id"] == "robot_b"
    assert task["session_id"] == "session-b"
    assert task["description"] == "거실 정리"
    assert task["status"] == "queued"
    journal.close()


def test_outbound_message_correlation_is_remembered_and_bounded():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.upsert_task("task-1", peer_id="robot_b", status="queued")

    for index in range(12):
        journal.note_outbound_message("task-1", f"out-{index}")

    assert journal.is_outbound_message("task-1", "out-11") is True
    assert journal.is_outbound_message("task-1", "out-0") is False
    assert journal.is_outbound_message("task-1", "") is False
    assert journal.is_outbound_message("unknown-task", "out-11") is False
    journal.note_outbound_message("task-1", "out-11")
    assert journal.outbound_message_count("task-1") <= 8
    journal.close()


def test_delivery_confirmation_marks_flag_without_completing_task():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.upsert_task("task-1", peer_id="robot_b", status="queued")
    journal.note_outbound_message("task-1", "out-1")

    assert (
        journal.mark_delivery_confirmed(
            "task-1", peer_id="robot_b", message_id="reply-1", reply_to="out-1"
        )
        is True
    )

    task = journal.task("task-1")
    assert task["delivery_confirmed"] is True
    assert task["status"] == "queued"
    events = journal.snapshot()["events"]
    assert events[-1]["event_type"] == "delivery_confirmed"
    assert events[-1]["details"]["delivery_confirmed"] is True
    assert events[-1]["reply_to"] == "out-1"
    journal.close()


def test_delivery_confirmation_rejects_mismatched_or_unknown_correlation():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.upsert_task("task-1", peer_id="robot_b", status="queued")
    journal.note_outbound_message("task-1", "out-1")

    assert (
        journal.mark_delivery_confirmed(
            "task-1", peer_id="robot_b", message_id="reply-1", reply_to="other"
        )
        is False
    )
    assert (
        journal.mark_delivery_confirmed(
            "task-1", peer_id="robot_c", message_id="reply-1", reply_to="out-1"
        )
        is False
    )
    assert (
        journal.mark_delivery_confirmed(
            "unknown-task", peer_id="robot_b", message_id="reply-1", reply_to="out-1"
        )
        is False
    )
    assert journal.task("task-1")["delivery_confirmed"] is False
    assert "delivery_confirmed" not in [
        event["event_type"] for event in journal.snapshot()["events"]
    ]
    journal.close()


# ---------------------------------------------------------------------------
# status 는 태스크 상태만 담는다
# ---------------------------------------------------------------------------


def test_session_events_do_not_put_lifecycle_state_into_status():
    journal = CooperationJournal(local_robot_id="robot_a")

    journal.start_session(goal="정리")
    journal.stop_session(reason="user_stop")

    events = journal.snapshot()["events"]
    assert events[0]["event_type"] == "session_started"
    assert events[0]["status"] == ""
    assert events[0]["details"]["state"] == "started"
    assert events[1]["event_type"] == "session_stopped"
    assert events[1]["status"] == ""
    assert events[1]["details"]["state"] == "stopped"
    journal.close()


# ---------------------------------------------------------------------------
# 결과 출처 표시 (dashboard 계약)
# ---------------------------------------------------------------------------


def test_local_execution_result_is_marked_observed_false():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.upsert_task("task-1", peer_id="robot_b", status="queued")

    journal.update_task("task-1", status="completed", result="로컬 완료", observed=False)

    task = journal.task("task-1")
    assert task["observed"] is False
    assert task["origin"] == ""


def test_peer_reported_result_sets_observed_and_origin():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.upsert_task("task-1", peer_id="robot_b", status="queued")
    journal.note_outbound_message("task-1", "out-1")

    assert journal.mark_observed_result("task-1", status="completed", result="상대 보고") is True

    task = journal.task("task-1")
    assert task["observed"] is True
    assert task["origin"] == "peer_report"
    assert task["outbound_message_id"] == "out-1"


def test_outbound_message_id_tracks_last_sent_message():
    journal = CooperationJournal(local_robot_id="robot_a")
    journal.upsert_task("task-1", peer_id="robot_b", status="queued")

    journal.note_outbound_message("task-1", "out-1")
    journal.note_outbound_message("task-1", "out-2")

    assert journal.task("task-1")["outbound_message_id"] == "out-2"
    journal.close()
