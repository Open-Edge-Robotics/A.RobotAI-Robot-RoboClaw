"""DashboardService component 테스트 — fake backend 로 rclpy 없이 검증."""

import pytest
from robo_claw_dashboard.application.service import DashboardService

pytestmark = pytest.mark.component


class FakeBackend:
    """DashboardBackend protocol 을 충족하는 테스트용 backend."""

    def __init__(self, **kw):
        self._query = kw.get("query")
        self._status = kw.get("agent_status", {})
        self._telemetry = kw.get("telemetry", {})
        self._sensors = kw.get("sensor_health", {})
        self._peers = kw.get("peers", [])
        self._send_result = kw.get("send_result", {"success": True, "message": "완료"})
        self.send_calls: list[str] = []

    def query_state(self) -> str | None:
        return self._query

    def latest_agent_status(self) -> dict:
        return self._status

    def latest_telemetry(self) -> dict:
        return self._telemetry

    def latest_sensor_health(self) -> dict:
        return self._sensors

    def list_peers(self) -> list:
        return self._peers

    def send_chat(self, message: str) -> dict:
        self.send_calls.append(message)
        raise_on = self._send_result.get("_raise")
        if raise_on:
            raise raise_on(message)
        result = dict(self._send_result)
        result.pop("_raise", None)
        return result


class TestStatus:
    def test_combines_all_backend_sources(self):
        backend = FakeBackend(
            query='{"agent_id":"r1"}',
            agent_status={"state": 2, "current_skill": "nav"},
            telemetry={"battery": {"percentage": "80.0%"}},
            sensor_health={"lidar": True},
            peers=[{"name": "peer-1"}],
        )
        snap = DashboardService(backend).status()
        assert snap["state"]["agent_id"] == "r1"
        assert snap["agent"]["state"] == "EXECUTING"
        assert snap["telemetry"]["battery"]["percentage"] == "80.0%"
        assert snap["sensor_health"]["lidar"] is True
        assert snap["peers"][0]["name"] == "peer-1"

    def test_empty_backend_returns_safe_defaults(self):
        snap = DashboardService(FakeBackend()).status()
        assert snap["agent"]["state"] == "UNKNOWN"
        assert snap["peers"] == []


class TestSendMessage:
    def test_invalid_message_does_not_call_backend(self):
        backend = FakeBackend()
        result = DashboardService(backend).send_message("   ")
        assert result["success"] is False
        assert result["error"]
        assert backend.send_calls == []

    def test_valid_message_calls_backend_and_returns_message(self):
        backend = FakeBackend(send_result={"success": True, "message": "완료"})
        result = DashboardService(backend).send_message("상태 알려줘")
        assert result["success"] is True
        assert result["message"] == "완료"
        assert backend.send_calls == ["상태 알려줘"]

    def test_forwards_skill_results(self):
        backend = FakeBackend(
            send_result={
                "success": True,
                "message": "완료",
                "skill_results": [{"skill_name": "get_status", "code": 0}],
            }
        )
        result = DashboardService(backend).send_message("상태")
        assert result["skill_results"][0]["skill_name"] == "get_status"

    def test_surfaces_backend_error(self):
        backend = FakeBackend(send_result={"success": False, "error": "거부됨"})
        result = DashboardService(backend).send_message("이동")
        assert result["success"] is False
        assert result["error"] == "거부됨"

    def test_backend_exception_normalized(self):
        def _boom(_msg):
            raise RuntimeError("timeout")

        backend = FakeBackend(send_result={"_raise": _boom})
        result = DashboardService(backend).send_message("이동")
        assert result["success"] is False
        assert "timeout" in result["error"]
