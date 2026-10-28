"""HTTP 라우터 component 테스트 — fake service 로 rclpy 없이 검증."""

import pytest
from robo_claw_dashboard.application.http_router import route_http

pytestmark = pytest.mark.component


class FakeService:
    def __init__(self, status_payload=None, send_result=None):
        self._status = status_payload or {}
        self._send_result = send_result or {"success": True, "message": "완료"}
        self.sent: list[str] = []
        self.initial_pose_payloads: list = []
        self.log_calls: list[dict] = []
        self.log_export_calls: list[dict] = []
        self._initial_pose_result = {"success": True, "message": "초기 위치를 설정했습니다"}

    def status(self):
        return dict(self._status)

    def send_message(self, message):
        self.sent.append(message)
        return dict(self._send_result)

    def map_snapshot(self):
        return {"available": False}

    def config_snapshot(self):
        return {"available": False, "agent": {"parameters": []}}

    def set_initial_pose(self, payload):
        self.initial_pose_payloads.append(payload)
        return dict(self._initial_pose_result)

    def logs_snapshot(self, **kwargs):
        self.log_calls.append(dict(kwargs))
        return {"available": True, "entries": [{"message": "patrol failed"}]}

    def logs_export(self, **kwargs):
        self.log_export_calls.append(dict(kwargs))
        if kwargs.get("fmt") == "jsonl":
            return "application/x-ndjson; charset=utf-8", '{"message": "patrol failed"}\n'
        return "text/plain; charset=utf-8", "2023-11-14T22:13:20.125Z WARN  /nav2: patrol failed\n"


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


def test_get_map_returns_service_snapshot(web_root):
    class MapService(FakeService):
        def map_snapshot(self):
            return {"available": True, "width": 2, "height": 1, "data": "AAE="}

    resp = route_http("GET", "/api/map", static_root=web_root, service=MapService())
    assert resp.status == 200
    assert b'"width": 2' in resp.body


def test_post_initial_pose_forwards_payload(web_root):
    service = FakeService()
    resp = route_http(
        "POST",
        "/api/initial_pose",
        static_root=web_root,
        service=service,
        body=b'{"x": 1.5, "y": -2.0, "yaw": 0.3}',
    )
    assert resp.status == 200
    assert service.initial_pose_payloads == [{"x": 1.5, "y": -2.0, "yaw": 0.3}]
    assert b"success" in resp.body


def test_post_initial_pose_rejects_invalid_json(web_root):
    service = FakeService()
    resp = route_http(
        "POST", "/api/initial_pose", static_root=web_root, service=service, body=b"{oops"
    )
    assert resp.status == 400
    assert service.initial_pose_payloads == []


def test_post_initial_pose_rejects_empty_body(web_root):
    resp = route_http(
        "POST", "/api/initial_pose", static_root=web_root, service=FakeService(), body=b""
    )
    assert resp.status == 400


def test_get_config_returns_panel(web_root):
    class ConfigService(FakeService):
        def config_snapshot(self):
            return {"available": True, "counts": {"agent": 1, "dashboard": 0}}

    resp = route_http("GET", "/api/config", static_root=web_root, service=ConfigService())
    assert resp.status == 200
    assert b'"available": true' in resp.body


class TestLogsEndpoints:
    def test_get_logs_forwards_filters(self, web_root):
        svc = FakeService()
        resp = route_http(
            "GET",
            "/api/logs?level=warn&q=patrol&limit=50",
            static_root=web_root,
            service=svc,
        )
        assert resp.status == 200
        assert b"patrol failed" in resp.body
        assert svc.log_calls == [{"level": "warn", "query": "patrol", "limit": 50}]

    def test_get_logs_without_query_uses_defaults(self, web_root):
        svc = FakeService()
        resp = route_http("GET", "/api/logs", static_root=web_root, service=svc)
        assert resp.status == 200
        assert svc.log_calls == [{"level": None, "query": None, "limit": None}]

    def test_get_logs_invalid_limit_is_ignored(self, web_root):
        svc = FakeService()
        route_http("GET", "/api/logs?limit=abc", static_root=web_root, service=svc)
        assert svc.log_calls == [{"level": None, "query": None, "limit": None}]

    def test_export_returns_text_file(self, web_root):
        svc = FakeService()
        resp = route_http(
            "GET", "/api/logs/export?format=txt&level=error", static_root=web_root, service=svc
        )
        assert resp.status == 200
        assert resp.content_type.startswith("text/plain")
        assert b"patrol failed" in resp.body
        assert svc.log_export_calls == [
            {"level": "error", "query": None, "limit": None, "fmt": "txt"}
        ]

    def test_export_supports_jsonl(self, web_root):
        resp = route_http(
            "GET", "/api/logs/export?format=jsonl", static_root=web_root, service=FakeService()
        )
        assert resp.status == 200
        assert resp.content_type.startswith("application/x-ndjson")
        assert resp.body.startswith(b'{"message"')

    def test_static_path_with_query_still_serves_index(self, web_root):
        resp = route_http("GET", "/?tab=logs", static_root=web_root, service=FakeService())
        assert resp.status == 200
        assert b"<html>hi</html>" in resp.body

    def test_authorizer_receives_path_without_query(self, web_root):
        seen: list[tuple[str, str]] = []

        def authorizer(method: str, path: str) -> bool:
            seen.append((method, path))
            return True

        route_http(
            "GET",
            "/api/logs?limit=10",
            static_root=web_root,
            service=FakeService(),
            authorizer=authorizer,
        )
        assert seen == [("GET", "/api/logs")]
