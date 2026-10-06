"""HTTP 라우팅 — 정적 리소스 서빙과 /api 엔드포인트 분기.

http.server 나 rclpy 와 무관한 순수 로직으로, 테스트에서는 fake service 로 검증한다.
실제 ROS2 노드는 이 함수를 호출하기만 하면 된다.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..domain.paths import mime_type, safe_join_web_path

Status = int


@dataclass
class HttpResponse:
    status: Status
    content_type: str
    body: bytes


def _json(data: dict[str, Any], status: Status = 200) -> HttpResponse:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return HttpResponse(status, "application/json; charset=utf-8", body)


def _serve_static(static_root: str, request_path: str) -> HttpResponse:
    file_path = safe_join_web_path(static_root, request_path)
    if file_path is None:
        return _json({"error": "not found"}, 404)
    return HttpResponse(200, mime_type(file_path), Path(file_path).read_bytes())


def _handle_chat(body: Any, service: Any) -> HttpResponse:
    """POST /api/chat - JSON body 를 검증하고 에이전트 메시지 전송."""
    if body in (None, b"", ""):
        return _json({"error": "잘못된 JSON"}, 400)
    try:
        payload = (
            json.loads(body.decode("utf-8"))
            if isinstance(body, (bytes, bytearray))
            else (json.loads(body) if isinstance(body, str) else body)
        )
    except (TypeError, ValueError):
        return _json({"error": "잘못된 JSON"}, 400)

    message = payload.get("message", "") if isinstance(payload, dict) else ""
    return _json(service.send_message(message))


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
    if authorizer is not None and not authorizer(method, request_path):
        return _json({"error": "권한 없음"}, 401)

    if method == "GET" and (request_path == "/" or request_path.startswith("/assets/")):
        return _serve_static(static_root, request_path)
    if method == "GET" and request_path == "/api/status":
        return _json(service.status())
    if method == "GET" and request_path == "/health":
        return _json({"status": "ok", "dashboard": "robo_claw_dashboard"})
    if method == "POST" and request_path == "/api/chat":
        return _handle_chat(body, service)

    return _json({"error": f"알 수 없는 경로: {method} {request_path}"}, 404)
