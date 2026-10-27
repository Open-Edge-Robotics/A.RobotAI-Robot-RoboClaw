"""노출 설정 점검 unit 테스트 — 대시보드가 로컬/원격 어느 쪽으로 열리는지 판정한다."""

import pytest
from robo_claw_dashboard.domain.exposure import (
    ERROR,
    WARNING,
    exposure_findings,
    is_loopback_host,
    parse_allowed_cidrs,
)

pytestmark = pytest.mark.unit

LOOPBACK_ONLY = ["127.0.0.1/32", "::1/128"]
LAN = ["127.0.0.1/32", "192.168.0.0/24"]


def _codes(**kw):
    return [finding.code for finding in exposure_findings(**kw)]


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.2"])
def test_loopback_hosts_are_detected(host):
    assert is_loopback_host(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "", "192.168.0.10", "::", "robot.local"])
def test_wildcard_and_lan_hosts_are_not_loopback(host):
    assert is_loopback_host(host) is False


def test_default_loopback_setup_reports_nothing():
    assert (
        exposure_findings(host="127.0.0.1", allowed_cidrs=LOOPBACK_ONLY, auth_enabled=False) == []
    )


def test_lan_cidrs_with_loopback_bind_blocks_remote_access():
    findings = exposure_findings(host="127.0.0.1", allowed_cidrs=LAN, auth_enabled=True)

    assert [f.code for f in findings] == ["loopback_bind"]
    assert findings[0].severity == ERROR
    assert "0.0.0.0" in findings[0].message


def test_wildcard_bind_with_loopback_cidrs_rejects_remote_clients():
    findings = exposure_findings(host="0.0.0.0", allowed_cidrs=LOOPBACK_ONLY, auth_enabled=True)

    assert [f.code for f in findings] == ["remote_bind_loopback_cidrs"]
    assert findings[0].severity == ERROR


def test_wildcard_bind_without_token_is_reported_as_unauthorized_control():
    codes = _codes(host="0.0.0.0", allowed_cidrs=LAN, auth_enabled=False)

    assert codes == ["remote_bind_no_auth"]


def test_wildcard_bind_with_lan_cidrs_and_token_is_clean():
    assert exposure_findings(host="0.0.0.0", allowed_cidrs=LAN, auth_enabled=True) == []


def test_empty_cidrs_on_remote_bind_warns_about_open_access():
    findings = exposure_findings(host="0.0.0.0", allowed_cidrs=[], auth_enabled=True)

    assert [f.code for f in findings] == ["remote_bind_open_cidrs"]
    assert findings[0].severity == WARNING


def test_invalid_cidr_entries_are_ignored_when_deciding_loopback_only():
    findings = exposure_findings(
        host="0.0.0.0", allowed_cidrs=["not-a-cidr", "10.0.0.0/8"], auth_enabled=True
    )

    assert findings == []


def test_remote_bind_without_auth_and_loopback_cidrs_reports_both():
    codes = _codes(host="192.168.0.10", allowed_cidrs=LOOPBACK_ONLY, auth_enabled=False)

    assert codes == ["remote_bind_loopback_cidrs", "remote_bind_no_auth"]


def test_parse_allowed_cidrs_accepts_json_array():
    cidrs, error = parse_allowed_cidrs('["127.0.0.1/32","192.168.0.0/24"]', fallback=LOOPBACK_ONLY)

    assert error is None
    assert cidrs == ["127.0.0.1/32", "192.168.0.0/24"]


def test_parse_allowed_cidrs_returns_empty_for_blank_value():
    cidrs, error = parse_allowed_cidrs("  ", fallback=LOOPBACK_ONLY)

    assert (cidrs, error) == ([], None)


def test_malformed_cidr_json_falls_back_to_loopback_instead_of_allow_all():
    cidrs, error = parse_allowed_cidrs("['192.168.0.0/24']", fallback=LOOPBACK_ONLY)

    assert cidrs == LOOPBACK_ONLY
    assert error is not None and "JSON" in error


def test_non_array_cidr_json_falls_back_to_loopback():
    cidrs, error = parse_allowed_cidrs("192.168.0.0/24", fallback=LOOPBACK_ONLY)

    assert cidrs == LOOPBACK_ONLY
    assert error is not None
