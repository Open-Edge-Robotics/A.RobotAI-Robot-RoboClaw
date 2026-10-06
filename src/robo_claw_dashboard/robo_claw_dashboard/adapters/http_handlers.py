"""HTTP 핸들러 — 인증을 수행한 뒤 route_http 로 라우팅한다.

http.server 의 BaseHTTPRequestHandler 는 socket 이 필요해 단위 테스트가 어려우므로,
실질 로직은 순수 함수 ``handle_request`` 에 두어 component 테스트로 검증한다.
"""

import json
import logging
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

from ..application.http_router import HttpResponse, route_http

_logger = logging.getLogger(__name__)
_JSON_CONTENT_TYPE = "application/json; charset=utf-8"


def _send_json(status: int, payload: dict[str, Any]) -> HttpResponse:
    return HttpResponse(
        status,
        _JSON_CONTENT_TYPE,
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )


def handle_request(
    method: str,
    request_path: str,
    *,
    client_ip: str,
    headers: Any,
    body: Any,
    service: Any,
    static_root: str,
    security: Any,
) -> HttpResponse:
    """인증 후 route_http 를 호출해 응답을 구성한다."""
    result = security.authorize(
        client_ip=client_ip, method=method, path=request_path, headers=headers
    )
    if not result.allowed:
        return _send_json(result.status_code, result.payload)
    return route_http(
        method,
        request_path,
        static_root=static_root,
        service=service,
        body=body,
    )


def make_handler(service: Any, static_root: str, security: Any) -> type:
    """service/static_root/security 를 캡처한 BaseHTTPRequestHandler 서브클래스를 생성한다."""

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: N802
            _logger.debug(format, *args)

        def _read_body(self) -> bytes:
            try:
                length = int(self.headers.get("Content-Length", 0))
            except (TypeError, ValueError):
                length = 0
            return self.rfile.read(length) if length else b""

        def _dispatch(self) -> None:
            request_path = urlparse(self.path).path
            body = self._read_body() if self.command == "POST" else None
            resp = handle_request(
                method=self.command,
                request_path=request_path,
                client_ip=self.client_address[0] if self.client_address else "",
                headers=self.headers,
                body=body,
                service=service,
                static_root=static_root,
                security=security,
            )
            self.send_response(resp.status)
            self.send_header("Content-Type", resp.content_type)
            self.send_header("Content-Length", str(len(resp.body)))
            self.end_headers()
            self.wfile.write(resp.body)

        def do_GET(self) -> None:  # noqa: N802
            self._dispatch()

        def do_POST(self) -> None:  # noqa: N802
            self._dispatch()

    return _Handler
