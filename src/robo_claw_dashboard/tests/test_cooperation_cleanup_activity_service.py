"""백엔드 activity 스냅샷이 대시보드 HTTP 표면까지 도달하는지 확인하는 component 테스트.

main 소유의 통합 테스트 파일을 건드리지 않고, 같은 조건(active true/goal 거실/ tasks 의
motion_requested)을 서비스 계층에서 고정한다.
"""

from __future__ import annotations

import json

import pytest
from robo_claw_dashboard.application.service import DashboardService

pytestmark = pytest.mark.component


def activity(**overrides) -> dict:
    base = {
        "source": "cleanup_demo",
        "run_id": "cleanup-run-former0047-7",
        "session_id": "cleanup-run-former0047-7",
        "robot_id": "former0047",
        "task_id": "cleanup-task-7",
        "goal": "거실 정리 시연",
        "phase": "demo_motion",
        "phase_label": "시연 모션 요청",
        "place": "거실",
        "target": "테이블 위 컵",
        "peer": "cloid01",
        "transport": "local",
        "status": "motion_requested",
        "task_status": "",
        "result": "정리 시연 모션 요청을 전달했습니다. 실제 정리와 모션 완료는 확인되지 않았습니다.",
        "failure_reason": "",
        "observed": False,
        "physical_cleanup_confirmed": False,
        "motion_completion_confirmed": False,
        "active": True,
        "started_at": 1700000000.0,
        "updated_at": 1700000010.0,
        "finished_at": 0.0,
        "duration_ms": 10000,
    }
    base.update(overrides)
    return base


def backend_snapshot() -> dict:
    return {
        "available": True,
        "enabled": True,
        "active": True,
        "activity_source": "cleanup_demo",
        "direct_cooperation_enabled": False,
        "local_robot_id": "former0047",
        "session_id": "cleanup-run-former0047-7",
        "goal": "거실 정리 시연",
        "stream_id": "stream-cleanup-7",
        "activity": activity(),
        "events": [],
        "tasks": [],
        "next_cursor": 1,
        "oldest_cursor": 0,
        "dropped_events": 0,
        "persistence": {"enabled": False, "error": None},
    }


class FakeBackend:
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


def _state_json() -> str:
    return json.dumps({"agent_id": "former0047", "cooperation": backend_snapshot()})


class TestActivityInHttpPayload:
    def test_status_snapshot_projects_activity_into_task_list(self):
        backend = FakeBackend(_state_json())
        snapshot = DashboardService(backend).status()
        cooperation = snapshot["state"]["cooperation"]
        assert cooperation["active"] is True
        assert "거실" in cooperation["goal"]
        assert cooperation["activity"]["phase_label"] == "시연 모션 요청"
        assert [task["status"] for task in cooperation["tasks"]] == ["motion_requested"]
        assert cooperation["tasks"][0]["source"] == "local_activity"

    def test_cooperation_cache_payload_carries_activity_and_projected_task(self):
        backend = FakeBackend(_state_json())
        service = DashboardService(backend)
        service.status()
        payload = service.cooperation_snapshot()
        assert backend.query_state_calls == 1
        assert payload["active"] is True
        assert "거실" in payload["goal"]
        assert payload["activity"]["peer"] == "cloid01"
        assert payload["activity"]["status"] == "motion_requested"
        assert [task["status"] for task in payload["tasks"]] == ["motion_requested"]
        assert payload["tasks"][0]["session_id"] == "cleanup-run-former0047-7"

    def test_activity_projection_does_not_leak_private_keys(self):
        raw = backend_snapshot()
        raw["activity"] = activity(secret_note="drop me")
        service = DashboardService(FakeBackend(json.dumps({"cooperation": raw})))
        snapshot = service.status()
        cooperation = snapshot["state"]["cooperation"]
        assert "secret_note" not in cooperation["activity"]
        assert "secret_note" not in json.dumps(snapshot)
