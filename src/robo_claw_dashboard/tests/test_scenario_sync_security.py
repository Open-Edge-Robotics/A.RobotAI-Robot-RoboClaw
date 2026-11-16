"""대시보드 시나리오 동기화 경로의 SSRF·권한 회귀 테스트.

`/api/scenarios/sync` 는 사용자가 입력한 호스트로 대시보드가 직접 HTTP 요청을
보내는 유일한 경로다. 따라서 (1) 권한이 control 이상이어야 하고,
(2) 허용 목록(allowed_config_hosts)이 설정되면 그 밖의 호스트는 거부하며,
(3) 클라우드 메타데이터·링크로컬 주소는 설정과 무관하게 항상 거부해야 한다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from robo_claw_dashboard.adapters.security import DashboardHttpSecurityPolicy
from robo_claw_dashboard.application.scenario_runner import (
    ScenarioConfig,
    ScenarioRunnerService,
    parse_user_server_config,
)

pytestmark = pytest.mark.unit

REMOTE_SCENARIO = {
    "name": "원격 시나리오",
    "description": "AI Config Server 활성 시나리오",
    "test_cases": [
        {"id": "tc_ping", "name": "gRPC Ping", "step": "Step 1", "type": "ping"},
    ],
}


class FakeBackend:
    def send_chat(self, message: str) -> dict[str, Any]:  # pragma: no cover - 미사용
        return {"success": True, "message": ""}


class RecordingFetcher:
    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else REMOTE_SCENARIO
        self.urls: list[str] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        self.urls.append(url)
        body = self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload)
        if isinstance(body, str):
            body = body.encode("utf-8")
        return self.status, body


def _runner(fetcher: RecordingFetcher, config: ScenarioConfig) -> ScenarioRunnerService:
    return ScenarioRunnerService(FakeBackend(), config, http_fetcher=fetcher)


def _user_config(url: str) -> dict[str, str]:
    return {
        "config_server_url": url,
        "robot_name": "former",
        "environment": "kon47",
        "device_token": "dev-token-xyz",
    }


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


# ---------------------------------------------------------------------------
# 권한
# ---------------------------------------------------------------------------


def test_scenario_sync_requires_control_role():
    policy = DashboardHttpSecurityPolicy(
        readonly_token="ro",
        control_token="ctl",
        allowed_cidrs=["127.0.0.1/32"],
        rate_limit_per_minute=60,
        allowed_skills=[],
        blocked_skills=[],
    )

    assert policy._required_role(method="POST", path="/api/scenarios/sync") == "control"


# ---------------------------------------------------------------------------
# 허용 목록
# ---------------------------------------------------------------------------


def test_allowlisted_host_is_accepted():
    fetcher = RecordingFetcher()
    runner = _runner(
        fetcher,
        ScenarioConfig(allowed_config_hosts=frozenset({"10.0.0.5:30180"})),
    )

    res = runner.sync_scenario(_user_config("http://10.0.0.5:30180"))

    assert res.get("status_code", 200) == 200
    assert len(fetcher.urls) == 1


def test_non_allowlisted_host_is_rejected_without_any_request():
    fetcher = RecordingFetcher()
    runner = _runner(
        fetcher,
        ScenarioConfig(allowed_config_hosts=frozenset({"10.0.0.5:30180"})),
    )

    res = runner.sync_scenario(_user_config("http://192.168.0.99:8080"))

    assert res["status_code"] == 400
    assert "허용" in res["error"]
    assert fetcher.urls == []


def test_startup_config_host_is_implicitly_allowed():
    fetcher = RecordingFetcher()
    runner = _runner(fetcher, ScenarioConfig(config_server_url="http://10.0.0.5:30180"))

    res = runner.sync_scenario(_user_config("http://10.0.0.5:30180"))

    assert res.get("status_code", 200) == 200
    assert len(fetcher.urls) == 1


def test_default_port_is_normalized_for_allowlist_matching():
    values, error = parse_user_server_config(
        _user_config("https://config.example.com"),
        allowed_hosts=frozenset({"config.example.com:443"}),
    )

    assert error == ""
    assert values["config_server_url"] == "https://config.example.com"


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254",
        "http://169.254.169.254:80",
        "http://metadata.google.internal",
        "http://[fd00:ec2::254]",
    ],
)
def test_link_local_and_metadata_hosts_are_always_rejected(url):
    values, error = parse_user_server_config(
        _user_config(url),
        allowed_hosts=frozenset({"169.254.169.254", "metadata.google.internal", "fd00:ec2::254"}),
    )

    assert values == {}
    assert "링크로컬" in error or "허용" in error


def test_path_and_query_are_still_rejected():
    _, error = parse_user_server_config(_user_config("http://10.0.0.5:30180/admin"))

    assert error != ""


# ---------------------------------------------------------------------------
# 호스트 정규화 (허용 목록 입력 형식)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://10.0.0.5:30180", "10.0.0.5:30180"),
        ("10.0.0.5:30180", "10.0.0.5:30180"),
        ("https://config.example.com", "config.example.com:443"),
        ("config.example.com", "config.example.com"),
        ("  http://127.0.0.1  ", "127.0.0.1:80"),
        ("", ""),
    ],
)
def test_normalize_host_entry_accepts_url_or_host_forms(raw, expected):
    from robo_claw_dashboard.application.scenario_runner import normalize_host_entry

    assert normalize_host_entry(raw) == expected
