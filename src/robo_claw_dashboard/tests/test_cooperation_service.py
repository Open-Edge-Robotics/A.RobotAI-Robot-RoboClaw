"""DashboardService 협동 관측 캐시 component 테스트.

계약: 대시보드는 ``/api/status`` 가 이미 받은 QueryState 응답의 ``cooperation`` 을
한 번만 캐시하고, ``/api/cooperation`` 은 그 캐시를 돌려준다(추가 ROS 질의 금지).
"""

from __future__ import annotations

import json

import pytest
from robo_claw_dashboard.application.service import DashboardService
from robo_claw_dashboard.domain import cooperation as coop

pytestmark = pytest.mark.component


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


def cooperation_journal(**overrides) -> dict:
    base = {
        "available": True,
        "enabled": True,
        "active": True,
        "local_robot_id": "robot-a",
        "session_id": "sess-1",
        "goal": "patrol the corridor",
        "stream_id": "stream-1",
        "events": [event(1), event(2)],
        "tasks": [],
        "next_cursor": 3,
        "oldest_cursor": 1,
        "dropped_events": 0,
        "persistence": {"enabled": True, "error": None},
    }
    base.update(overrides)
    return base


def state_json(cooperation: dict | None = None, agent_id: str = "robot-a") -> str:
    state: dict = {"agent_id": agent_id}
    if cooperation is not None:
        state["cooperation"] = cooperation
    return json.dumps(state)


class FakeBackend:
    """DashboardBackend protocol 최소 구현 + 호출 횟수 기록."""

    def __init__(self, query: str | None = None):
        self._query = query
        self.query_state_calls = 0

    def query_state(self) -> str | None:
        self.query_state_calls += 1
        return self._query

    def latest_agent_status(self) -> dict:
        return {}

    def latest_telemetry(self) -> dict:
        return {}

    def latest_sensor_health(self) -> dict:
        return {}

    def list_peers(self) -> list:
        return []

    def send_chat(self, message: str) -> dict:
        return {"success": True}

    def latest_map(self) -> dict:
        return {"available": False}

    def latest_pose(self) -> dict:
        return {"available": False}

    def set_initial_pose(self, pose: dict) -> dict:
        return {"success": True}

    def describe_config(self) -> dict | None:
        return None

    def logs(self, *, min_level=None, query=None, limit=None) -> dict:
        return {"available": False, "entries": []}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestCacheFromStatus:
    def test_status_caches_cooperation_without_extra_query(self):
        backend = FakeBackend(state_json(cooperation_journal()))
        service = DashboardService(backend)
        service.status()
        payload = service.cooperation_snapshot()
        assert backend.query_state_calls == 1
        assert payload["available"] is True
        assert [item["sequence"] for item in payload["events"]] == [1, 2]
        assert payload["local_robot_id"] == "robot-a"
        assert payload["goal"] == "patrol the corridor"

    def test_status_snapshot_carries_sanitized_cooperation(self):
        backend = FakeBackend(state_json(cooperation_journal()))
        snapshot = DashboardService(backend).status()
        assert snapshot["state"]["cooperation"]["stream_id"] == "stream-1"
        assert snapshot["state"]["cooperation"]["events"][0]["sequence"] == 1

    def test_status_replaces_raw_cooperation_with_bounded_sanitized_copy(self):
        """raw cooperation 가 /api/status 로 그대로 나가면 방어 정규화가 우회된다."""
        raw = cooperation_journal(
            events=[
                event(
                    1,
                    content="x" * 5000,
                    details={"device_token": "raw-secret-token", "authorization": "Bearer abc.def"},
                )
            ],
            tasks=[{"task_id": "t1", "status": "queued", "raw_envelope": "{secret}"}],
        )
        snapshot = DashboardService(FakeBackend(state_json(raw))).status()
        cooperation = snapshot["state"]["cooperation"]
        serialized = json.dumps(snapshot, ensure_ascii=False)
        assert "raw-secret-token" not in serialized
        assert "abc.def" not in serialized
        assert len(cooperation["events"][0]["content"]) <= coop.MAX_CONTENT_CHARS
        assert cooperation["events"][0]["details"]["device_token"] == coop.MASK
        assert "raw_envelope" not in cooperation["tasks"][0]

    def test_status_reports_unavailable_cooperation_for_old_agent(self):
        snapshot = DashboardService(FakeBackend(state_json())).status()
        assert snapshot["state"]["cooperation"]["available"] is False
        assert snapshot["state"]["cooperation"]["events"] == []

    def test_snapshot_before_status_is_unavailable(self):
        service = DashboardService(FakeBackend(state_json(cooperation_journal())))
        payload = service.cooperation_snapshot()
        assert payload["available"] is False
        assert payload["events"] == []
        assert payload["error"]

    def test_agent_without_cooperation_field_is_unavailable(self):
        service = DashboardService(FakeBackend(state_json()))
        service.status()
        payload = service.cooperation_snapshot()
        assert payload["available"] is False
        assert "협동" in payload["error"]

    def test_unparsable_state_reports_unavailable(self):
        service = DashboardService(FakeBackend("not json"))
        service.status()
        assert service.cooperation_snapshot()["available"] is False


