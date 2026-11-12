"""협동 관측 정규화/필터/내보내기 도메인 단위 테스트.

에이전트가 QueryState 로 주는 ``cooperation`` 객체는 신뢰 경계 밖 입력이다.
여기서는 잘못된 타입·과도한 크기·시크릿이 대시보드 응답으로 새지 않는지 고정한다.
"""

from __future__ import annotations

import json

import pytest
from robo_claw_dashboard.domain import cooperation as coop

pytestmark = pytest.mark.unit


def event(sequence: int, **overrides) -> dict:
    base = {
        "sequence": sequence,
        "event_type": "task_completed",
        "timestamp": 1700000000.0 + sequence,
        "robot_id": "robot-a",
        "peer_id": "robot-b",
        "session_id": "sess-1",
        "task_id": f"task-{sequence}",
        "message_id": f"msg-{sequence}",
        "reply_to": "",
        "status": "completed",
        "content": f"event {sequence}",
        "details": {},
    }
    base.update(overrides)
    return base


def journal(**overrides) -> dict:
    base = {
        "available": True,
        "enabled": True,
        "active": True,
        "local_robot_id": "robot-a",
        "session_id": "sess-1",
        "goal": "patrol the corridor",
        "stream_id": "stream-1",
        "events": [event(1), event(2)],
        "tasks": [
            {
                "task_id": "task-1",
                "session_id": "sess-1",
                "peer_id": "robot-b",
                "description": "scan the shelf",
                "status": "queued",
                "created_at": 1.0,
                "updated_at": 2.0,
                "result": "",
            }
        ],
        "next_cursor": 3,
        "oldest_cursor": 1,
        "dropped_events": 0,
        "persistence": {"enabled": True, "error": None},
    }
    base.update(overrides)
    return base


class TestNormalizeUnavailable:
    def test_missing_cooperation_is_unavailable(self):
        payload = coop.normalize_cooperation(None)
        assert payload["available"] is False
        assert payload["enabled"] is False
        assert payload["active"] is False
        assert payload["events"] == []
        assert payload["tasks"] == []
        assert payload["error"]

    def test_non_mapping_cooperation_is_unavailable(self):
        for raw in (["x"], "cooperation", 42):
            payload = coop.normalize_cooperation(raw)
            assert payload["available"] is False
            assert payload["error"]

    def test_no_events_is_still_available(self):
        payload = coop.normalize_cooperation(journal(events=[], tasks=[]))
        assert payload["available"] is True
        assert payload["events"] == []
        assert payload["oldest_cursor"] == 1


class TestNormalizeJournal:
    def test_copies_identity_and_cursors(self):
        payload = coop.normalize_cooperation(journal())
        assert payload["local_robot_id"] == "robot-a"
        assert payload["session_id"] == "sess-1"
        assert payload["goal"] == "patrol the corridor"
        assert payload["stream_id"] == "stream-1"
        assert payload["next_cursor"] == 3
        assert payload["oldest_cursor"] == 1
        assert payload["dropped_events"] == 0
        assert payload["persistence"] == {"enabled": True, "error": None, "reported": True}
        assert [item["sequence"] for item in payload["events"]] == [1, 2]

    def test_maestro_disabled_forces_inactive(self):
        payload = coop.normalize_cooperation(journal(enabled=False, active=True))
        assert payload["available"] is True
        assert payload["enabled"] is False
        assert payload["active"] is False

    def test_events_keep_oldest_to_newest_and_cap(self):
        many = [event(sequence) for sequence in range(1, 151)]
        payload = coop.normalize_cooperation(journal(events=many, oldest_cursor=1, next_cursor=151))
        sequences = [item["sequence"] for item in payload["events"]]
        assert len(sequences) == coop.MAX_EVENTS
        assert sequences == sorted(sequences)
        assert sequences[-1] == 150
        assert payload["dropped_events"] >= 50

    def test_event_without_usable_sequence_is_counted(self):
        payload = coop.normalize_cooperation(
            journal(events=[event(1), {"sequence": "abc"}, {"event_type": "task_failed"}, "junk"])
        )
        assert [item["sequence"] for item in payload["events"]] == [1]
        assert payload["invalid_events"] == 3

    def test_unknown_status_and_event_type_are_kept_as_text(self):
        payload = coop.normalize_cooperation(
            journal(events=[event(1, status="EXPLODED", event_type="custom_thing")])
        )
        item = payload["events"][0]
        assert item["status"] == "unknown"
        assert item["event_type"] == "custom_thing"

    def test_status_is_lowercased_and_known(self):
        payload = coop.normalize_cooperation(journal(events=[event(1, status="RUNNING")]))
        assert payload["events"][0]["status"] == "running"

    def test_content_and_identifiers_are_truncated(self):
        payload = coop.normalize_cooperation(
            journal(events=[event(1, content="x" * 5000, robot_id="r" * 500)])
        )
        item = payload["events"][0]
        assert len(item["content"]) <= coop.MAX_CONTENT_CHARS
        assert len(item["robot_id"]) <= coop.MAX_TEXT_CHARS

    def test_event_fields_are_strings_even_when_agent_sends_numbers(self):
        payload = coop.normalize_cooperation(journal(events=[event(1, task_id=42, content=7)]))
        item = payload["events"][0]
        assert item["task_id"] == "42"
        assert item["content"] == "7"


