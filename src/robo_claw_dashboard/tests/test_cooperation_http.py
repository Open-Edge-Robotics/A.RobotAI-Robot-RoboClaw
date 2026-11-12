"""협동 관측 HTTP 엔드포인트 component 테스트 — 라우팅, 400 검증, readonly 권한.

``/api/cooperation`` 은 읽기 전용 조회이고, 잘못된 숫자 파라미터는 서비스에 도달하기
전에 400 으로 거절해야 한다. 쿼리 문자열이 붙어도 인증 판정은 path 로만 한다.
"""

from __future__ import annotations

import json

import pytest
from robo_claw_dashboard.adapters.http_handlers import handle_request
from robo_claw_dashboard.adapters.security import DashboardHttpSecurityPolicy
from robo_claw_dashboard.application.http_router import route_http

pytestmark = pytest.mark.component


class FakeCooperationService:
    def __init__(self) -> None:
        self.snapshot_calls: list[dict] = []
        self.export_calls = 0

    def cooperation_snapshot(self, *, after=None, stream_id=None, limit=None):
        self.snapshot_calls.append({"after": after, "stream_id": stream_id, "limit": limit})
        return {
            "available": True,
            "enabled": True,
            "active": True,
            "source": "status-cache",
            "events": [{"sequence": 2, "event_type": "task_completed", "content": "done"}],
            "tasks": [],
            "cursor": 2,
            "next_cursor": 3,
            "oldest_cursor": 2,
            "returned": 1,
            "stale": False,
            "age_ms": 5,
            "gap": None,
            "reset": False,
        }

    def cooperation_export(self):
        self.export_calls += 1
        return (
            "application/x-ndjson; charset=utf-8",
            '{"sequence": 2, "event_type": "task_completed"}\n',
        )


@pytest.fixture
def web_root(tmp_path):
    root = tmp_path / "web"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    return str(root)


def _policy(**kw) -> DashboardHttpSecurityPolicy:
    return DashboardHttpSecurityPolicy(
        readonly_token=kw.get("readonly", ""),
        control_token=kw.get("control", ""),
        allowed_cidrs=kw.get("cidrs", ["127.0.0.1/32"]),
        rate_limit_per_minute=kw.get("rate", 60),
        allowed_skills=[],
        blocked_skills=[],
    )


class TestCooperationRoutes:
    def test_get_cooperation_returns_cached_payload(self, web_root):
        service = FakeCooperationService()
        resp = route_http("GET", "/api/cooperation", static_root=web_root, service=service)
        assert resp.status == 200
        payload = json.loads(resp.body)
        assert payload["available"] is True
        assert payload["events"][0]["sequence"] == 2
        assert service.snapshot_calls == [{"after": None, "stream_id": None, "limit": None}]

    def test_forwards_paging_params(self, web_root):
        service = FakeCooperationService()
        resp = route_http(
            "GET",
            "/api/cooperation?after=4&stream_id=stream-1&limit=50",
            static_root=web_root,
            service=service,
        )
        assert resp.status == 200
        assert service.snapshot_calls == [{"after": 4, "stream_id": "stream-1", "limit": 50}]

    @pytest.mark.parametrize("query", ["after=abc", "after=-1", "limit=abc", "limit=0"])
    def test_invalid_numeric_params_return_400(self, web_root, query):
        service = FakeCooperationService()
        resp = route_http("GET", f"/api/cooperation?{query}", static_root=web_root, service=service)
        assert resp.status == 400
        assert "error" in json.loads(resp.body)
        assert service.snapshot_calls == []

    def test_export_returns_jsonl_attachment(self, web_root):
        service = FakeCooperationService()
        resp = route_http("GET", "/api/cooperation/export", static_root=web_root, service=service)
        assert resp.status == 200
        assert resp.content_type.startswith("application/x-ndjson")
        assert resp.headers.get("Content-Disposition", "").startswith("attachment;")
        assert b'"sequence": 2' in resp.body
        assert service.export_calls == 1

    def test_post_is_not_routed(self, web_root):
        resp = route_http(
            "POST", "/api/cooperation", static_root=web_root, service=FakeCooperationService()
        )
        assert resp.status == 404

    def test_authorizer_receives_path_without_query(self, web_root):
        seen: list[tuple[str, str]] = []

        def authorizer(method: str, path: str) -> bool:
            seen.append((method, path))
            return True

        route_http(
            "GET",
            "/api/cooperation?limit=10",
            static_root=web_root,
            service=FakeCooperationService(),
            authorizer=authorizer,
        )
        assert seen == [("GET", "/api/cooperation")]


class TestCooperationAuth:
    def test_cooperation_requires_readonly_when_auth_enabled(self, web_root):
        security = _policy(readonly="r-secret")
        denied = handle_request(
            "GET",
            "/api/cooperation?after=1",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeCooperationService(),
            static_root=web_root,
            security=security,
        )
        allowed = handle_request(
            "GET",
            "/api/cooperation?after=1",
            client_ip="127.0.0.1",
            headers={"Authorization": "Bearer r-secret"},
            body=None,
            service=FakeCooperationService(),
            static_root=web_root,
            security=security,
        )
        assert denied.status == 401
        assert allowed.status == 200

    def test_cooperation_export_requires_readonly_when_auth_enabled(self, web_root):
        security = _policy(readonly="r-secret")
        denied = handle_request(
            "GET",
            "/api/cooperation/export",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeCooperationService(),
            static_root=web_root,
            security=security,
        )
        allowed = handle_request(
            "GET",
            "/api/cooperation/export",
            client_ip="127.0.0.1",
            headers={"X-RoboClaw-Token": "r-secret"},
            body=None,
            service=FakeCooperationService(),
            static_root=web_root,
            security=security,
        )
        assert denied.status == 401
        assert allowed.status == 200

    def test_cooperation_is_open_when_no_tokens(self, web_root):
        resp = handle_request(
            "GET",
            "/api/cooperation",
            client_ip="127.0.0.1",
            headers={},
            body=None,
            service=FakeCooperationService(),
            static_root=web_root,
            security=_policy(),
        )
        assert resp.status == 200

    def test_cooperation_is_not_a_control_endpoint(self, web_root):
        """읽기 전용 경로는 control 토큰이 필요하지 않다(조회는 누구나 가능)."""
        security = _policy(control="c-secret")
        resp = handle_request(
            "GET",
            "/api/cooperation",
            client_ip="127.0.0.1",
            headers={"Authorization": "Bearer c-secret"},
            body=None,
            service=FakeCooperationService(),
            static_root=web_root,
            security=security,
        )
        assert resp.status == 200


class TestControlSurface:
    def test_no_robot_control_routes_are_added_by_cooperation(self):
        """협동 패널은 조회 전용이므로 POST 계열 control 경로가 늘어나면 안 된다."""
        assert DashboardHttpSecurityPolicy.CONTROL_POST_PATHS.isdisjoint(
            {"/api/cooperation", "/api/cooperation/export"}
        )
        assert {"/api/cooperation", "/api/cooperation/export"} <= (
            DashboardHttpSecurityPolicy.READONLY_GET_PATHS
        )
