"""대시보드 HTTP 보안 — channel 의 HttpSecurityPolicy 를 확장해 재사용한다.

IP/CIDR, 토큰(role), rate-limit, skill allowlist 로직은 robo_claw_channel 의 검증된
구현을 상속받고, 대시보드 엔드포인트에 맞는 권한 규칙만 재정의한다.
"""

from robo_claw_channel.security import HttpSecurityPolicy


class DashboardHttpSecurityPolicy(HttpSecurityPolicy):
    """대시보드 경로에 맞는 역할 요구사항을 정의한다.

    - GET  / (static), /health   : 공개
    - GET  /api/status, /api/map, /api/config, /api/logs: auth 활성 시 readonly 이상
    - GET  /api/localization   : readonly 이상 (LIO 상태/지도 목록 조회)
    - GET  /api/cooperation, /api/cooperation/export: readonly 이상 (조회 전용)
    - POST /api/chat             : control
    - POST /api/initial_pose     : control (AMCL/LIO 초기 위치 변경)
    - POST /api/localization/switch : control (SLAM 모드/지도 전환 — 로봇 상태 변경)

    경로 판정은 쿼리 문자열을 뗀 path 로 한다(핸들러가 쿼리를 붙여 넘기더라도
    `/api/logs?level=error` 가 공개 경로로 판정되어 인증을 우회하지 않도록 한다).
    """

    #: auth 활성 시 readonly 토큰이 필요한 GET 경로.
    READONLY_GET_PATHS = frozenset(
        {
            "/api/status",
            "/api/map",
            "/api/localization",
            "/api/config",
            "/api/logs",
            "/api/logs/export",
            "/api/cooperation",
            "/api/cooperation/export",
            "/api/scenarios",
            "/api/scenarios/status",
            "/api/scenarios/report",
        }
    )

    #: control 토큰이 필요한 POST 경로.
    CONTROL_POST_PATHS = frozenset(
        {
            "/api/chat",
            "/api/initial_pose",
            # SLAM 모드/지도 전환은 로봇의 위치추정 상태를 바꾸므로 control 로 둔다.
            "/api/localization/switch",
            "/api/scenarios/run",
            "/api/scenarios/cancel",
            # sync 는 사용자가 입력한 호스트로 대시보드가 직접 HTTP 요청을 보내는
            # 경로다. 읽기 권한으로 임의 목적지 조회를 허용하지 않도록 control 로 둔다.
            "/api/scenarios/sync",
        }
    )

    #: readonly 이상이면 되는 POST 경로. 로봇을 움직이지도, 임의 목적지로 요청을
    #: 보내지도 않는 조회성 동작만 여기에 둔다.
    READONLY_POST_PATHS: frozenset[str] = frozenset()

    def _required_role(self, *, method: str, path: str) -> str:
        if method == "POST" and path in self.CONTROL_POST_PATHS:
            return "control"
        if method == "POST" and path in self.READONLY_POST_PATHS:
            return "readonly" if self.auth_enabled else "public"
        if method == "GET" and path in self.READONLY_GET_PATHS:
            return "readonly" if self.auth_enabled else "public"
        return "public"