class TestSecretRedaction:
    def test_secret_detail_keys_are_masked_recursively(self):
        payload = coop.normalize_cooperation(
            journal(
                events=[
                    event(
                        1,
                        details={
                            "device_token": "abc123",
                            "nested": {"http_control_token": "ctrl", "ok": 1},
                            "list": [{"password": "pw"}, "plain"],
                            "note": "keep me",
                        },
                    )
                ]
            )
        )
        details = payload["events"][0]["details"]
        assert details["device_token"] == coop.MASK
        assert details["nested"]["http_control_token"] == coop.MASK
        assert details["nested"]["ok"] == 1
        assert details["list"][0]["password"] == coop.MASK
        assert details["list"][1] == "plain"
        assert details["note"] == "keep me"

    def test_content_with_inline_token_is_masked(self):
        payload = coop.normalize_cooperation(
            journal(events=[event(1, content="sent http_readonly_token=r-secret to peer")])
        )
        assert "r-secret" not in payload["events"][0]["content"]
        assert coop.MASK in payload["events"][0]["content"]

    def test_details_are_bounded_in_depth_items_and_text(self):
        deep = {"level1": {"level2": {"level3": {"level4": {"level5": "bottom"}}}}}
        wide = {f"k{index}": index for index in range(100)}
        long_text = {"note": "y" * 5000}
        payload = coop.normalize_cooperation(
            journal(events=[event(1, details={**long_text, **deep, **wide})])
        )
        details = payload["events"][0]["details"]
        assert len(details) <= coop.MAX_DETAIL_ITEMS
        nested = details["level1"]["level2"]["level3"]
        assert isinstance(nested, str)
        assert len(details["note"]) <= coop.MAX_DETAIL_CHARS


class TestTasks:
    def test_tasks_are_sorted_by_updated_at_desc_and_capped(self):
        tasks = [
            {
                "task_id": f"task-{index}",
                "session_id": "sess-1",
                "peer_id": "robot-b",
                "description": "scan",
                "status": "queued",
                "created_at": float(index),
                "updated_at": float(index),
                "result": "",
            }
            for index in range(60)
        ]
        payload = coop.normalize_cooperation(journal(tasks=tasks))
        assert len(payload["tasks"]) == coop.MAX_TASKS
        updated = [item["updated_at"] for item in payload["tasks"]]
        assert updated == sorted(updated, reverse=True)
        assert payload["tasks"][0]["task_id"] == "task-59"

    def test_task_status_and_result_are_normalized(self):
        payload = coop.normalize_cooperation(
            journal(
                tasks=[
                    {
                        "task_id": "task-9",
                        "status": "PARTIAL",
                        "result": "token=abc",
                        "description": "d" * 5000,
                    }
                ]
            )
        )
        item = payload["tasks"][0]
        assert item["status"] == "partial"
        assert len(item["description"]) <= coop.MAX_CONTENT_CHARS
        assert "abc" not in item["result"]


class TestPersistence:
    def test_persistence_error_object_becomes_text(self):
        payload = coop.normalize_cooperation(
            journal(persistence={"enabled": "yes", "error": {"message": "disk full"}})
        )
        assert payload["persistence"]["enabled"] is True
        assert isinstance(payload["persistence"]["error"], str)
        assert "disk full" in payload["persistence"]["error"]

    def test_missing_persistence_is_marked_unreported(self):
        payload = coop.normalize_cooperation(journal(persistence=None))
        assert payload["persistence"] == {"enabled": False, "error": None, "reported": False}


