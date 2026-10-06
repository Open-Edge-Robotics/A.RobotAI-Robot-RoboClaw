"""HTTP 채널 보안 정책.

IP 화이트리스트, 토큰 기반 역할(public/readonly/control) 인가, 슬라이딩 윈도우
레이트 리밋, 스킬 허용/차단 목록을 담당한다.
"""

import ast
import ipaddress
import json
import threading
import time
from collections import deque
from dataclasses import dataclass
from secrets import compare_digest
from typing import Any


def parse_string_list(raw: str, *, parameter_name: str) -> list[str]:
    """JSON 문자열 리스트 파라미터를 파싱해 비어있지 않은 문자열 목록으로 반환."""
    if not raw:
        return []
    if not isinstance(raw, str):
        raw = str(raw)
    raw = raw.strip()
    if not raw or raw in ('""', "''"):
        return []
    # ros2 launch 의 key:=value 처리가 distro 마다 다르다.
    #   jazzy(호스트): value 를 YAML 로 평가 → yamlStringArg 가 감싼 작은따옴표가
    #     벗겨져 깨끗한 JSON 문자열이 도달한다.
    #   humble(Docker): value 를 raw 문자열 그대로 전달 → 작은따옴표가 리터럴로
    #     남아 '["a","b"]' 처럼 도달해 json.loads 가 char 0 에서 실패한다.
    # 한 겹의 짝맞는 인용부호를 벗기고, 그래도 안 되면 str(list)/str(dict) 로
    # 문자열화된 Python repr(ast.literal_eval) 도 흡수한다.
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        inner = raw[1:-1].strip()
        if inner and inner[0] in "[{":
            raw = inner
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = ast.literal_eval(raw)
        except (ValueError, SyntaxError) as exc:
            raise ValueError(
                f"{parameter_name} 파라미터는 JSON 문자열 리스트여야 합니다: {exc}"
            ) from exc
    if not isinstance(data, list):
        raise ValueError(f"{parameter_name} 파라미터는 JSON 리스트여야 합니다.")
    normalized: list[str] = []
    for item in data:
        if not isinstance(item, str):
            raise ValueError(f"{parameter_name} 파라미터는 문자열 리스트여야 합니다.")
        value = item.strip()
        if not value:
            raise ValueError(f"{parameter_name} 파라미터에 빈 문자열을 넣을 수 없습니다.")
        normalized.append(value)
    return normalized


class SlidingWindowRateLimiter:
    def __init__(self, max_requests: int, window_sec: float = 60.0) -> None:
        self._max_requests = max_requests
        self._window_sec = window_sec
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    @property
    def max_requests(self) -> int:
        return self._max_requests

    def allow(self, key: str, *, now: float | None = None) -> bool:
        if self._max_requests <= 0:
            return True
        current = time.monotonic() if now is None else now
        cutoff = current - self._window_sec
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self._max_requests:
                return False
            events.append(current)
            return True


@dataclass(frozen=True)
class HttpAccessResult:
    allowed: bool
    status_code: int
    payload: dict[str, Any]
    role: str = "public"


class HttpSecurityPolicy:
    def __init__(
        self,
        *,
        readonly_token: str,
        control_token: str,
        allowed_cidrs: list[str],
        rate_limit_per_minute: int,
        allowed_skills: list[str],
        blocked_skills: list[str],
    ) -> None:
        self._readonly_token = readonly_token.strip()
        self._control_token = control_token.strip()
        self._allowed_networks = [
            ipaddress.ip_network(item, strict=False) for item in allowed_cidrs
        ]
        self._rate_limiter = SlidingWindowRateLimiter(rate_limit_per_minute)
        self._allowed_skills = {item.strip() for item in allowed_skills}
        self._blocked_skills = {item.strip() for item in blocked_skills}

    @property
    def auth_enabled(self) -> bool:
        return bool(self._readonly_token or self._control_token)

    @property
    def allowed_cidrs(self) -> list[str]:
        return [str(network) for network in self._allowed_networks]

    @property
    def rate_limit_per_minute(self) -> int:
        return self._rate_limiter.max_requests

    def skill_controls(self) -> dict[str, list[str]]:
        return {
            "allowed_skills": sorted(self._allowed_skills),
            "blocked_skills": sorted(self._blocked_skills),
        }

    def is_skill_allowed(self, skill_name: str) -> str | None:
        normalized = skill_name.strip()
        if normalized in self._blocked_skills:
            return f"HTTP에서 허용되지 않은 위험 스킬입니다: {normalized}"
        if self._allowed_skills and normalized not in self._allowed_skills:
            return f"HTTP skill allowlist에 없는 스킬입니다: {normalized}"
        return None

    def authorize(
        self,
        *,
        client_ip: str,
        method: str,
        path: str,
        headers: Any,
    ) -> HttpAccessResult:
        if not self._is_ip_allowed(client_ip):
            return HttpAccessResult(
                allowed=False,
                status_code=403,
                payload={"error": f"허용되지 않은 클라이언트 IP입니다: {client_ip}"},
            )

        role = self._resolve_role(self._extract_token(headers))
        required_role = self._required_role(method=method, path=path)
        if not self._has_required_role(role=role, required_role=required_role):
            return HttpAccessResult(
                allowed=False,
                status_code=401,
                payload={"error": f"{path} 요청에는 {required_role} 권한 토큰이 필요합니다."},
                role=role,
            )

        identity = self._extract_token(headers) or client_ip
        if not self._rate_limiter.allow(identity):
            return HttpAccessResult(
                allowed=False,
                status_code=429,
                payload={"error": "요청 수 제한을 초과했습니다. 잠시 후 다시 시도하세요."},
                role=role,
            )

        return HttpAccessResult(
            allowed=True, status_code=200, payload={"ok": True}, role=role
        )

    def _extract_token(self, headers: Any) -> str:
        auth_header = headers.get("Authorization", "").strip()
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()
        return headers.get("X-RoboClaw-Token", "").strip()

    def _resolve_role(self, token: str) -> str:
        if self._control_token and token and compare_digest(token, self._control_token):
            return "control"
        if self._readonly_token and token and compare_digest(token, self._readonly_token):
            return "readonly"
        if not self.auth_enabled:
            return "control"
        return "public"

    def _required_role(self, *, method: str, path: str) -> str:
        if method == "POST" and path in {"/task", "/skill"}:
            return "control"
        if method == "GET" and path == "/status":
            return "readonly" if self.auth_enabled else "public"
        return "public"

    def _has_required_role(self, *, role: str, required_role: str) -> bool:
        order = {"public": 0, "readonly": 1, "control": 2}
        return order.get(role, 0) >= order.get(required_role, 0)

    def _is_ip_allowed(self, client_ip: str) -> bool:
        if not self._allowed_networks:
            return True
        try:
            address = ipaddress.ip_address(client_ip)
        except ValueError:
            return False
        return any(address in network for network in self._allowed_networks)
