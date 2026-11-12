"""지도 목표 HTTP 라우팅/권한 테스트 — fake service 로 rclpy 없이 검증."""

import json

import pytest
from robo_claw_dashboard.adapters.http_handlers import handle_request
from robo_claw_dashboard.adapters.security import DashboardHttpSecurityPolicy
from robo_claw_dashboard.application.http_router import route_http

pytestmark = pytest.mark.component

RECORD = {
    "request_id": "req-1",
    "robot_id": "former0047",
    "status": "queued",
    "message": "접수",
    "target": {"x": 0.75, "y": -1.75, "frame_id": "map"},
    "geometry_id": "a" * 64,
    "execution_started": False,
    "cancel_confirmed": False,
    "reason": "",
    "progress": None,
    "created_at": 1.0,
    "updated_at": 1.0,
    "can_submit": False,
}


class FakeService:
    def __init__(self):
        self.sent: list[str] = []
        self.map_goal_payloads: list[dict] = []
        self._submit_result = {"success": True, "request": dict(RECORD), "status_code": 202}

    def status(self):
        return {"agent": {"state": "IDLE"}, "navigation_request": dict(RECORD)}

    def send_message(self, message):
        self.sent.append(message)
        return {"success": True, "message": "완료"}

    def submit_map_goal(self, payload):
        self.map_goal_payloads.append(payload)
        if not isinstance(payload.get("map_goal"), dict):
            return {
                "success": False,
                "error": "map_goal 은 JSON 객체여야 합니다",
                "status_code": 400,
            }
        return dict(self._submit_result)

    def map_snapshot(self):
        return {"available": False}

    def set_initial_pose(self, payload):
        return {"success": True}


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    root.mkdir(parents=True)
    (root / "index.html").write_text("<html>x</html>", encoding="utf-8")
    return str(root)


def goal_body(request_id="req-1"):
    return {
        "message": "지도에서 선택한 목표로 이동해 주세요.",
        "request_id": request_id,
        "map_goal": {
            "x": 0.75,
            "y": -1.75,
            "frame_id": "map",
            "robot_id": "former0047",
            "geometry_id": "a" * 64,
        },
    }


def post(service, body, web_root, headers=None, security=None):
    return handle_request(
        "POST",
        "/api/chat",
        client_ip="127.0.0.1",
        headers=headers or {},
        body=json.dumps(body).encode("utf-8"),
        service=service,
        static_root=web_root,
        security=security
        or DashboardHttpSecurityPolicy(
            readonly_token="",
            control_token="",
            allowed_cidrs=["127.0.0.1/32"],
            rate_limit_per_minute=60,
            allowed_skills=[],
            blocked_skills=[],
        ),
    )


class TestMapGoalRouting:
    def test_map_goal_body_returns_202_and_request(self, web_root):
        service = FakeService()
        resp = post(service, goal_body(), web_root)
        assert resp.status == 202
        data = json.loads(resp.body)
        assert data["success"] is True
        assert data["request"]["request_id"] == "req-1"
        assert service.sent == []
        assert service.map_goal_payloads == [goal_body()]

    def test_plain_message_remains_synchronous_200(self, web_root):
        service = FakeService()
        resp = post(service, {"message": "상태"}, web_root)
        assert resp.status == 200
        assert service.sent == ["상태"]
        assert service.map_goal_payloads == []

    @pytest.mark.parametrize("bad", [None, "x", 3, []])
    def test_malformed_map_goal_never_falls_through_as_text(self, web_root, bad):
        service = FakeService()
        body = {"message": "지도로 이동", "map_goal": bad}
        resp = post(service, body, web_root)
        assert resp.status == 400
        assert service.sent == []
        assert service.map_goal_payloads == [body]

    def test_conflict_response_keeps_authoritative_request(self, web_root):
        service = FakeService()
        service._submit_result = {
            "success": False,
            "error": "다른 지도 목표가 진행 중입니다",
            "status_code": 409,
            "request": dict(RECORD, request_id="req-active", status="running"),
        }
        resp = post(service, goal_body(), web_root)
        assert resp.status == 409
        data = json.loads(resp.body)
        assert data["request"]["request_id"] == "req-active"
        assert data["request"]["status"] == "running"

    def test_service_errors_map_to_status_codes(self, web_root):
        service = FakeService()
        for code, error in ((400, "잘못된 목표"), (409, "이미 진행 중"), (503, "지도 없음")):
            service._submit_result = {"success": False, "error": error, "status_code": code}
            resp = post(service, goal_body(), web_root)
            assert resp.status == code
            assert json.loads(resp.body)["error"] == error

    def test_status_exposes_top_level_navigation_request(self, web_root):
        resp = route_http("GET", "/api/status", static_root=web_root, service=FakeService())
        assert resp.status == 200
        data = json.loads(resp.body)
        assert data["navigation_request"]["request_id"] == "req-1"


class TestMapGoalAuthorization:
    def test_readonly_token_cannot_submit_map_goal(self, web_root):
        security = DashboardHttpSecurityPolicy(
            readonly_token="r-secret",
            control_token="c-secret",
            allowed_cidrs=["127.0.0.1/32"],
            rate_limit_per_minute=60,
            allowed_skills=[],
            blocked_skills=[],
        )
        service = FakeService()
        denied = post(service, goal_body(), web_root, headers={}, security=security)
        assert denied.status == 401
        assert service.map_goal_payloads == []

        allowed = post(
            service,
            goal_body(),
            web_root,
            headers={"X-RoboClaw-Token": "c-secret"},
            security=security,
        )
        assert allowed.status == 202
        assert service.map_goal_payloads

    def test_readonly_status_query_still_allowed(self, web_root):
        security = DashboardHttpSecurityPolicy(
            readonly_token="r-secret",
            control_token="c-secret",
            allowed_cidrs=["127.0.0.1/32"],
            rate_limit_per_minute=60,
            allowed_skills=[],
            blocked_skills=[],
        )
        resp = handle_request(
            "GET",
            "/api/status",
            client_ip="127.0.0.1",
            headers={"Authorization": "Bearer r-secret"},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 200
