"""대시보드에서 입력한 AI Config 서버 설정으로 시나리오를 동기화하는 동작 검증.

Docker 로 기동한 대시보드는 CLI 의 `~/.config/robo_claw_cli/config.yaml` 도, CLI 캐시
디렉터리도 볼 수 없다. 따라서 서버 URL/로봇/환경/디바이스 토큰을 브라우저에서 입력받아
요청마다 주입하는 경로를 가짜 HTTP 페처로 격리 검증한다.
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

USER_CONFIG = {
    "config_server_url": "http://10.0.0.5:30180",
    "robot_name": "former",
    "environment": "kon47",
    "device_token": "dev-token-xyz",
}


class FakeBackend:
    """시나리오 조회에는 쓰이지 않지만 runner 생성에 필요한 최소 백엔드."""

    def send_chat(self, message: str) -> dict[str, Any]:  # pragma: no cover - 미사용
        return {"success": True, "message": ""}


class RecordingFetcher:
    """요청 URL/헤더를 기록하고 고정 응답을 돌려주는 가짜 HTTP 페처."""

    def __init__(self, status: int = 200, payload: Any = None) -> None:
        self.status = status
        self.payload = payload if payload is not None else REMOTE_SCENARIO
        self.urls: list[str] = []
        self.headers: list[dict[str, str]] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        self.urls.append(url)
        self.headers.append(dict(headers))
        body = self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload)
        if isinstance(body, str):
            body = body.encode("utf-8")
        return self.status, body


def _runner(
    fetcher: RecordingFetcher, config: ScenarioConfig | None = None
) -> ScenarioRunnerService:
    return ScenarioRunnerService(FakeBackend(), config or ScenarioConfig(), http_fetcher=fetcher)


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """사용자 입력 설정은 ~/.robo_claw 캐시를 쓰므로 테스트 홈을 격리한다."""
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


class TestSyncScenario:
    def test_uses_user_provided_config(self, tmp_path):
        fetcher = RecordingFetcher()
        runner = _runner(fetcher, ScenarioConfig(cache_dir=str(tmp_path)))

        res = runner.sync_scenario(dict(USER_CONFIG))

        assert "status_code" not in res or res["status_code"] == 200
        assert res["source"] == "remote"
        assert res["scenario"]["name"] == "원격 시나리오"
        assert len(fetcher.urls) == 1
        assert fetcher.urls[0].startswith("http://10.0.0.5:30180/api/v1/scenarios/active?")
        assert "robot_name=former" in fetcher.urls[0]
        assert "environment=kon47" in fetcher.urls[0]
        assert fetcher.headers[0]["Authorization"] == "Bearer dev-token-xyz"

    def test_response_reports_effective_config_without_token(self, tmp_path):
        fetcher = RecordingFetcher()
        runner = _runner(fetcher, ScenarioConfig(cache_dir=str(tmp_path)))

        res = runner.sync_scenario(dict(USER_CONFIG))

        assert res["config"]["config_server_url"] == "http://10.0.0.5:30180"
        assert res["config"]["robot_name"] == "former"
        assert res["config"]["environment"] == "kon47"
        assert res["config"]["device_token_set"] is True
        assert "dev-token-xyz" not in json.dumps(res)

    def test_normalizes_trailing_slash_and_whitespace(self):
        fetcher = RecordingFetcher()
        runner = _runner(fetcher)

        res = runner.sync_scenario(
            {
                "config_server_url": "  http://10.0.0.5:30180/  ",
                "robot_name": " former ",
                "environment": "kon47 ",
            }
        )

        assert res["config"]["config_server_url"] == "http://10.0.0.5:30180"
        assert res["config"]["robot_name"] == "former"
        assert fetcher.urls[0].startswith("http://10.0.0.5:30180/api/v1/scenarios/active?")
        assert "Bearer" not in json.dumps(fetcher.headers[0])

    def test_rejects_unsupported_scheme(self):
        fetcher = RecordingFetcher()
        runner = _runner(fetcher)

        res = runner.sync_scenario(
            {
                "config_server_url": "file:///etc/passwd",
                "robot_name": "former",
                "environment": "kon47",
            }
        )

        assert res["status_code"] == 400
        assert "http" in res["error"]
        assert fetcher.urls == []

    def test_rejects_blank_url(self):
        runner = _runner(RecordingFetcher())

        res = runner.sync_scenario({"robot_name": "former", "environment": "kon47"})

        assert res["status_code"] == 400
        assert "URL" in res["error"]

    def test_rejects_missing_robot_name(self):
        runner = _runner(RecordingFetcher())

        res = runner.sync_scenario(
            {"config_server_url": "http://10.0.0.5:30180", "environment": "kon47"}
        )

        assert res["status_code"] == 400
        assert "로봇" in res["error"]

    def test_rejects_missing_environment(self):
        runner = _runner(RecordingFetcher())

        res = runner.sync_scenario(
            {"config_server_url": "http://10.0.0.5:30180", "robot_name": "former"}
        )

        assert res["status_code"] == 400
        assert "환경" in res["error"]

    def test_rejects_unsafe_characters(self):
        runner = _runner(RecordingFetcher())

        res = runner.sync_scenario(
            {
                "config_server_url": "http://10.0.0.5:30180",
                "robot_name": "former&environment=prod",
                "environment": "kon47",
            }
        )

        assert res["status_code"] == 400

    def test_writes_cache_under_user_environment(self, tmp_path, _isolated_home):
        fetcher = RecordingFetcher()
        runner = _runner(fetcher)

        res = runner.sync_scenario(dict(USER_CONFIG))

        cache_file = (
            Path(_isolated_home) / ".robo_claw" / "config_cache" / "former_kon47" / "scenario.json"
        )
        assert cache_file.is_file()
        assert res["config"]["cache_dir"] == str(cache_file.parent)

    def test_remote_failure_falls_back_to_cache_with_warning(self, _isolated_home):
        cache_dir = Path(_isolated_home) / ".robo_claw" / "config_cache" / "former_kon47"
        cache_dir.mkdir(parents=True)
        (cache_dir / "scenario.json").write_text(
            json.dumps(
                {
                    "name": "캐시 시나리오",
                    "test_cases": [
                        {"id": "tc_p", "name": "핑", "step": "S1", "type": "ping"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        runner = _runner(RecordingFetcher(status=500, payload=b"boom"))

        res = runner.sync_scenario(dict(USER_CONFIG))

        assert res["source"] == "cache"
        assert res["scenario"]["name"] == "캐시 시나리오"
        assert "500" in res["warning"]

    def test_auth_failure_points_at_device_token(self):
        runner = _runner(RecordingFetcher(status=401, payload=b'{"error":"Unauthorized"}'))

        res = runner.sync_scenario(dict(USER_CONFIG))

        assert res["source"] == "default"
        assert "401" in res["warning"]
        assert "디바이스 토큰" in res["warning"]

    def test_missing_active_scenario_is_explained(self):
        runner = _runner(RecordingFetcher(status=404, payload=b'{"error":"not found"}'))

        res = runner.sync_scenario(dict(USER_CONFIG))

        assert "활성화된 테스트 시나리오가 없습니다" in res["warning"]


class TestEffectiveConfig:
    def test_sync_config_is_reused_for_later_refresh(self, tmp_path):
        fetcher = RecordingFetcher()
        runner = _runner(fetcher, ScenarioConfig(cache_dir=str(tmp_path)))

        runner.sync_scenario(dict(USER_CONFIG))
        runner.get_scenario(refresh=True)

        assert len(fetcher.urls) == 2
        assert "robot_name=former" in fetcher.urls[1]

    def test_refresh_without_config_reports_missing_settings(self):
        runner = _runner(RecordingFetcher())

        res = runner.get_scenario(refresh=True)

        assert res["source"] == "default"
        assert "설정" in res["warning"]
        assert "URL" in res["warning"]

    def test_sync_clears_missing_settings_warning(self, tmp_path):
        fetcher = RecordingFetcher()
        runner = _runner(fetcher, ScenarioConfig(cache_dir=str(tmp_path)))
        assert runner.get_scenario(refresh=True)["warning"]

        res = runner.sync_scenario(dict(USER_CONFIG))

        assert res["warning"] == ""

    def test_startup_config_still_used_when_absent_from_user_input(self):
        fetcher = RecordingFetcher()
        runner = _runner(
            fetcher,
            ScenarioConfig(
                robot_name="cloid",
                environment="lab",
                config_server_url="http://config-server:8080",
            ),
        )

        res = runner.get_scenario(refresh=True)

        assert res["source"] == "remote"
        assert "robot_name=cloid" in fetcher.urls[0]
