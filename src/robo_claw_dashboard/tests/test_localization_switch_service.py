"""지도 전환 서비스 테스트 — fake backend 로 rclpy 없이 접수/실행/상태를 검증."""

import threading

import pytest
from robo_claw_dashboard.application.localization_switch import LocalizationSwitchService

pytestmark = pytest.mark.unit

MAP_NAMES = ["FACTORYMAP", "konjiam_L_107"]
MAP_ROOT = "/ws/maps"


class FakeBackend:
    """switch_localization_map 결과와 LIO 상태를 흉내내는 대역."""

    def __init__(self, *, result=None, status="ready", control=None, block=False):
        self.plans: list[dict] = []
        self._result = result or {"success": True, "message": "ok"}
        self._status_raw = status
        self._control = control or {"available": True, "reason": ""}
        self._block = threading.Event()
        self._blocked = block
        self._entered = threading.Event()

    # ---- backend protocol -------------------------------------------------
    def switch_localization_map(self, plan):
        self.plans.append(dict(plan))
        self._entered.set()
        if self._blocked:
            self._block.wait(5.0)
        if isinstance(self._result, Exception):
            raise self._result
        return dict(self._result)

    def latest_lio_status(self):
        if isinstance(self._status_raw, Exception):
            raise self._status_raw
        if self._status_raw is None:
            return None
        from robo_claw_dashboard.domain.localization_mode import normalize_lio_status

        return normalize_lio_status(self._status_raw)

    def localization_control_available(self):
        return dict(self._control)

    # ---- test helpers -----------------------------------------------------
    def release(self):
        self._block.set()

    @property
    def entered(self):
        return self._entered


def _service(backend, *, holds_lease=None):
    return LocalizationSwitchService(
        backend,
        map_names=MAP_NAMES,
        map_root=MAP_ROOT,
        timeout_sec=5.0,
        holds_lease=holds_lease,
    )


def _request(**overrides):
    payload = {"map": "konjiam_L_107", "confirm": True}
    payload.update(overrides)
    return payload


def test_invalid_request_is_rejected_without_calling_backend():
    backend = FakeBackend()
    service = _service(backend)
    result = service.submit({"map": "konjiam_L_107"})
    assert result["success"] is False
    assert result["status_code"] == 400
    assert backend.plans == []


def test_localization_switch_runs_worker_and_records_success():
    backend = FakeBackend(result={"success": True, "message": "switch_mode ok"})
    service = _service(backend)
    result = service.submit(_request(set_initial_pose=True, x=1.5, y=-2.0, yaw=0.5))
    assert result["success"] is True
    assert result["status_code"] == 202
    assert result["request"]["status"] == "queued"

    assert service.wait_idle(5.0) is True
    assert backend.plans[0]["map_path"] == "/ws/maps/konjiam_L_107"
    assert backend.plans[0]["set_initial_pose"] is True
    assert backend.plans[0]["pose"]["x"] == 1.5

    snapshot = service.snapshot()
    job = snapshot["last_switch"]
    assert job["status"] == "succeeded"
    assert job["message"] == "switch_mode ok"
    assert snapshot["current_map"] == "konjiam_L_107"
    assert snapshot["control"]["busy"] is False


def test_backend_failure_is_reported_as_failed_job():
    backend = FakeBackend(result={"success": False, "message": "map load failed"})
    service = _service(backend)
    service.submit(_request())
    assert service.wait_idle(5.0) is True
    job = service.snapshot()["last_switch"]
    assert job["status"] == "failed"
    assert "map load failed" in job["message"]
    assert service.snapshot()["current_map"] is None


def test_backend_exception_is_recorded_without_crashing():
    backend = FakeBackend(result=RuntimeError("service unavailable"))
    service = _service(backend)
    service.submit(_request())
    assert service.wait_idle(5.0) is True
    job = service.snapshot()["last_switch"]
    assert job["status"] == "failed"
    assert "service unavailable" in job["message"]


def test_second_request_while_running_is_rejected():
    backend = FakeBackend(block=True)
    service = _service(backend)
    first = service.submit(_request())
    assert first["status_code"] == 202
    assert backend.entered.wait(5.0) is True

    second = service.submit(_request(map="FACTORYMAP"))
    assert second["success"] is False
    assert second["status_code"] == 409
    assert "진행" in second["error"]

    backend.release()
    assert service.wait_idle(5.0) is True


def test_unavailable_control_reports_reason():
    backend = FakeBackend(control={"available": False, "reason": "lio_fr_system_msgs 미설치"})
    service = _service(backend)
    result = service.submit(_request())
    assert result["success"] is False
    assert result["status_code"] == 503
    assert "lio_fr_system_msgs" in result["error"]
    assert backend.plans == []
    assert service.snapshot()["control"]["available"] is False


def test_map_goal_lease_blocks_switch():
    backend = FakeBackend()
    service = _service(backend, holds_lease=lambda: True)
    result = service.submit(_request())
    assert result["success"] is False
    assert result["status_code"] == 409
    assert backend.plans == []


def test_snapshot_degrades_when_lio_status_is_missing_or_broken():
    for status, label in ((None, "상태 미수신"), (RuntimeError("boom"), "상태 조회 실패")):
        service = _service(FakeBackend(status=status))
        snapshot = service.snapshot()
        assert snapshot["available"] is False
        assert snapshot["status"]["phase"] == "unknown"
        assert snapshot["status"]["label"] == label
        assert [item["name"] for item in snapshot["maps"]] == MAP_NAMES
        assert snapshot["maps_available"] is True


def test_snapshot_reports_active_relocalization():
    service = _service(FakeBackend(status="relocalizing_pose"))
    snapshot = service.snapshot()
    assert snapshot["available"] is True
    assert snapshot["status"]["active"] is True
    assert snapshot["status"]["label"] == "초기 위치 기반 재위치추정 중"


def test_mapping_switch_is_accepted_without_map_name():
    backend = FakeBackend()
    service = _service(backend)
    result = service.submit({"mode": "mapping", "confirm": True})
    assert result["status_code"] == 202
    assert service.wait_idle(5.0) is True
    assert backend.plans[0]["mode"] == "mapping"
    assert backend.plans[0]["map_path"] == ""


def test_shutdown_rejects_new_requests():
    backend = FakeBackend()
    service = _service(backend)
    service.shutdown()
    result = service.submit(_request())
    assert result["success"] is False
    assert result["status_code"] == 503
