"""대시보드 노출 점검 — 바인드 주소·CIDR·인증 조합을 순수 함수로 판정한다.

ROS2/네트워크 import 금지. 대시보드가 `127.0.0.1` 에만 바인딩되어 원격 접속이
불가능하거나, 반대로 인증 없이 LAN 에 열리는 설정을 기동 시점에 드러낸다.
판정 결과는 (severity, code, message) 형태의 finding 목록이며, 노드가 이를 로그로 남긴다.
"""

import ipaddress
import json
from dataclasses import dataclass

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]", "ip6-localhost"})

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class ExposureFinding:
    """노출 설정 점검 결과 한 건."""

    severity: str
    code: str
    message: str


def is_loopback_host(host: str) -> bool:
    """바인드 주소가 루프백 전용인지 판정한다.

    빈 문자열은 모든 인터페이스 바인드와 같으므로 루프백으로 보지 않는다.
    """
    normalized = (host or "").strip().lower()
    if not normalized:
        return False
    if normalized in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(normalized.strip("[]")).is_loopback
    except ValueError:
        return False


def parse_allowed_cidrs(raw: str, *, fallback: list[str]) -> tuple[list[str], str | None]:
    """`http_allowed_cidrs_json` 문자열을 CIDR 목록으로 해석한다.

    보안 정책은 빈 목록을 "모든 IP 허용" 으로 해석하므로, 형식이 깨진 값은
    전체 허용으로 이어지지 않도록 fallback(기본 루프백)으로 되돌리고 오류를 알린다.

    Returns:
        (해석된 CIDR 목록, 형식 오류 메시지 또는 None)
    """
    text = (raw or "").strip()
    if not text:
        return [], None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return list(fallback), (
            f"http_allowed_cidrs_json 값이 올바른 JSON 배열이 아닙니다: {text[:80]!r} — "
            "이대로 두면 모든 IP 가 허용되므로 기본 루프백 제한을 적용합니다. "
            '예: ["127.0.0.1/32","192.168.0.0/24"] (작은따옴표는 사용할 수 없습니다)'
        )
    if not isinstance(parsed, list):
        return list(fallback), (
            f"http_allowed_cidrs_json 값이 JSON 배열이 아닙니다: {text[:80]!r} — "
            "기본 루프백 제한을 적용합니다."
        )
    return [str(item) for item in parsed], None


def _loopback_only(allowed_cidrs: list[str]) -> bool:
    """허용 CIDR 이 모두 루프백이면 True. 잘못된 항목은 판정에서 제외한다."""
    networks = []
    for item in allowed_cidrs:
        try:
            networks.append(ipaddress.ip_network(str(item).strip(), strict=False))
        except ValueError:
            continue
    if not networks:
        # 비어 있으면 보안 정책은 "전체 허용" 으로 동작한다.
        return False
    return all(network.is_loopback for network in networks)


def exposure_findings(
    *,
    host: str,
    allowed_cidrs: list[str],
    auth_enabled: bool,
) -> list[ExposureFinding]:
    """대시보드 노출 설정의 위험 요소를 찾아 반환한다.

    Args:
        host: `dashboard_host` 파라미터 값.
        allowed_cidrs: `http_allowed_cidrs_json` 로 해석된 CIDR 목록.
        auth_enabled: HTTP 토큰이 하나라도 설정되어 있는지 여부.
    """
    findings: list[ExposureFinding] = []
    loopback_bind = is_loopback_host(host)
    cidrs_allow_remote = not _loopback_only(allowed_cidrs)

    if loopback_bind and cidrs_allow_remote:
        findings.append(
            ExposureFinding(
                severity=ERROR,
                code="loopback_bind",
                message=(
                    f"dashboard_host={host} 는 루프백 전용 바인드라 원격 접속이 불가능합니다. "
                    "원격 접속이 필요하면 dashboard_host:=0.0.0.0 (또는 로봇 LAN IP) 로 실행하세요."
                ),
            )
        )

    if not loopback_bind:
        if _loopback_only(allowed_cidrs):
            findings.append(
                ExposureFinding(
                    severity=ERROR,
                    code="remote_bind_loopback_cidrs",
                    message=(
                        f"dashboard_host={host} 로 외부에 바인딩했지만 http_allowed_cidrs_json 이 "
                        "루프백 전용이라 원격 클라이언트는 전부 403 으로 거부됩니다. "
                        '예: http_allowed_cidrs_json:=\'["127.0.0.1/32","192.168.0.0/24"]\''
                    ),
                )
            )
        if not allowed_cidrs:
            findings.append(
                ExposureFinding(
                    severity=WARNING,
                    code="remote_bind_open_cidrs",
                    message=(
                        "http_allowed_cidrs_json 이 비어 있어 모든 IP 를 허용합니다. "
                        "접속 가능한 대역을 CIDR 로 제한하세요."
                    ),
                )
            )
        if not auth_enabled:
            findings.append(
                ExposureFinding(
                    severity=ERROR,
                    code="remote_bind_no_auth",
                    message=(
                        f"dashboard_host={host} 로 외부에 바인딩했지만 HTTP 토큰이 비어 있어 "
                        "네트워크에 닿는 누구나 /api/chat 으로 에이전트를 실행할 수 있습니다. "
                        "http_readonly_token / http_control_token 을 설정하세요."
                    ),
                )
            )

    return findings