class TestDisabledCooperation:
    def test_maestro_disabled_is_explicit(self):
        service = DashboardService(
            FakeBackend(state_json(cooperation_journal(enabled=False, active=True)))
        )
        service.status()
        payload = service.cooperation_snapshot()
        assert payload["available"] is True
        assert payload["enabled"] is False
        assert payload["active"] is False


class TestFreshness:
    def test_cache_is_fresh_within_threshold(self):
        clock = Clock()
        service = DashboardService(FakeBackend(state_json(cooperation_journal())), monotonic=clock)
        service.status()
        clock.advance(coop.STALE_AFTER_MS / 1000.0)
        payload = service.cooperation_snapshot()
        assert payload["stale"] is False
        assert payload["age_ms"] == pytest.approx(coop.STALE_AFTER_MS, abs=1)

    def test_cache_is_stale_after_threshold(self):
        clock = Clock()
        service = DashboardService(FakeBackend(state_json(cooperation_journal())), monotonic=clock)
        service.status()
        clock.advance(coop.STALE_AFTER_MS / 1000.0 + 5)
        payload = service.cooperation_snapshot()
        assert payload["stale"] is True
        assert payload["age_ms"] > coop.STALE_AFTER_MS

    def test_status_refresh_resets_age(self):
        clock = Clock()
        service = DashboardService(FakeBackend(state_json(cooperation_journal())), monotonic=clock)
        service.status()
        clock.advance(30)
        service.status()
        assert service.cooperation_snapshot()["stale"] is False


class TestPaging:
    def test_after_cursor_and_limit_are_applied(self):
        service = DashboardService(
            FakeBackend(state_json(cooperation_journal(events=[event(seq) for seq in range(1, 6)])))
        )
        service.status()
        payload = service.cooperation_snapshot(after=4)
        assert [item["sequence"] for item in payload["events"]] == [5]
        limited = service.cooperation_snapshot(after=None, limit=1)
        assert [item["sequence"] for item in limited["events"]] == [5]
        assert limited["truncated"] is True

    def test_stream_mismatch_signals_reset(self):
        service = DashboardService(FakeBackend(state_json(cooperation_journal())))
        service.status()
        payload = service.cooperation_snapshot(after=2, stream_id="other-stream")
        assert payload["reset"] is True
        assert len(payload["events"]) == 2


