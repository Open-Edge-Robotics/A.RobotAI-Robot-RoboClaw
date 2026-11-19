"""DashboardService 의 지도 전환/초기 위치 연동 테스트 (fake backend)."""

import pytest
from robo_claw_dashboard.application.localization_switch import LocalizationSwitchService
from robo_claw_dashboard.application.service import DashboardService
from robo_claw_dashboard.domain.localization import INITIAL_POSE_COVARIANCE
from robo_claw_dashboard.domain.localization_mode import normalize_lio_status

pytestmark = pytest.mark.component

MAP_META = {
    "available": True,
    "width": 100,
    "height": 80,
    "resolution": 0.05,
    "origin": {"x": -1.0, "y": -2.0, "yaw": 0.0},
    "frame_id": "map",
    "encoding": "uint8",
    "data": "",
}


class FakeBackend:
    def __init__(self, *, lio_status="ready", switch_result=None):
        self._lio_status = lio_status
        self._switch_result = switch_result or {"success": True, "message": "ok"}
        self.initial_pose_calls: list[dict] = []
        self.switch_plans: list[dict] = []

    # ---- 대시보드 상태 -----------------------------------------------------
    def query_state(self):
        return None

    def latest_agent_status(self):
        return {}

    def latest_telemetry(self):
        return {}

    def latest_sensor_health(self):
        return {}

    def list_peers(self):
        return []

    def send_chat(self, message, **kwargs):  # pragma: no cover - 사용하지 않음
        return {"success": True}

    def latest_map(self):
        return dict(MAP_META)

    def latest_pose(self):
        return {"available": True, "x": 1.0, "y": 2.0, "yaw": 0.0, "frame_id": "map"}

    def describe_config(self):
        return None

    def logs(self, **kwargs):  # pragma: no cover - 사용하지 않음
        return {"entries": []}

    # ---- 초기 위치/지도 전환 -------------------------------------------------
    def set_initial_pose(self, pose):
        self.initial_pose_calls.append(dict(pose))
        return {"success": True}

    def latest_lio_status(self):
        if self._lio_status is None:
            return None
        return normalize_lio_status(self._lio_status)

    def localization_control_available(self):
        return {"available": True, "reason": ""}

    def switch_localization_map(self, plan):
        self.switch_plans.append(dict(plan))
        return dict(self._switch_result)


def _service(backend, **kwargs):
    kwargs.setdefault("localization_map_names", ["FACTORYMAP", "konjiam_L_107"])
    return DashboardService(backend, **kwargs)


def test_initial_pose_uses_configured_covariance():
    backend = FakeBackend()
    service = _service(backend, initialpose_covariance=0.25)
    result = service.set_initial_pose({"x": 1.0, "y": 2.0, "yaw": 0.0})
    assert result["success"] is True
    assert backend.initial_pose_calls[0]["covariance"][0] == 0.25
    assert backend.initial_pose_calls[0]["covariance"][7] == 0.25


def test_invalid_covariance_falls_back_to_default():
    backend = FakeBackend()
    service = _service(backend, initialpose_covariance=-1.0)
    service.set_initial_pose({"x": 1.0, "y": 2.0})
    assert backend.initial_pose_calls[0]["covariance"][0] == INITIAL_POSE_COVARIANCE


def test_initial_pose_is_rejected_while_mapping():
    backend = FakeBackend(lio_status="mapping")
    result = _service(backend).set_initial_pose({"x": 1.0, "y": 2.0})
    assert result["success"] is False
    assert "매핑" in result["error"]
    assert backend.initial_pose_calls == []


def test_initial_pose_allowed_after_localization_ready():
    backend = FakeBackend(lio_status="ready")
    result = _service(backend).set_initial_pose({"x": 1.0, "y": 2.0})
    assert result["success"] is True
    assert backend.initial_pose_calls


def test_initial_pose_allowed_without_lio_status_source():
    backend = FakeBackend(lio_status=None)
    result = _service(backend).set_initial_pose({"x": 1.0, "y": 2.0})
    assert result["success"] is True


def test_localization_snapshot_exposes_maps_and_status():
    backend = FakeBackend(lio_status="relocalizing_pose")
    snapshot = _service(backend).localization_snapshot()
    assert snapshot["available"] is True
    assert snapshot["status"]["active"] is True
    assert [item["name"] for item in snapshot["maps"]] == ["FACTORYMAP", "konjiam_L_107"]
    assert snapshot["control"]["available"] is True


def test_localization_snapshot_reports_initial_pose_source_amcl_by_default():
    """AMCL 로봇(대부)은 lio 후보가 아니므로 amcl 로 내려간다(UI 가 LIO 카드를 숨긴다)."""
    snapshot = _service(FakeBackend()).localization_snapshot()
    assert snapshot["initial_pose_source"] == "amcl"


def test_localization_snapshot_reports_lio_for_lio_robot():
    backend = FakeBackend()
    backend.initial_pose_semantics = lambda: "lio"
    snapshot = _service(backend).localization_snapshot()
    assert snapshot["initial_pose_source"] == "lio"


def test_switch_is_accepted_and_runs_worker():
    backend = FakeBackend()
    service = _service(backend)
    result = service.switch_localization_map({"map": "konjiam_L_107", "confirm": True})
    assert result["status_code"] == 202
    assert service._localization.wait_idle(5.0) is True
    assert backend.switch_plans[0]["map_path"] == "/ws/maps/konjiam_L_107"
    assert service.localization_snapshot()["last_switch"]["status"] == "succeeded"


def test_injected_switch_service_is_used_instead_of_default():
    backend = FakeBackend()
    injected = LocalizationSwitchService(backend, map_names=["only_map"], map_root="/maps")
    service = _service(backend, localization=injected)
    snapshot = service.localization_snapshot()
    assert [item["name"] for item in snapshot["maps"]] == ["only_map"]
    assert snapshot["map_root"] == "/maps"


def test_shutdown_stops_localization_worker():
    backend = FakeBackend()
    service = _service(backend)
    service.shutdown()
    result = service.switch_localization_map({"map": "konjiam_L_107", "confirm": True})
    assert result["status_code"] == 503
