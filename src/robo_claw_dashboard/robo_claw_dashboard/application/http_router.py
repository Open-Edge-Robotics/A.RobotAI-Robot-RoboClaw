"""HTTP 라우팅 — 정적 리소스 서빙과 /api 엔드포인트 분기.

http.server 나 rclpy 와 무관한 순수 로직으로, 테스트에서는 fake service 로 검증한다.
실제 ROS2 노드는 이 함수를 호출하기만 하면 된다.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from ..domain.cooperation import coerce_cursor, coerce_limit
from ..domain.paths import mime_type, safe_join_web_path

Status = int


@dataclass
class HttpResponse:
    status: Status
    content_type: str
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)


def _json(
    data: dict[str, Any],
    status: Status = 200,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return HttpResponse(
        status,
        "application/json; charset=utf-8",
        body,
        headers=headers or {},
    )


def _serve_static(static_root: str, request_path: str) -> HttpResponse:
    file_path = safe_join_web_path(static_root, request_path)
    if file_path is None:
        return _json({"error": "not found"}, 404)
    return HttpResponse(200, mime_type(file_path), Path(file_path).read_bytes())


def _parse_body(body: Any) -> tuple[bool, Any]:
    """요청 본문을 JSON 으로 파싱한다. (ok, payload) 를 반환한다."""
    if body in (None, b"", ""):
        return False, None
    try:
        payload = (
            json.loads(body.decode("utf-8"))
            if isinstance(body, (bytes, bytearray))
            else (json.loads(body) if isinstance(body, str) else body)
        )
    except (TypeError, ValueError):
        return False, None
    return True, payload


def _handle_chat(body: Any, service: Any) -> HttpResponse:
    """POST /api/chat - 일반 채팅은 동기, map_goal 모드는 202 비동기 접수."""
    ok, payload = _parse_body(body)
    if not ok:
        return _json({"error": "잘못된 JSON"}, 400)

    if isinstance(payload, dict) and "map_goal" in payload:
        # map_goal 이 null/문자열이어도 일반 채팅으로 흘려보내지 않는다.
        res = service.submit_map_goal(payload)
        status_code = int(res.pop("status_code", 202 if res.get("success") else 400))
        return _json(res, status=status_code)

    message = payload.get("message", "") if isinstance(payload, dict) else ""
    return _json(service.send_message(message))


def _handle_initial_pose(body: Any, service: Any) -> HttpResponse:
    """POST /api/initial_pose - 좌표를 검증하고 AMCL 초기 위치를 설정한다."""
    ok, payload = _parse_body(body)
    if not ok:
        return _json({"error": "잘못된 JSON"}, 400)
    return _json(service.set_initial_pose(payload))


def _split_query(request_path: str) -> tuple[str, dict[str, str]]:
    """URL path 에서 쿼리 문자열을 분리한다(같은 키가 여러 번 오면 마지막 값)."""
    path, _, raw = str(request_path or "/").partition("?")
    if not raw:
        return path or "/", {}
    try:
        parsed = parse_qs(raw, keep_blank_values=False)
    except ValueError:  # 기형 쿼리 문자열은 무시하고 path 만 라우팅한다
        return path or "/", {}
    return path or "/", {key: values[-1] for key, values in parsed.items() if values}


def _parse_limit(raw: str | None) -> int | None:
    """`limit` 쿼리를 정수로 바꾼다(없거나 잘못된 값은 None → 서비스 기본값)."""
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _handle_logs(query: dict[str, str], service: Any) -> HttpResponse:
    """GET /api/logs - 레벨/검색어/줄 수 조건으로 최근 로그를 반환한다."""
    return _json(
        service.logs_snapshot(
            level=query.get("level"), query=query.get("q"), limit=_parse_limit(query.get("limit"))
        )
    )


def _handle_logs_export(query: dict[str, str], service: Any) -> HttpResponse:
    """GET /api/logs/export - 로그를 파일(.log/.jsonl) 본문으로 반환한다."""
    content_type, text = service.logs_export(
        level=query.get("level"),
        query=query.get("q"),
        limit=_parse_limit(query.get("limit")),
        fmt=query.get("format") or "txt",
    )
    return HttpResponse(200, content_type, text.encode("utf-8"))


def _handle_cooperation(query: dict[str, str], service: Any) -> HttpResponse:
    """GET /api/cooperation - 캐시된 최근 협동 관측을 커서 조건으로 반환한다.

    숫자 파라미터가 정수가 아니면 서비스에 닿기 전에 400 으로 거절한다.
    """
    try:
        after = coerce_cursor(query["after"]) if "after" in query else None
        limit = coerce_limit(query["limit"]) if "limit" in query else None
    except ValueError as exc:
        return _json({"error": str(exc)}, 400)
    return _json(
        service.cooperation_snapshot(after=after, stream_id=query.get("stream_id"), limit=limit)
    )


def _handle_cooperation_export(service: Any) -> HttpResponse:
    """GET /api/cooperation/export - 최근 협동 관측을 JSONL 첨부 파일로 반환한다."""
    content_type, text = service.cooperation_export()
    return HttpResponse(
        200,
        content_type,
        text.encode("utf-8"),
        headers={"Content-Disposition": 'attachment; filename="robo_claw_cooperation.jsonl"'},
    )


def _handle_scenario_run(body: Any, service: Any) -> HttpResponse:
    """POST /api/scenarios/run - 시나리오 테스트 실행."""
    payload: dict[str, Any] = {}
    if body not in (None, b"", ""):
        ok, raw = _parse_body(body)
        if not ok or not isinstance(raw, dict):
            return _json({"error": "잘못된 JSON 형식입니다."}, 400)
        payload = raw

    res = service.start_scenario_run(payload)
    status_code = int(res.pop("status_code", 202 if res.get("accepted") else 400))
    return _json(res, status=status_code)


def _handle_scenario_sync(body: Any, service: Any) -> HttpResponse:
    """POST /api/scenarios/sync - 대시보드 입력값으로 AI Config 서버 시나리오를 동기화한다.

    디바이스 토큰이 본문에 포함되므로 쿼리 문자열이 아닌 JSON body 로만 받는다
    (URL 은 접근·오류 로그에 남기 때문).
    """
    ok, raw = _parse_body(body)
    if not ok or not isinstance(raw, dict):
        return _json({"error": "서버 설정 JSON 본문이 필요합니다."}, 400)

    res = service.sync_scenario(raw)
    status_code = int(res.pop("status_code", 200))
    return _json(res, status=status_code)


def _handle_scenario_report(query: dict[str, str], service: Any) -> HttpResponse:
    """GET /api/scenarios/report - 보고서 다운로드."""
    fmt = (query.get("format") or "md").lower()
    rep = service.scenario_report(fmt=fmt)
    if not rep.get("found"):
        return _json(
            {"error": rep.get("error", "not found")},
            status=int(rep.get("status_code", 404)),
        )
    return HttpResponse(
        status=200,
        content_type=rep["content_type"],
        body=rep["body"],
        headers={"Content-Disposition": f'attachment; filename="{rep["filename"]}"'},
    )


def route_http(
    method: str,
    request_path: str,
    *,
    static_root: str,
    service: Any,
    body: Any = None,
    authorizer: Callable[[str, str], bool] | None = None,
) -> HttpResponse:
    """HTTP 요청(method/path/body)을 대시보드 리소스/API로 라우팅한다."""
    path, query = _split_query(request_path)

    if authorizer is not None and not authorizer(method, path):
        return _json({"error": "권한 없음"}, 401)

    if method == "GET" and (path == "/" or path.startswith("/assets/")):
        return _serve_static(static_root, path)
    if method == "GET" and path == "/api/status":
        return _json(service.status())
    if method == "GET" and path == "/health":
        return _json({"status": "ok", "dashboard": "robo_claw_dashboard"})
    if method == "GET" and path == "/api/map":
        return _json(service.map_snapshot())
    if method == "GET" and path == "/api/config":
        return _json(service.config_snapshot())
    if method == "GET" and path == "/api/logs":
        return _handle_logs(query, service)
    if method == "GET" and path == "/api/logs/export":
        return _handle_logs_export(query, service)
    if method == "GET" and path == "/api/cooperation":
        return _handle_cooperation(query, service)
    if method == "GET" and path == "/api/cooperation/export":
        return _handle_cooperation_export(service)
    if method == "POST" and path == "/api/chat":
        return _handle_chat(body, service)
    if method == "POST" and path == "/api/initial_pose":
        return _handle_initial_pose(body, service)

    # 시나리오 테스트 엔드포인트
    if method == "GET" and path == "/api/scenarios":
        refresh = (query.get("refresh") or "0").lower() in ("1", "true", "yes")
        return _json(service.get_scenario(refresh=refresh))
    if method == "POST" and path == "/api/scenarios/sync":
        return _handle_scenario_sync(body, service)
    if method == "POST" and path == "/api/scenarios/run":
        return _handle_scenario_run(body, service)
    if method == "POST" and path == "/api/scenarios/cancel":
        return _json(service.cancel_scenario_run())
    if method == "GET" and path == "/api/scenarios/status":
        return _json(service.scenario_status())
    if method == "GET" and path == "/api/scenarios/report":
        return _handle_scenario_report(query, service)

    return _json({"error": f"알 수 없는 경로: {method} {path}"}, 404)
