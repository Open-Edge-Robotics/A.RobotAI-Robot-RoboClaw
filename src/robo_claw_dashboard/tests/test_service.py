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
        self._map = kw.get("map", {"available": False})
        self._pose = kw.get("pose", {"available": False})
        self._initial_pose_result = kw.get("initial_pose_result", {"success": True})
        self._config = kw.get("config", None)
        self.send_calls: list[str] = []
        self.initial_pose_calls: list[dict] = []

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

    def latest_map(self) -> dict:
        return dict(self._map)

    def latest_pose(self) -> dict:
        return dict(self._pose)

    def describe_config(self) -> dict | None:
        return self._config

    def set_initial_pose(self, pose: dict) -> dict:
        self.initial_pose_calls.append(dict(pose))
        raise_on = self._initial_pose_result.get("_raise")
        if raise_on:
            raise raise_on(pose)
        result = dict(self._initial_pose_result)
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


MAP_META = {
    "available": True,
    "frame_id": "map",
    "stamp_ns": 1,
    "width": 100,
    "height": 80,
    "resolution": 0.05,
    "origin": {"x": -1.0, "y": -2.0, "yaw": 0.0},
    "encoding": "uint8",
    "data": "",
}

POSE = {
    "available": True,
    "frame_id": "map",
    "base_frame": "base_link",
    "x": 1.0,
    "y": 2.0,
    "yaw": 0.5,
}


class TestLocalizationSection:
    def test_status_exposes_map_meta_and_robot_pose(self):
        backend = FakeBackend(map=MAP_META, pose=POSE)
        snap = DashboardService(backend).status()
        assert snap["localization"]["pose"]["x"] == 1.0
        assert snap["localization"]["map"]["available"] is True
        assert "data" not in snap["localization"]["map"]

    def test_status_reports_localization_unavailable(self):
        snap = DashboardService(FakeBackend()).status()
        assert snap["localization"]["pose"]["available"] is False
        assert snap["localization"]["map"]["available"] is False

    def test_map_snapshot_is_returned_from_backend(self):
        backend = FakeBackend(map=MAP_META)
        snapshot = DashboardService(backend).map_snapshot()
        assert snapshot["width"] == 100
        assert snapshot["data"] == ""


class TestSetInitialPose:
    def test_valid_pose_is_published_with_covariance(self):
        backend = FakeBackend(map=MAP_META)
        result = DashboardService(backend).set_initial_pose({"x": 1.0, "y": 2.0, "yaw": 0.5})
        assert result["success"] is True
        assert backend.initial_pose_calls[0]["x"] == 1.0
        assert backend.initial_pose_calls[0]["frame_id"] == "map"
        assert "covariance" in backend.initial_pose_calls[0]

    def test_missing_map_blocks_initial_pose(self):
        backend = FakeBackend()
        result = DashboardService(backend).set_initial_pose({"x": 1.0, "y": 2.0})
        assert result["success"] is False
        assert "지도" in result["error"]
        assert backend.initial_pose_calls == []

    def test_position_outside_map_is_rejected_before_publish(self):
        backend = FakeBackend(map=MAP_META)
        result = DashboardService(backend).set_initial_pose({"x": 100.0, "y": 2.0})
        assert result["success"] is False
        assert "지도" in result["error"]
        assert backend.initial_pose_calls == []

    def test_invalid_payload_does_not_reach_backend(self):
        backend = FakeBackend(map=MAP_META)
        result = DashboardService(backend).set_initial_pose({"x": "abc"})
        assert result["success"] is False
        assert backend.initial_pose_calls == []

    def test_backend_exception_is_normalized(self):
        def _boom(_pose):
            raise RuntimeError("publish 실패")

        backend = FakeBackend(map=MAP_META, initial_pose_result={"_raise": _boom})
        result = DashboardService(backend).set_initial_pose({"x": 1.0, "y": 2.0})
        assert result["success"] is False
        assert "publish 실패" in result["error"]

    def test_backend_rejection_is_surfaced(self):
        backend = FakeBackend(
            map=MAP_META,
            initial_pose_result={"success": False, "error": "/initialpose 구독자 없음"},
        )
        result = DashboardService(backend).set_initial_pose({"x": 1.0, "y": 2.0})
        assert result["success"] is False
        assert "구독자" in result["error"]


class TestConfigPanel:
    def test_config_panel_is_built_from_backend_sections(self):
        backend = FakeBackend(
            config={
                "agent_node": "robo_claw_agent_node",
                "agent_parameters": {"llm_model": "gemma4:31b", "http_control_token": "abc"},
                "dashboard_node": "robo_claw_dashboard_node",
                "dashboard_parameters": {"dashboard_port": 9090},
            }
        )
        panel = DashboardService(backend).config_snapshot()
        assert panel["available"] is True
        rows = {row["name"]: row for row in panel["agent"]["parameters"]}
        assert rows["llm_model"]["value"] == "gemma4:31b"
        assert rows["http_control_token"]["value"] == "***"

    def test_config_panel_unavailable_backend_reports_empty(self):
        panel = DashboardService(FakeBackend()).config_snapshot()
        assert panel["available"] is False
        assert panel["agent"]["parameters"] == []

    def test_backend_error_does_not_break_response(self):
        class BoomBackend(FakeBackend):
            def describe_config(self):
                raise RuntimeError("param service 없음")

        panel = DashboardService(BoomBackend()).config_snapshot()
        assert panel["available"] is False
