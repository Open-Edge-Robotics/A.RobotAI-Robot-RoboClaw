"""대시보드 HTTP 보안 — channel 의 HttpSecurityPolicy 를 확장해 재사용한다.

IP/CIDR, 토큰(role), rate-limit, skill allowlist 로직은 robo_claw_channel 의 검증된
구현을 상속받고, 대시보드 엔드포인트에 맞는 권한 규칙만 재정의한다.
"""

from robo_claw_channel.security import HttpSecurityPolicy


class DashboardHttpSecurityPolicy(HttpSecurityPolicy):
    """대시보드 경로에 맞는 역할 요구사항을 정의한다.

    - GET  / (static), /health  : 공개
    - GET  /api/status          : auth 활성 시 readonly 이상
    - POST /api/chat            : control
    """

    def _required_role(self, *, method: str, path: str) -> str:
        if method == "POST" and path == "/api/chat":
            return "control"
        if method == "GET" and path == "/api/status":
            return "readonly" if self.auth_enabled else "public"
        return "public"
