"""지도 전환 HTTP 라우팅/권한 테스트 — fake service 로 rclpy 없이 검증."""

import json

import pytest
from robo_claw_dashboard.adapters.http_handlers import handle_request
from robo_claw_dashboard.adapters.security import DashboardHttpSecurityPolicy
from robo_claw_dashboard.application.http_router import route_http

pytestmark = pytest.mark.component

SWITCH_RECORD = {
    "request_id": "loc-1",
    "mode": "localization",
    "map": "konjiam_L_107",
    "map_path": "/ws/maps/konjiam_L_107",
    "set_initial_pose": False,
    "pose": None,
    "status": "queued",
    "message": "접수됨",
    "created_at": 1.0,
    "updated_at": 1.0,
}

SNAPSHOT = {
    "available": True,
    "status": {
        "available": True,
        "raw": "ready",
        "label": "위치추정 준비 완료",
        "phase": "ready",
        "mode": "localization",
        "active": False,
        "failed": False,
        "stamp_ns": 10,
    },
    "maps": [{"name": "konjiam_L_107", "path": "/ws/maps/konjiam_L_107"}],
    "map_root": "/ws/maps",
    "maps_available": True,
    "current_map": None,
    "control": {"available": True, "reason": "", "busy": False},
    "last_switch": None,
}


class FakeService:
    def __init__(self):
        self.payloads: list = []
        self.localization_calls = 0
        self._switch_result = {
            "success": True,
            "request": dict(SWITCH_RECORD),
            "status_code": 202,
        }

    def localization_snapshot(self):
        self.localization_calls += 1
        return dict(SNAPSHOT)

    def switch_localization_map(self, payload):
        self.payloads.append(payload)
        return dict(self._switch_result)

    # 다른 경로가 같은 fake 로 호출되어도 죽지 않도록 최소 구현
    def status(self):
        return {"agent": {"state": "IDLE"}}

    def map_snapshot(self):
        return {"available": False}


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    root.mkdir(parents=True)
    (root / "index.html").write_text("<html>x</html>", encoding="utf-8")
    return str(root)


def _security(**kwargs):
    params = {
        "readonly_token": "",
        "control_token": "",
        "allowed_cidrs": ["127.0.0.1/32"],
        "rate_limit_per_minute": 60,
        "allowed_skills": [],
        "blocked_skills": [],
    }
    params.update(kwargs)
    return DashboardHttpSecurityPolicy(**params)


def _post(service, body, web_root, *, headers=None, security=None, path="/api/localization/switch"):
    return handle_request(
        "POST",
        path,
        client_ip="127.0.0.1",
        headers=headers or {},
        body=json.dumps(body).encode("utf-8"),
        service=service,
        static_root=web_root,
        security=security or _security(),
    )


class TestLocalizationRouting:
    def test_snapshot_route_returns_lio_status_and_maps(self, web_root):
        service = FakeService()
        resp = route_http("GET", "/api/localization", static_root=web_root, service=service)
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["status"]["raw"] == "ready"
        assert data["maps"][0]["name"] == "konjiam_L_107"
        assert service.localization_calls == 1

    def test_switch_returns_202_and_queued_request(self, web_root):
        service = FakeService()
        body = {"map": "konjiam_L_107", "confirm": True}
        resp = _post(service, body, web_root)
        assert resp.status == 202
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["request"]["request_id"] == "loc-1"
        assert service.payloads == [body]

    def test_malformed_json_is_rejected_before_service(self, web_root):
        service = FakeService()
        resp = handle_request(
            "POST",
            "/api/localization/switch",
            client_ip="127.0.0.1",
            headers={},
            body=b"{not json",
            service=service,
            static_root=web_root,
            security=_security(),
        )
        assert resp.status == 400
        assert service.payloads == []

    @pytest.mark.parametrize(
        "code,error", [(400, "지도 이름(map)이 필요합니다"), (409, "이미 진행 중"), (503, "미설치")]
    )
    def test_service_status_codes_are_preserved(self, web_root, code, error):
        service = FakeService()
        service._switch_result = {"success": False, "error": error, "status_code": code}
        resp = _post(service, {"map": "x", "confirm": True}, web_root)
        assert resp.status == code
        assert json.loads(resp.body)["error"] == error

    def test_unknown_path_still_404(self, web_root):
        resp = route_http(
            "POST", "/api/localization/nope", static_root=web_root, service=FakeService()
        )
        assert resp.status == 404


class TestLocalizationAuthorization:
    def test_snapshot_requires_readonly_token_when_auth_enabled(self, web_root):
        security = _security(readonly_token="r-secret", control_token="c-secret")
        denied = handle_request(
            "GET",
            "/api/localization",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert denied.status == 401

        allowed = handle_request(
            "GET",
            "/api/localization",
            client_ip="127.0.0.1",
            headers={"Authorization": "Bearer r-secret"},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert allowed.status == 200

    def test_readonly_token_cannot_switch_map(self, web_root):
        security = _security(readonly_token="r-secret", control_token="c-secret")
        service = FakeService()
        denied = _post(
            service,
            {"map": "konjiam_L_107", "confirm": True},
            web_root,
            headers={"Authorization": "Bearer r-secret"},
            security=security,
        )
        assert denied.status == 401
        assert service.payloads == []

        allowed = _post(
            service,
            {"map": "konjiam_L_107", "confirm": True},
            web_root,
            headers={"X-RoboClaw-Token": "c-secret"},
            security=security,
        )
        assert allowed.status == 202
        assert service.payloads