class TestCursorParams:
    def test_cursor_accepts_non_negative_integers(self):
        assert coop.coerce_cursor("0") == 0
        assert coop.coerce_cursor("12") == 12
        assert coop.coerce_cursor(7) == 7

    def test_cursor_rejects_malformed_and_negative(self):
        for raw in ("abc", "-1", "1.5", "", None, []):
            with pytest.raises(ValueError):
                coop.coerce_cursor(raw)

    def test_limit_accepts_positive_and_clamps_upper_bound(self):
        assert coop.coerce_limit("10") == 10
        assert coop.coerce_limit("100000") == coop.MAX_LIMIT

    def test_limit_rejects_malformed_and_non_positive(self):
        for raw in ("abc", "0", "-5", "", None):
            with pytest.raises(ValueError):
                coop.coerce_limit(raw)


class TestSliceEvents:
    def test_after_cursor_filters_older_events(self):
        events = coop.normalize_cooperation(journal(events=[event(seq) for seq in range(1, 6)]))
        window = coop.slice_events(events["events"], after=3, limit=10)
        assert [item["sequence"] for item in window["events"]] == [4, 5]
        assert window["gap"] is None
        assert window["truncated"] is False

    def test_limit_keeps_most_recent_and_reports_truncation(self):
        events = coop.normalize_cooperation(journal(events=[event(seq) for seq in range(1, 6)]))
        window = coop.slice_events(events["events"], after=None, limit=2)
        assert [item["sequence"] for item in window["events"]] == [4, 5]
        assert window["truncated"] is True

    def test_gap_is_reported_when_cursor_predates_retained_window(self):
        events = coop.normalize_cooperation(
            journal(events=[event(seq) for seq in range(11, 14)], oldest_cursor=11)
        )
        window = coop.slice_events(
            events["events"], after=5, limit=10, oldest_cursor=events["oldest_cursor"]
        )
        assert [item["sequence"] for item in window["events"]] == [11, 12, 13]
        assert window["gap"] == {"from": 6, "to": 10, "missed": 5}

    def test_no_gap_when_cursor_is_inside_window(self):
        events = coop.normalize_cooperation(
            journal(events=[event(seq) for seq in range(11, 14)], oldest_cursor=11)
        )
        window = coop.slice_events(
            events["events"], after=11, limit=10, oldest_cursor=events["oldest_cursor"]
        )
        assert window["gap"] is None


class TestSequentialCursorPaging:
    """after 를 주면 "가장 오래된 미처리"부터 잘라야 순차 페이징에서 기록이 유실되지 않는다."""

    @staticmethod
    def _journal(count: int = 5) -> dict:
        return coop.normalize_cooperation(
            journal(events=[event(seq) for seq in range(1, count + 1)], oldest_cursor=1)
        )

    def test_after_returns_oldest_unprocessed_batch(self):
        events = self._journal()["events"]
        window = coop.slice_events(events, after=0, limit=2)
        assert [item["sequence"] for item in window["events"]] == [1, 2]
        assert window["truncated"] is True
        assert window["gap"] is None

    def test_sequential_paging_walks_forward_without_gaps(self):
        events = self._journal()["events"]
        first = coop.slice_events(events, after=0, limit=2)
        second = coop.slice_events(events, after=first["events"][-1]["sequence"], limit=2)
        third = coop.slice_events(events, after=second["events"][-1]["sequence"], limit=2)
        assert [item["sequence"] for item in first["events"]] == [1, 2]
        assert [item["sequence"] for item in second["events"]] == [3, 4]
        assert [item["sequence"] for item in third["events"]] == [5]
        assert third["truncated"] is False
        assert first["gap"] is None and second["gap"] is None and third["gap"] is None

    def test_without_after_returns_most_recent_batch(self):
        events = self._journal()["events"]
        window = coop.slice_events(events, after=None, limit=2)
        assert [item["sequence"] for item in window["events"]] == [4, 5]

    def test_gap_window_starts_at_retained_oldest(self):
        retained = coop.normalize_cooperation(
            journal(events=[event(seq) for seq in range(11, 14)], oldest_cursor=11)
        )
        window = coop.slice_events(
            retained["events"], after=5, limit=2, oldest_cursor=retained["oldest_cursor"]
        )
        assert [item["sequence"] for item in window["events"]] == [11, 12]
        assert window["gap"] == {"from": 6, "to": 10, "missed": 5}
        assert window["truncated"] is True


