"""handle_request 인증+라우팅 component 테스트 — 실제 DashboardHttpSecurityPolicy 사용."""

import pytest
from robo_claw_dashboard.adapters.http_handlers import handle_request
from robo_claw_dashboard.adapters.security import DashboardHttpSecurityPolicy

pytestmark = pytest.mark.component


class FakeService:
    def __init__(self, status_payload=None, send_result=None):
        self._status = status_payload or {"agent": {"state": "IDLE"}}
        self._send_result = send_result or {"success": True, "message": "완료"}

    def status(self):
        return dict(self._status)

    def send_message(self, message):
        return dict(self._send_result)

    def map_snapshot(self):
        return {"available": False}

    def config_snapshot(self):
        return {"available": False}

    def set_initial_pose(self, payload):
        return {"success": True, "message": "ok"}


def _policy(**kw):
    return DashboardHttpSecurityPolicy(
        readonly_token=kw.get("readonly", ""),
        control_token=kw.get("control", ""),
        allowed_cidrs=kw.get("cidrs", ["127.0.0.1/32"]),
        rate_limit_per_minute=kw.get("rate", 60),
        allowed_skills=[],
        blocked_skills=["delete_file", "emergency_stop"],
    )


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text("<html>x</html>", encoding="utf-8")
    return str(root)


class TestAuthDisabledDefaultsToOpen:
    def test_static_open(self, web_root):
        resp = handle_request(
            "GET",
            "/",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=_policy(),
        )
        assert resp.status == 200
        assert b"<html>x</html>" in resp.body

    def test_chat_open_when_no_tokens(self, web_root):
        resp = handle_request(
            "POST",
            "/api/chat",
            client_ip="127.0.0.1",
            headers={},
            body='{"message":"상태"}'.encode(),
            service=FakeService(),
            static_root=web_root,
            security=_policy(),
        )
        assert resp.status == 200

    def test_status_open_when_no_tokens(self, web_root):
        resp = handle_request(
            "GET",
            "/api/status",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=_policy(),
        )
        assert resp.status == 200


class TestAuthEnabled:
    def test_chat_requires_control(self, web_root):
        security = _policy(control="c-secret")
        resp = handle_request(
            "POST",
            "/api/chat",
            client_ip="127.0.0.1",
            headers={},
            body='{"message":"상태"}'.encode(),
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 401

    def test_chat_with_control_token_ok(self, web_root):
        security = _policy(control="c-secret")
        resp = handle_request(
            "POST",
            "/api/chat",
            client_ip="127.0.0.1",
            headers={"X-RoboClaw-Token": "c-secret"},
            body='{"message":"상태"}'.encode(),
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 200

    def test_readonly_token_cannot_post_chat(self, web_root):
        security = _policy(readonly="r-secret", control="c-secret")
        resp = handle_request(
            "POST",
            "/api/chat",
            client_ip="127.0.0.1",
            headers={"Authorization": "Bearer r-secret"},
            body='{"message":"상태"}'.encode(),
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 401

    def test_readonly_can_get_status(self, web_root):
        security = _policy(readonly="r-secret")
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


class TestIpBlock:
    def test_disallowed_ip_403(self, web_root):
        security = _policy(control="c")
        resp = handle_request(
            "POST",
            "/api/chat",
            client_ip="10.0.0.5",
            headers={},
            body=b"{}",
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 403


class TestLocalizationEndpointsAuth:
    def test_initial_pose_requires_control(self, web_root):
        security = _policy(control="c-secret")
        resp = handle_request(
            "POST",
            "/api/initial_pose",
            client_ip="127.0.0.1",
            headers={},
            body=b'{"x": 1.0, "y": 2.0}',
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 401

    def test_initial_pose_with_control_token_ok(self, web_root):
        security = _policy(control="c-secret")
        resp = handle_request(
            "POST",
            "/api/initial_pose",
            client_ip="127.0.0.1",
            headers={"X-RoboClaw-Token": "c-secret"},
            body=b'{"x": 1.0, "y": 2.0}',
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 200

    def test_readonly_token_cannot_set_initial_pose(self, web_root):
        security = _policy(readonly="r-secret", control="c-secret")
        resp = handle_request(
            "POST",
            "/api/initial_pose",
            client_ip="127.0.0.1",
            headers={"Authorization": "Bearer r-secret"},
            body=b'{"x": 1.0, "y": 2.0}',
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 401

    def test_map_requires_readonly_when_auth_enabled(self, web_root):
        security = _policy(readonly="r-secret")
        denied = handle_request(
            "GET",
            "/api/map",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        allowed = handle_request(
            "GET",
            "/api/map",
            client_ip="127.0.0.1",
            headers={"Authorization": "Bearer r-secret"},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert denied.status == 401
        assert allowed.status == 200

    def test_map_open_when_no_tokens(self, web_root):
        resp = handle_request(
            "GET",
            "/api/map",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=_policy(),
        )
        assert resp.status == 200


class TestConfigEndpointAuth:
    def test_config_requires_readonly_when_auth_enabled(self, web_root):
        security = _policy(readonly="r-secret")
        denied = handle_request(
            "GET",
            "/api/config",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        allowed = handle_request(
            "GET",
            "/api/config",
            client_ip="127.0.0.1",
            headers={"Authorization": "Bearer r-secret"},
            body=None,
            service=FakeService(),
            static_root=web_root,
            security=security,
        )
        assert denied.status == 401
        assert allowed.status == 200
