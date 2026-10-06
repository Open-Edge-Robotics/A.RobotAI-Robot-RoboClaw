"""HTTP REST 핸들러 팩토리.

ChannelNode 참조를 캡처해 ``BaseHTTPRequestHandler`` 하위 클래스를 생성한다.
라우팅: POST /task, POST /skill, GET /status, GET /health.
"""

import json
import logging
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def make_http_handler(node) -> type:
    """ChannelNode 참조를 캡처한 HTTP 핸들러 클래스를 생성한다."""

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: N802
            logger.debug(fmt, *args)

        def _send_json(self, data: dict[str, Any], code: int = 200) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorize(self, method: str, path: str) -> str | None:
            client_ip = self.client_address[0] if self.client_address else ""
            result = node._http_security.authorize(
                client_ip=client_ip,
                method=method,
                path=path,
                headers=self.headers,
            )
            if not result.allowed:
                logger.warning(
                    "HTTP access denied [%s %s] ip=%s reason=%s",
                    method,
                    path,
                    client_ip,
                    result.payload.get("error", "unknown"),
                )
                self._send_json(result.payload, result.status_code)
                return None
            return result.role

        def _read_json(self) -> dict[str, Any] | None:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError as e:
                logger.warning("Failed to parse request JSON: %s", e)
                return None

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if self._authorize("POST", path) is None:
                return
            body = self._read_json()
            if body is None:
                self._send_json({"error": "잘못된 JSON"}, 400)
                return

            if path == "/task":
                instruction = body.get("instruction", "")
                context = body.get("context", "")
                if not instruction:
                    self._send_json({"error": "instruction 필드 필요"}, 400)
                    return

                logger.info(f"[HTTP POST /task] instruction='{instruction}'")
                result = node.send_task(instruction, context)
                logger.info(
                    f"[HTTP POST /task] result: success={result.get('success')}, "
                    f"message='{result.get('message')}'"
                )
                self._send_json(result)

            elif path == "/skill":
                skill_name = body.get("skill_name", "")
                params = body.get("params", {})
                if not skill_name:
                    self._send_json({"error": "skill_name 필드 필요"}, 400)
                    return
                skill_error = node._http_security.is_skill_allowed(skill_name)
                if skill_error:
                    self._send_json({"error": skill_error}, 403)
                    return

                logger.info(f"[HTTP POST /skill] skill='{skill_name}', params={params}")
                result = node.call_skill(
                    skill_name, json.dumps(params, ensure_ascii=False)
                )
                logger.info(
                    f"[HTTP POST /skill] result: success={result.get('success')}, "
                    f"message='{result.get('message')}'"
                )
                self._send_json(result)

            else:
                self._send_json({"error": f"알 수 없는 경로: {path}"}, 404)

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if self._authorize("GET", path) is None:
                return
            if path == "/status":
                result = node.get_status()
                self._send_json(result)
            elif path == "/health":
                self._send_json(
                    {
                        "status": "ok",
                        "agent_namespace": node._agent_ns,
                        "messenger_channel_count": len(node._messenger_channels),
                        "clients": {
                            "execute_task_ready": node._task_client.server_is_ready(),
                            "execute_skill_ready": node._skill_client.service_is_ready(),
                            "query_state_ready": node._query_client.service_is_ready(),
                        },
                        "http_security": {
                            "auth_enabled": node._http_security.auth_enabled,
                            "allowed_cidrs": node._http_security.allowed_cidrs,
                            "rate_limit_per_minute": node._http_security.rate_limit_per_minute,
                            **node._http_security.skill_controls(),
                        },
                        "metrics": node._rpc_snapshot(),
                    }
                )
            else:
                self._send_json({"error": f"알 수 없는 경로: {path}"}, 404)

    return _Handler