class TestCooperationPayload:
    def test_without_cache_is_unavailable(self):
        payload = coop.cooperation_payload(None)
        assert payload["available"] is False
        assert payload["events"] == []
        assert payload["error"]
        assert payload["stale"] is True

    def test_payload_exposes_cursor_gap_and_freshness(self):
        cached = coop.normalize_cooperation(journal())
        payload = coop.cooperation_payload(cached, after=0, limit=10, age_ms=1200)
        assert payload["available"] is True
        assert payload["source"] == "status-cache"
        assert payload["returned"] == 2
        assert payload["cursor"] == 2
        assert payload["next_cursor"] == 3
        assert payload["oldest_cursor"] == 1
        assert payload["stale"] is False
        assert payload["age_ms"] == 1200
        assert payload["reset"] is False

    def test_stale_after_threshold(self):
        cached = coop.normalize_cooperation(journal())
        fresh = coop.cooperation_payload(cached, age_ms=coop.STALE_AFTER_MS)
        stale = coop.cooperation_payload(cached, age_ms=coop.STALE_AFTER_MS + 1)
        assert fresh["stale"] is False
        assert stale["stale"] is True

    def test_stream_change_requests_client_reset(self):
        cached = coop.normalize_cooperation(journal())
        payload = coop.cooperation_payload(cached, after=2, stream_id="stream-0")
        assert payload["reset"] is True
        assert [item["sequence"] for item in payload["events"]] == [1, 2]

    def test_same_stream_does_not_reset(self):
        cached = coop.normalize_cooperation(journal())
        payload = coop.cooperation_payload(cached, after=1, stream_id="stream-1")
        assert payload["reset"] is False
        assert [item["sequence"] for item in payload["events"]] == [2]

    def test_disabled_journal_is_preserved_in_payload(self):
        cached = coop.normalize_cooperation(journal(enabled=False))
        payload = coop.cooperation_payload(cached)
        assert payload["enabled"] is False
        assert payload["active"] is False


class TestJsonlExport:
    def test_one_json_object_per_event_and_secrets_masked(self):
        cached = coop.normalize_cooperation(
            journal(
                events=[
                    event(1, details={"device_token": "abc"}),
                    event(2, content="normal text"),
                ]
            )
        )
        text = coop.format_cooperation_jsonl(cached)
        lines = [line for line in text.splitlines() if line]
        assert len(lines) == 2
        decoded = [json.loads(line) for line in lines]
        assert decoded[0]["details"]["device_token"] == coop.MASK
        assert "Traceback" not in text
        assert text.endswith("\n")

    def test_export_is_empty_without_events(self):
        cached = coop.normalize_cooperation(journal(events=[]))
        assert coop.format_cooperation_jsonl(cached) == ""


def _raw_event(sequence: int, **overrides) -> dict:
    base = {
        "sequence": sequence,
        "event_type": "session_started",
        "timestamp": 1700000000.0,
        "robot_id": "robot-a",
        "peer_id": "",
        "session_id": "sess-1",
        "task_id": "",
        "message_id": "",
        "reply_to": "",
        "status": "",
        "content": "",
        "details": {},
    }
    base.update(overrides)
    return base


class TestStatuslessEvents:
    """백엔드가 session/decision/policy 이벤트를 빈 status + details 로 보낸다."""

    def test_blank_event_status_is_preserved_as_empty(self):
        payload = coop.normalize_cooperation(
            journal(
                events=[
                    _raw_event(1, event_type="session_started", details={"state": "active"}),
                    _raw_event(2, event_type="session_stopped", details={"state": "stopped"}),
                    _raw_event(3, event_type="decision", details={"decision": "refuse"}),
                    _raw_event(4, event_type="policy", details={"action": "deny"}),
                ]
            )
        )
        assert [item["status"] for item in payload["events"]] == ["", "", "", ""]

    def test_non_blank_unknown_event_status_stays_unknown(self):
        payload = coop.normalize_cooperation(journal(events=[_raw_event(1, status="EXPLODED")]))
        assert payload["events"][0]["status"] == "unknown"

    def test_blank_task_status_becomes_unknown(self):
        payload = coop.normalize_cooperation(journal(tasks=[{"task_id": "task-1", "status": ""}]))
        assert payload["tasks"][0]["status"] == "unknown"

    def test_session_decision_policy_details_are_kept(self):
        payload = coop.normalize_cooperation(
            journal(
                events=[
                    _raw_event(1, event_type="session_started", details={"state": "active"}),
                    _raw_event(
                        2,
                        event_type="decision",
                        details={"decision": "refuse", "reason": "peer not allowed"},
                    ),
                    _raw_event(
                        3,
                        event_type="policy",
                        details={"action": "deny", "reason": "blocked skill", "limits": "max 2"},
                    ),
                ]
            )
        )
        session, decision, policy = payload["events"]
        assert session["details"]["state"] == "active"
        assert decision["details"]["decision"] == "refuse"
        assert decision["details"]["reason"] == "peer not allowed"
        assert policy["details"]["action"] == "deny"
        assert policy["details"]["limits"] == "max 2"