class TestSequentialPaging:
    def test_payload_cursor_advances_through_retained_events(self):
        service = DashboardService(
            FakeBackend(
                state_json(
                    cooperation_journal(events=[event(seq) for seq in range(1, 6)], oldest_cursor=1)
                )
            )
        )
        service.status()
        first = service.cooperation_snapshot(after=0, limit=2)
        assert [item["sequence"] for item in first["events"]] == [1, 2]
        assert first["cursor"] == 2
        second = service.cooperation_snapshot(after=first["cursor"], limit=2)
        assert [item["sequence"] for item in second["events"]] == [3, 4]
        assert second["cursor"] == 4
        third = service.cooperation_snapshot(after=second["cursor"], limit=2)
        assert [item["sequence"] for item in third["events"]] == [5]
        assert third["cursor"] == 5
        assert third["truncated"] is False
        assert first["gap"] is None and second["gap"] is None and third["gap"] is None

    def test_without_cursor_returns_recent_events(self):
        service = DashboardService(
            FakeBackend(
                state_json(
                    cooperation_journal(events=[event(seq) for seq in range(1, 6)], oldest_cursor=1)
                )
            )
        )
        service.status()
        payload = service.cooperation_snapshot(limit=2)
        assert [item["sequence"] for item in payload["events"]] == [4, 5]
        assert payload["truncated"] is True


class TestExport:
    def test_export_returns_ndjson_with_masked_secrets(self):
        service = DashboardService(
            FakeBackend(
                state_json(
                    cooperation_journal(
                        events=[event(1, details={"device_token": "abc"}), event(2)]
                    )
                )
            )
        )
        service.status()
        content_type, text = service.cooperation_export()
        assert content_type.startswith("application/x-ndjson")
        lines = [line for line in text.splitlines() if line]
        assert len(lines) == 2
        assert json.loads(lines[0])["details"]["device_token"] == coop.MASK
        assert "abc" not in lines[0]

    def test_export_without_status_is_empty(self):
        content_type, text = DashboardService(FakeBackend()).cooperation_export()
        assert content_type.startswith("application/x-ndjson")
        assert text == ""

    def test_export_is_bounded_to_recent_events(self):
        service = DashboardService(
            FakeBackend(
                state_json(cooperation_journal(events=[event(seq) for seq in range(1, 151)]))
            )
        )
        service.status()
        _, text = service.cooperation_export()
        assert len([line for line in text.splitlines() if line]) <= coop.MAX_EVENTS


class TestCacheCoherence:
    """ThreadingHTTPServer 는 요청마다 스레드로 status/cooperation 을 동시에 부른다."""

    def test_payload_and_timestamp_are_read_as_one_pair(self):
        clock = Clock()
        backend = FakeBackend(state_json(cooperation_journal()))
        service = DashboardService(backend, monotonic=clock)
        service.status()
        clock.advance(5)
        first = service.cooperation_snapshot()
        clock.advance(5)
        second = service.cooperation_snapshot()
        assert first["age_ms"] == 5000
        assert second["age_ms"] == 10000
        assert first["stale"] is False and second["stale"] is False

    def test_concurrent_status_and_readers_see_coherent_streams(self):
        import threading

        class AlternatingBackend(FakeBackend):
            def __init__(self):
                super().__init__(None)
                self.index = 0
                self.lock = threading.Lock()

            def query_state(self):
                with self.lock:
                    self.index += 1
                    marker = f"s{self.index}"
                return state_json(
                    cooperation_journal(
                        stream_id="stream-" + marker,
                        events=[event(1, content=marker)],
                        tasks=[{"task_id": marker + "-task", "status": "queued"}],
                    )
                )

        service = DashboardService(AlternatingBackend())
        errors: list[str] = []

        def writer():
            for _ in range(60):
                service.status()

        def reader():
            for _ in range(60):
                payload = service.cooperation_snapshot()
                if not payload["events"]:
                    continue
                marker = payload["stream_id"].split("-", 1)[1]
                if payload["events"][0]["content"] != marker:
                    errors.append("stream/event mismatch")
                if payload["tasks"] and not payload["tasks"][0]["task_id"].startswith(marker):
                    errors.append("stream/task mismatch")
                if payload["age_ms"] is None or payload["age_ms"] < 0:
                    errors.append("bad age")

        def exporter():
            for _ in range(60):
                _, text = service.cooperation_export()
                markers = {json.loads(line)["content"] for line in text.splitlines() if line}
                if len(markers) > 1:
                    errors.append("export mixed streams")

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=exporter),
            threading.Thread(target=reader),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
