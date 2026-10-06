"""HTTP 라우터 component 테스트 — fake service 로 rclpy 없이 검증."""

import pytest
from robo_claw_dashboard.application.http_router import route_http

pytestmark = pytest.mark.component


class FakeService:
    def __init__(self, status_payload=None, send_result=None):
        self._status = status_payload or {}
        self._send_result = send_result or {"success": True, "message": "완료"}
        self.sent: list[str] = []

    def status(self):
        return dict(self._status)

    def send_message(self, message):
        self.sent.append(message)
        return dict(self._send_result)


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    (root / "assets" / "app.js").write_text("js", encoding="utf-8")
    return str(root)


def test_get_root_serves_index(web_root):
    resp = route_http("GET", "/", static_root=web_root, service=FakeService())
    assert resp.status == 200
    assert "text/html" in resp.content_type
    assert b"<html>hi</html>" in resp.body


def test_get_asset_serves_js(web_root):
    resp = route_http("GET", "/assets/app.js", static_root=web_root, service=FakeService())
    assert resp.status == 200
    assert "javascript" in resp.content_type
    assert resp.body == b"js"


def test_get_status_returns_snapshot(web_root):
    svc = FakeService(status_payload={"agent": {"state": "EXECUTING"}})
    resp = route_http("GET", "/api/status", static_root=web_root, service=svc)
    assert resp.status == 200
    assert b"EXECUTING" in resp.body


def test_get_health(web_root):
    resp = route_http("GET", "/health", static_root=web_root, service=FakeService())
    assert resp.status == 200
    assert b'"dashboard"' in resp.body


def test_post_chat_valid(web_root):
    svc = FakeService(send_result={"success": True, "message": "완료"})
    resp = route_http(
        "POST",
        "/api/chat",
        static_root=web_root,
        body='{"message":"상태"}'.encode(),
        service=svc,
    )
    assert resp.status == 200
    assert svc.sent == ["상태"]
    assert "완료".encode() in resp.body


def test_post_chat_malformed_json_rejected(web_root):
    svc = FakeService()
    resp = route_http("POST", "/api/chat", static_root=web_root, body=b"{bad", service=svc)
    assert resp.status == 400
    assert svc.sent == []


def test_post_chat_empty_body_rejected(web_root):
    svc = FakeService()
    resp = route_http("POST", "/api/chat", static_root=web_root, body=None, service=svc)
    assert resp.status == 400


def test_post_chat_empty_message_forwards_to_service(web_root):
    import json

    svc = FakeService(send_result={"success": False, "error": "메시지가 비어 있습니다."})
    resp = route_http(
        "POST", "/api/chat", static_root=web_root, body=b'{"message":""}', service=svc
    )
    assert resp.status == 200
    assert json.loads(resp.body)["success"] is False


def test_path_traversal_rejected_404(web_root):
    resp = route_http("GET", "/../secret", static_root=web_root, service=FakeService())
    assert resp.status == 404


def test_unknown_path_404(web_root):
    resp = route_http("GET", "/nope", static_root=web_root, service=FakeService())
    assert resp.status == 404


def test_wrong_method_404(web_root):
    resp = route_http("POST", "/api/status", static_root=web_root, service=FakeService())
    assert resp.status == 404


def test_unauthorized_returns_401(web_root):
    svc = FakeService()
    resp = route_http(
        "GET",
        "/api/status",
        static_root=web_root,
        service=svc,
        authorizer=lambda m, p: False,
    )
    assert resp.status == 401
