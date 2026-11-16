"""시나리오 조회 우선순위(파일 → 원격 → 캐시 → 기본)와 경고 누적 규칙 고정 테스트.

``get_scenario`` 는 실패 사유를 응답의 ``warning`` 으로만 노출하고, 그 문구를 다음
조회까지 보관하는지 여부가 경로마다 다르다. 이 파일은 모듈 분리 전후로 같은 문구가
같은 시점에 노출되는지를 고정한다(characterization).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from robo_claw_dashboard.application.scenario_runner import (
    ScenarioConfig,
    ScenarioRunnerService,
)

pytestmark = pytest.mark.unit

REMOTE_SCENARIO = {
    "name": "원격 시나리오",
    "description": "AI Config Server 활성 시나리오",
    "test_cases": [
        {"id": "tc_ping", "name": "gRPC Ping", "step": "Step 1", "type": "ping"},
    ],
}

CACHED_SCENARIO = {
    "name": "캐시 시나리오",
    "description": "마지막 성공 캐시",
    "test_cases": [
        {"id": "tc_cached", "name": "캐시 핑", "step": "Step 1", "type": "ping"},
    ],
}

SCENARIO_RESPONSE_KEYS = {
    "scenario",
    "source",
    "robot_name",
    "environment",
    "config",
    "warning",
}
CONFIG_KEYS = {
    "config_server_url",
    "robot_name",
    "environment",
    "device_token_set",
    "cache_dir",
}


class FakeBackend:
    """시나리오 조회 경로만 검증하므로 채팅/텔레메트리는 최소값을 돌려준다."""

    def query_state(self) -> str | None:
        return None

    def latest_agent_status(self) -> dict[str, Any]:
        return {}

    def latest_telemetry(self) -> dict[str, Any]:
        return {}

    def latest_sensor_health(self) -> dict[str, Any]:
        return {}

    def send_chat(self, message: str) -> dict[str, Any]:
        return {"success": True, "message": ""}


class Fetcher:
    """고정 상태 코드/본문을 돌려주며 호출 URL 을 기록하는 가짜 HTTP 페처."""

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


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


def _remote_config(cache_dir: Path | str = "") -> ScenarioConfig:
    return ScenarioConfig(
        robot_name="cloid",
        environment="lab",
        config_server_url="http://config-server:8080",
        cache_dir=str(cache_dir) if cache_dir else "",
    )


def test_scenario_response_shape_is_stable():
    runner = ScenarioRunnerService(FakeBackend(), ScenarioConfig())

    res = runner.get_scenario()

    assert set(res) == SCENARIO_RESPONSE_KEYS
    assert set(res["config"]) == CONFIG_KEYS


def test_default_fallback_warning_is_not_persisted_between_reads():
    """설정 누락 안내는 응답 문구일 뿐, 다음 조회용 저장 경고가 아니다."""
    runner = ScenarioRunnerService(FakeBackend(), ScenarioConfig())

    first = runner.get_scenario()
    second = runner.get_scenario()

    assert first["source"] == "default"
    assert "URL" in first["warning"]
    assert second["warning"] == ""


def test_file_parse_failure_warning_is_cleared_by_remote_success(tmp_path):
    scenario_file = tmp_path / "broken.json"
    scenario_file.write_text("not json", encoding="utf-8")
    runner = ScenarioRunnerService(
        FakeBackend(),
        ScenarioConfig(scenario_file=str(scenario_file), **_config_fields()),
        http_fetcher=Fetcher(),
    )

    res = runner.get_scenario(refresh=True)

    assert res["source"] == "remote"
    assert res["warning"] == ""


def test_file_parse_failure_is_reported_when_no_remote_config(tmp_path):
    scenario_file = tmp_path / "broken.json"
    scenario_file.write_text("not json", encoding="utf-8")
    runner = ScenarioRunnerService(FakeBackend(), ScenarioConfig(scenario_file=str(scenario_file)))

    res = runner.get_scenario(refresh=True)

    assert res["source"] == "default"
    assert "파싱 오류" in res["warning"]


def test_remote_failure_warning_survives_cache_fallback_and_later_read(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "scenario.json").write_text(json.dumps(CACHED_SCENARIO), encoding="utf-8")
    runner = ScenarioRunnerService(
        FakeBackend(), _remote_config(cache_dir), http_fetcher=Fetcher(status=500, payload=b"boom")
    )

    first = runner.get_scenario(refresh=True)
    second = runner.get_scenario()

    assert first["source"] == "cache"
    assert "HTTP 500" in first["warning"]
    # 캐시로 대체했어도 실패 사유는 다음 조회까지 유지된다.
    assert second["warning"] == first["warning"]


def test_cached_scenario_is_used_without_warning_when_refresh_skipped(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "scenario.json").write_text(json.dumps(CACHED_SCENARIO), encoding="utf-8")
    fetcher = Fetcher()
    runner = ScenarioRunnerService(FakeBackend(), _remote_config(cache_dir), http_fetcher=fetcher)

    first = runner.get_scenario(refresh=True)
    second = runner.get_scenario(refresh=False)

    assert first["source"] == "remote"
    assert second["scenario"]["name"] == first["scenario"]["name"]
    assert len(fetcher.urls) == 1


def test_remote_success_is_cached_next_to_config(tmp_path):
    cache_dir = tmp_path / "cache"
    runner = ScenarioRunnerService(FakeBackend(), _remote_config(cache_dir), http_fetcher=Fetcher())

    runner.get_scenario(refresh=True)

    assert (cache_dir / "scenario.json").is_file()
    assert (
        json.loads((cache_dir / "scenario.json").read_text(encoding="utf-8"))["name"]
        == (REMOTE_SCENARIO["name"])
    )


def test_cache_write_failure_still_returns_remote_scenario(tmp_path):
    """캐시 디렉터리를 만들 수 없어도 원격 조회 결과는 그대로 노출한다."""
    blocking_file = tmp_path / "blocked"
    blocking_file.write_text("x", encoding="utf-8")
    runner = ScenarioRunnerService(
        FakeBackend(), _remote_config(blocking_file / "cache"), http_fetcher=Fetcher()
    )

    res = runner.get_scenario(refresh=True)

    assert res["source"] == "remote"
    assert res["warning"] == ""
    assert blocking_file.is_file()


def test_remote_parse_failure_falls_back_to_cache_with_reason(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "scenario.json").write_text(json.dumps(CACHED_SCENARIO), encoding="utf-8")
    runner = ScenarioRunnerService(
        FakeBackend(),
        _remote_config(cache_dir),
        http_fetcher=Fetcher(status=200, payload=b'{"name": 1, "test_cases": "nope"}'),
    )

    res = runner.get_scenario(refresh=True)

    assert res["source"] == "cache"
    assert "원격 시나리오 파싱 실패" in res["warning"]


def _config_fields() -> dict[str, str]:
    return {
        "robot_name": "cloid",
        "environment": "lab",
        "config_server_url": "http://config-server:8080",
    }
