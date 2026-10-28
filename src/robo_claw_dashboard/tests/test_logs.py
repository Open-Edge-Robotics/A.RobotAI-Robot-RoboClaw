"""로그 패널 도메인 unit 테스트 — /rosout 정규화·링 버퍼·필터·직렬화."""

import json

import pytest
from robo_claw_dashboard.domain.logs import (
    LEVEL_NAMES,
    MAX_CAPACITY,
    MAX_LIMIT,
    MIN_CAPACITY,
    LogBuffer,
    build_log_payload,
    format_log_jsonl,
    format_log_text,
    level_name,
    matches_filters,
    normalize_entry,
    normalize_min_level,
    unavailable_logs,
)

pytestmark = pytest.mark.unit


def _entry(**kw):
    base = {
        "stamp_sec": 1700000000.125,
        "level": 20,
        "node": "/robo_claw_agent_node",
        "message": "hello",
        "file": "",
        "function": "",
        "line": 0,
    }
    base.update(kw)
    return normalize_entry(**base)


class TestLevelNames:
    @pytest.mark.parametrize("value", sorted(LEVEL_NAMES))
    def test_ros_level_values_map_to_names(self, value):
        assert level_name(value) == LEVEL_NAMES[value]

    def test_string_level_accepted(self):
        assert level_name("30") == "warn"

    @pytest.mark.parametrize(("value", "expected"), [(0, "debug"), (25, "info"), (55, "fatal")])
    def test_out_of_range_level_snaps_to_nearest_band(self, value, expected):
        assert level_name(value) == expected

    def test_garbage_level_defaults_to_info(self):
        assert level_name(None) == "info"
        assert level_name("not-a-level") == "info"


class TestNormalizeMinLevel:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("warn", "warn"), ("WARNING", "warn"), ("err", "error"), ("debug", "debug")],
    )
    def test_aliases_are_canonicalized(self, raw, expected):
        assert normalize_min_level(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "all", "  ", "unknown"])
    def test_empty_or_unknown_means_no_filter(self, raw):
        assert normalize_min_level(raw) is None


class TestNormalizeEntry:
    def test_shape_and_utc_time(self):
        entry = _entry()
        assert entry["time"] == "2023-11-14T22:13:20.125Z"
        assert entry["stamp_sec"] == pytest.approx(1700000000.125)
        assert entry["level"] == "info"
        assert entry["node"] == "/robo_claw_agent_node"
        assert entry["message"] == "hello"

    def test_none_fields_become_empty_strings(self):
        entry = _entry(node=None, message=None, file=None, function=None, line=None)
        assert entry["node"] == ""
        assert entry["message"] == ""
        assert entry["file"] == ""
        assert entry["line"] == 0

    def test_bad_stamp_does_not_raise(self):
        entry = _entry(stamp_sec="oops")
        assert entry["stamp_sec"] == 0.0


class TestFilters:
    def test_min_level_keeps_severity_at_or_above(self):
        warn = _entry(level=30)
        info = _entry(level=20)
        assert matches_filters(warn, min_level="warn") is True
        assert matches_filters(info, min_level="warn") is False

    def test_query_matches_message_or_node_case_insensitively(self):
        entry = _entry(node="/Nav2_Node", message="Patrol failed")
        assert matches_filters(entry, query="patrol") is True
        assert matches_filters(entry, query="nav2") is True
        assert matches_filters(entry, query="docking") is False

    def test_no_filters_matches_everything(self):
        assert matches_filters(_entry(), min_level=None, query=None) is True


class TestLogBuffer:
    def test_capacity_is_clamped(self):
        assert LogBuffer(1).capacity == MIN_CAPACITY
        assert LogBuffer(10**9).capacity == MAX_CAPACITY

    def test_append_assigns_increasing_seq(self):
        buffer = LogBuffer(10)
        assert buffer.append(_entry())["seq"] == 1
        assert buffer.append(_entry())["seq"] == 2
        assert len(buffer) == 2

    def test_overflow_keeps_newest_entries(self):
        buffer = LogBuffer(MIN_CAPACITY)
        for index in range(MIN_CAPACITY + 5):
            buffer.append(_entry(message=f"m{index}"))
        snapshot = buffer.snapshot()
        assert len(snapshot) == MIN_CAPACITY
        assert snapshot[0]["message"] == "m5"
        assert snapshot[-1]["message"] == f"m{MIN_CAPACITY + 4}"

    def test_snapshot_is_a_detached_copy(self):
        buffer = LogBuffer(10)
        buffer.append(_entry())
        snapshot = buffer.snapshot()
        snapshot[0]["message"] = "mutated"
        snapshot.clear()
        assert buffer.snapshot()[0]["message"] == "hello"


class TestBuildLogPayload:
    def _entries(self):
        return [
            _entry(level=10, message="debug tick"),
            _entry(level=20, message="patrol started"),
            _entry(level=30, message="patrol slow"),
            _entry(level=40, node="/nav2", message="patrol failed"),
        ]

    def test_counts_and_available(self):
        payload = build_log_payload(self._entries(), capacity=500, topic="/rosout")
        assert payload["available"] is True
        assert payload["stored"] == 4
        assert payload["levels"] == {"debug": 1, "info": 1, "warn": 1, "error": 1, "fatal": 0}
        assert payload["nodes"] == ["/nav2", "/robo_claw_agent_node"]
        assert payload["topic"] == "/rosout"

    def test_filter_and_limit_keep_newest(self):
        payload = build_log_payload(
            self._entries(), capacity=500, min_level="warn", query="patrol", limit=1
        )
        assert [entry["message"] for entry in payload["entries"]] == ["patrol failed"]
        assert payload["returned"] == 1
        assert payload["truncated"] is True
        assert payload["level"] == "warn"
        assert payload["query"] == "patrol"

    def test_unfiltered_full_result_is_not_truncated(self):
        payload = build_log_payload(self._entries(), capacity=500, limit=100)
        assert payload["returned"] == 4
        assert payload["truncated"] is False

    def test_invalid_limit_falls_back_to_default(self):
        payload = build_log_payload(self._entries(), capacity=500, limit="abc")
        assert payload["returned"] == 4

    def test_limit_is_capped(self):
        payload = build_log_payload(self._entries(), capacity=500, limit=10**9)
        assert payload["limit"] == MAX_LIMIT

    def test_negative_limit_falls_back_to_default(self):
        payload = build_log_payload(self._entries(), capacity=500, limit=-5)
        assert payload["limit"] > 0

    def test_empty_buffer_is_still_available(self):
        payload = build_log_payload([], capacity=500)
        assert payload["available"] is True
        assert payload["entries"] == []
        assert payload["stored"] == 0


class TestSerialization:
    def test_text_line_has_time_level_node_message(self):
        entry = _entry(level=30, message="patrol failed", file="execution.py", line=42)
        text = format_log_text([entry])
        assert "2023-11-14T22:13:20.125Z WARN  /robo_claw_agent_node: patrol failed" in text
        assert "(execution.py:42)" in text

    def test_text_header_is_optional(self):
        assert format_log_text([], header="") == ""
        assert format_log_text([], header="export").startswith("# export")

    def test_jsonl_round_trips(self):
        entries = [_entry(message="a"), _entry(level=40, message="b")]
        lines = format_log_jsonl(entries).strip().splitlines()
        assert [json.loads(line) for line in lines] == entries


def test_unavailable_logs_shape():
    payload = unavailable_logs("로그 미수신")
    assert payload["available"] is False
    assert payload["entries"] == []
    assert payload["error"] == "로그 미수신"