class TestObservationMetadata:
    def test_event_observed_flag_is_preserved(self):
        payload = coop.normalize_cooperation(
            journal(
                events=[
                    _raw_event(1, event_type="task_completed", status="completed", observed=True),
                    _raw_event(2, event_type="task_completed", status="completed", observed=False),
                    _raw_event(3, event_type="task_completed", status="completed"),
                    _raw_event(
                        4, event_type="task_completed", status="completed", origin="peer_report"
                    ),
                ]
            )
        )
        assert [item["observed"] for item in payload["events"]] == [True, False, None, True]

    def test_task_observed_flag_is_preserved(self):
        payload = coop.normalize_cooperation(
            journal(
                tasks=[
                    {"task_id": "t1", "observed": True},
                    {"task_id": "t2", "observed": False},
                    {"task_id": "t3"},
                ]
            )
        )
        observed = {item["task_id"]: item["observed"] for item in payload["tasks"]}
        assert observed == {"t1": True, "t2": False, "t3": None}

    def test_delivery_confirmation_and_outbound_id_are_preserved(self):
        payload = coop.normalize_cooperation(
            journal(
                tasks=[
                    {
                        "task_id": "t1",
                        "status": "queued",
                        "session_id": "sess-1",
                        "peer_id": "robot-b",
                        "delivery_confirmed": True,
                        "outbound_message_id": "msg-out-1",
                    }
                ]
            )
        )
        task = payload["tasks"][0]
        assert task["delivery_confirmed"] is True
        assert task["outbound_message_id"] == "msg-out-1"
        assert task["peer_id"] == "robot-b"
        assert task["session_id"] == "sess-1"

    def test_unknown_private_fields_are_not_copied(self):
        payload = coop.normalize_cooperation(
            journal(
                tasks=[
                    {
                        "task_id": "t1",
                        "status": "running",
                        "internal_notes": "do not expose",
                        "raw_envelope": "{...}",
                    }
                ],
                events=[_raw_event(1, task_id="t1", status="running", internal_notes="x")],
            )
        )
        assert "internal_notes" not in payload["tasks"][0]
        assert "raw_envelope" not in payload["tasks"][0]
        assert "internal_notes" not in payload["events"][0]


class TestSecretHardening:
    def test_extended_secret_keys_are_masked(self):
        payload = coop.normalize_cooperation(
            journal(
                events=[
                    _raw_event(
                        1,
                        details={
                            "authorization": "Bearer abc",
                            "cookie": "session=1",
                            "bearer": "abc",
                            "privatekey": "-----BEGIN",
                            "auth": "basic",
                            "ok": 1,
                        },
                    )
                ]
            )
        )
        details = payload["events"][0]["details"]
        for key in ("authorization", "cookie", "bearer", "privatekey", "auth"):
            assert details[key] == coop.MASK
        assert details["ok"] == 1

    def test_bearer_value_in_content_is_masked(self):
        payload = coop.normalize_cooperation(
            journal(events=[_raw_event(1, content="Authorization: Bearer abc.def.ghi1234")])
        )
        content = payload["events"][0]["content"]
        assert "abc.def.ghi1234" not in content
        assert coop.MASK in content

    def test_overlong_detail_keys_are_bounded(self):
        payload = coop.normalize_cooperation(
            journal(events=[_raw_event(1, details={"k" * 500: "value"})])
        )
        keys = list(payload["events"][0]["details"])
        assert len(keys) == 1
        assert len(keys[0]) <= coop.MAX_KEY_CHARS

    def test_non_finite_numbers_are_zeroed(self):
        payload = coop.normalize_cooperation(
            journal(
                events=[_raw_event(1, timestamp=float("inf"))],
                tasks=[{"task_id": "t1", "created_at": float("nan"), "updated_at": float("inf")}],
            )
        )
        assert payload["events"][0]["timestamp"] == 0.0
        assert payload["tasks"][0]["created_at"] == 0.0
        assert payload["tasks"][0]["updated_at"] == 0.0
