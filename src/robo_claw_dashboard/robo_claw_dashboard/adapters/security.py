"""대시보드 HTTP 보안 — channel 의 HttpSecurityPolicy 를 확장해 재사용한다.

IP/CIDR, 토큰(role), rate-limit, skill allowlist 로직은 robo_claw_channel 의 검증된
구현을 상속받고, 대시보드 엔드포인트에 맞는 권한 규칙만 재정의한다.
"""

from robo_claw_channel.security import HttpSecurityPolicy


class DashboardHttpSecurityPolicy(HttpSecurityPolicy):
    """대시보드 경로에 맞는 역할 요구사항을 정의한다.

    - GET  / (static), /health   : 공개
    - GET  /api/status, /api/map, /api/config, /api/logs: auth 활성 시 readonly 이상
    - POST /api/chat             : control
    - POST /api/initial_pose     : control (AMCL 초기 위치 변경)

    경로 판정은 쿼리 문자열을 뗀 path 로 한다(핸들러가 쿼리를 붙여 넘기더라도
    `/api/logs?level=error` 가 공개 경로로 판정되어 인증을 우회하지 않도록 한다).
    """

    #: auth 활성 시 readonly 토큰이 필요한 GET 경로.
    READONLY_GET_PATHS = frozenset(
        {"/api/status", "/api/map", "/api/config", "/api/logs", "/api/logs/export"}
    )

    def _required_role(self, *, method: str, path: str) -> str:
        if method == "POST" and path in ("/api/chat", "/api/initial_pose"):
            return "control"
        if method == "GET" and path in self.READONLY_GET_PATHS:
            return "readonly" if self.auth_enabled else "public"
        return "public"
