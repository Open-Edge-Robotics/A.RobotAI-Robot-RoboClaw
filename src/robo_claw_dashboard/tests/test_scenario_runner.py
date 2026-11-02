"""시나리오 테스트 실행기(ScenarioRunnerService) 단위 및 컴포넌트 테스트.

rclpy 의존성 없이 가짜 백엔드와 HTTP 페처를 주입하여 격리 검증한다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from robo_claw_dashboard.application.scenario_runner import (
    ScenarioConfig,
    ScenarioRunnerService,
    resolve_cli_config_defaults,
)
from robo_claw_dashboard.domain.scenario_models import (
    ScenarioTestCase,
    get_default_scenario,
)

pytestmark = pytest.mark.unit


class FakeDashboardBackend:
    """테스트용 가짜 백엔드."""

    def __init__(
        self,
        *,
        state: str | None = '{"robot_name": "butler"}',
        agent_status: dict[str, Any] | None = None,
        telemetry: dict[str, Any] | None = None,
        sensor_health: dict[str, Any] | None = None,
        chat_response: dict[str, Any] | None = None,
    ) -> None:
        self._state = state
        self._agent_status = agent_status if agent_status is not None else {"state_code": 1}
        self._telemetry = (
            telemetry
            if telemetry is not None
            else {"battery": {"percentage": "85.0%", "voltage": "24.5V"}}
        )
        self._sensor_health = (
            sensor_health if sensor_health is not None else {"camera": True, "map": True}
        )
        self._chat_response = (
            chat_response
            if chat_response is not None
            else {"success": True, "message": "성공적으로 수행했습니다."}
        )
        self.chat_history: list[str] = []

    def query_state(self) -> str | None:
        return self._state

    def latest_agent_status(self) -> dict[str, Any]:
        return dict(self._agent_status)

    def latest_telemetry(self) -> dict[str, Any]:
        return dict(self._telemetry)

    def latest_sensor_health(self) -> dict[str, Any]:
        return dict(self._sensor_health)

    def list_peers(self) -> list[dict[str, Any]]:
        return []

    def send_chat(self, message: str) -> dict[str, Any]:
        self.chat_history.append(message)
        return dict(self._chat_response)


class TestScenarioConfig:
    def test_cache_dir_auto_computation(self):
        cfg = ScenarioConfig(robot_name="butler", environment="office")
        assert "config_cache/butler_office" in cfg.cache_dir

    def test_custom_cache_dir_preserved(self):
        cfg = ScenarioConfig(
            robot_name="butler",
            environment="office",
            cache_dir="/tmp/custom_cache",
        )
        assert cfg.cache_dir == "/tmp/custom_cache"


class TestResolveCliConfigDefaults:
    def test_reads_yaml_file(self, tmp_path):
        conf_file = tmp_path / "config.yaml"
        conf_file.write_text(
            """
device_token: "my-secret-token"
server:
  host: "192.168.1.100"
  port: 9000
  allow_insecure_http: true
""",
            encoding="utf-8",
        )
        res = resolve_cli_config_defaults(str(conf_file))
        assert res["device_token"] == "my-secret-token"
        assert res["host"] == "192.168.1.100"
        assert res["port"] == "9000"
        assert res["config_server_url"] == "http://192.168.1.100:9000"

    def test_env_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CONFIG_URL", "https://api.roboclaw.com")
        monkeypatch.setenv("DEVICE_TOKEN", "env-tok")
        res = resolve_cli_config_defaults(str(tmp_path / "nonexistent.yaml"))
        assert res["config_server_url"] == "https://api.roboclaw.com"
        assert res["device_token"] == "env-tok"


class TestGetScenario:
    def test_load_default_when_no_config(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend, ScenarioConfig())
        data = runner.get_scenario()
        assert data["source"] == "default"
        assert len(data["scenario"]["test_cases"]) == 11
        assert data["warning"] == ""

    def test_load_from_scenario_file_valid(self, tmp_path):
        backend = FakeDashboardBackend()
        sc_file = tmp_path / "custom_sc.json"
        sc_data = {
            "name": "파일 시나리오",
            "description": "설명",
            "test_cases": [{"id": "tc1", "name": "핑", "step": "S1", "type": "ping"}],
        }
        sc_file.write_text(json.dumps(sc_data), encoding="utf-8")

        cfg = ScenarioConfig(scenario_file=str(sc_file))
        runner = ScenarioRunnerService(backend, cfg)
        data = runner.get_scenario()
        assert data["source"] == "file"
        assert data["scenario"]["name"] == "파일 시나리오"
        assert len(data["scenario"]["test_cases"]) == 1

    def test_load_from_scenario_file_invalid_falls_back(self, tmp_path):
        backend = FakeDashboardBackend()
        sc_file = tmp_path / "bad.json"
        sc_file.write_text("invalid json", encoding="utf-8")

        cfg = ScenarioConfig(scenario_file=str(sc_file))
        runner = ScenarioRunnerService(backend, cfg)
        data = runner.get_scenario()
        assert data["source"] == "default"
        assert "파싱 오류" in data["warning"]

    def test_load_from_remote_server_success(self, tmp_path):
        backend = FakeDashboardBackend()
        remote_data = {
            "name": "원격 시나리오",
            "description": "원격",
            "test_cases": [{"id": "tc_rem", "name": "원격 케이스", "step": "S1", "type": "ping"}],
        }
        fetched_urls: list[str] = []
        fetched_headers: list[dict[str, str]] = []

        def fake_fetcher(url: str, headers: dict[str, str], timeout: float):
            fetched_urls.append(url)
            fetched_headers.append(headers)
            return 200, json.dumps(remote_data).encode("utf-8")

        cache_dir = tmp_path / "cache"
        cfg = ScenarioConfig(
            robot_name="cloid",
            environment="lab",
            config_server_url="http://config-server:8080",
            device_token="dev-token-xyz",
            cache_dir=str(cache_dir),
        )
        runner = ScenarioRunnerService(backend, cfg, http_fetcher=fake_fetcher)
        data = runner.get_scenario()

        assert data["source"] == "remote"
        assert data["scenario"]["name"] == "원격 시나리오"
        assert len(fetched_urls) == 1
        assert "robot_name=cloid" in fetched_urls[0]
        assert fetched_headers[0]["Authorization"] == "Bearer dev-token-xyz"
        # 로컬 캐시 파일 생성 확인
        assert (cache_dir / "scenario.json").is_file()

    def test_remote_server_failure_falls_back_to_cache(self, tmp_path):
        backend = FakeDashboardBackend()
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        cached_data = {
            "name": "캐시된 시나리오",
            "description": "캐시",
            "test_cases": [{"id": "tc_c", "name": "캐시 케이스", "step": "S1", "type": "ping"}],
        }
        (cache_dir / "scenario.json").write_text(json.dumps(cached_data), encoding="utf-8")

        def failing_fetcher(url: str, headers: dict[str, str], timeout: float):
            return 500, b"Server error"

        cfg = ScenarioConfig(
            robot_name="cloid",
            environment="lab",
            config_server_url="http://config-server:8080",
            cache_dir=str(cache_dir),
        )
        runner = ScenarioRunnerService(backend, cfg, http_fetcher=failing_fetcher)
        data = runner.get_scenario()

        assert data["source"] == "cache"
        assert data["scenario"]["name"] == "캐시된 시나리오"
        assert "HTTP 500" in data["warning"]


class TestStartAndCancelRun:
    def test_start_run_sync_mode(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend, ScenarioConfig(robot_name="test_bot"))
        res = runner.start_run(
            {"run_navigation": False, "run_manipulation": False}, async_mode=False
        )

        assert res["accepted"] is True
        assert res["status_code"] == 200
        assert res["status"] in ("COMPLETED", "FAILED")

        st = runner.status()
        assert st["status"] in ("COMPLETED", "FAILED")
        assert st["progress_percent"] == 100
        assert st["has_report"] is True

    def test_start_run_already_running_rejected(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend)
        # simulate running state
        runner._state = "RUNNING"
        res = runner.start_run()
        assert res["accepted"] is False
        assert res["status_code"] == 409

    def test_start_run_with_custom_scenario(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend)
        custom_sc = {
            "name": "커스텀 1개",
            "test_cases": [{"id": "tc_ping", "name": "핑", "step": "1", "type": "ping"}],
        }
        res = runner.start_run({"scenario": custom_sc}, async_mode=False)
        assert res["accepted"] is True
        assert res["status"] == "COMPLETED"
        assert runner.status()["scenario_name"] == "커스텀 1개"

    def test_start_run_with_invalid_scenario_rejected(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend)
        res = runner.start_run({"scenario": "bad json"}, async_mode=False)
        assert res["accepted"] is False
        assert res["status_code"] == 400

    def test_run_options_and_enabled_ids(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend)
        # Run navigation False -> navigation cases disabled
        runner.start_run(
            {
                "run_navigation": False,
                "run_manipulation": False,
                "enabled_ids": ["tc_ping", "tc_guardrail"],
            },
            async_mode=False,
        )
        st = runner.status()
        step_dict = {s["id"]: s for s in st["steps"]}
        assert step_dict["tc_ping"]["status"] == "PASS"
        # tc_guardrail was requested in enabled_ids but run_navigation was False -> safety gate forced SKIP
        assert step_dict["tc_guardrail"]["status"] == "SKIP"

    def test_cancel_run_when_not_running(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend)
        res = runner.cancel_run()
        assert res["cancelled"] is False
        assert "실행 중인 시나리오가 없습니다" in res["error"]

    def test_cancel_run_while_running(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend)
        # Set up a fake running state
        sc = get_default_scenario()
        runner._state = "RUNNING"
        runner._run_scenario_name = sc.name
        runner._run_id = "run_test_cancel"
        runner._step_results = [
            ScenarioTestCase.from_dict(tc.to_dict()).to_dict() for tc in sc.test_cases
        ]
        # convert to StepResult
        from robo_claw_dashboard.domain.scenario_models import StepResult

        runner._step_results = [
            StepResult(id=tc.id, step=tc.step, name=tc.name, type=tc.type, status="PENDING")
            for tc in sc.test_cases
        ]
        runner._step_results[0].status = "PASS"

        res = runner.cancel_run()
        assert res["cancelled"] is True
        assert res["status"] == "CANCELLED"
        assert runner.status()["status"] == "CANCELLED"
        # remaining steps should be SKIP
        assert runner.status()["steps"][1]["status"] == "SKIP"
        assert "사용자 요청으로 중단됨" in runner.status()["steps"][1]["detail"]


class TestReport:
    def test_get_report_404_when_no_report(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend)
        res = runner.get_report(fmt="md")
        assert res["found"] is False
        assert res["status_code"] == 404

    def test_get_report_md_and_json(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(
            backend, ScenarioConfig(robot_name="butler", environment="lab")
        )
        runner.start_run({"run_navigation": False, "run_manipulation": False}, async_mode=False)

        md_rep = runner.get_report(fmt="md")
        assert md_rep["found"] is True
        assert md_rep["content_type"] == "text/markdown; charset=utf-8"
        assert "test_report_butler_lab" in md_rep["filename"]
        assert b"# RoboClaw" in md_rep["body"]

        json_rep = runner.get_report(fmt="json")
        assert json_rep["found"] is True
        assert json_rep["content_type"] == "application/json; charset=utf-8"
        assert "test_report_butler_lab" in json_rep["filename"]
        parsed = json.loads(json_rep["body"].decode("utf-8"))
        assert parsed["total_steps"] > 0


class TestStepHandlers:
    def test_ping_step_state(self):
        backend = FakeDashboardBackend(state='{"state": 1}')
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_ping", name="Ping", step="S1", type="ping")
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_ping_step_fallback_chat(self):
        backend = FakeDashboardBackend(state=None, agent_status={})
        backend._chat_response = {"success": True, "message": "pong"}
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_ping", name="Ping", step="S1", type="ping")
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_robot_info_step(self):
        backend = FakeDashboardBackend(state='{"robot_name": "butler"}')
        runner = ScenarioRunnerService(backend, ScenarioConfig(robot_name="butler"))
        tc = ScenarioTestCase(id="tc_info", name="Info", step="S1", type="robot_info")
        res = runner._execute_step(tc)
        assert res.status == "PASS"
        assert "butler" in res.detail

    def test_battery_step_healthy(self):
        backend = FakeDashboardBackend(
            telemetry={"battery": {"percentage": "80.0%", "voltage": "24V"}}
        )
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_bat", name="Bat", step="S1", type="battery")
        res = runner._execute_step(tc)
        assert res.status == "PASS"
        assert "80.0%" in res.detail

    def test_battery_step_low_warning(self):
        backend = FakeDashboardBackend(
            telemetry={"battery": {"percentage": "5.0%", "voltage": "20V"}}
        )
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_bat", name="Bat", step="S1", type="battery")
        res = runner._execute_step(tc)
        assert res.status == "WARNING"

    def test_battery_step_fallback_chat(self):
        backend = FakeDashboardBackend(telemetry={})
        backend._chat_response = {"success": True, "message": "배터리 50% 남음"}
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_bat", name="Bat", step="S1", type="battery")
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_camera_step_health(self):
        backend = FakeDashboardBackend(sensor_health={"camera": True})
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_cam", name="Cam", step="S1", type="camera")
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_map_step_health(self):
        backend = FakeDashboardBackend(sensor_health={"map": True})
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_map", name="Map", step="S1", type="map")
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_camera_analysis_with_scene_image(self):
        backend = FakeDashboardBackend()
        backend._chat_response = {
            "success": True,
            "message": "분석 완료, 이미지: scene_20261102_120000.png",
        }
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_ca", name="CamAnalysis", step="S2", type="camera_analysis")
        res = runner._execute_step(tc)
        assert res.status == "PASS"
        assert "scene_20261102_120000.png" in res.detail

    def test_camera_analysis_without_image_warns(self):
        backend = FakeDashboardBackend()
        backend._chat_response = {"success": True, "message": "사람 한 명이 보입니다."}
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_ca", name="CamAnalysis", step="S2", type="camera_analysis")
        res = runner._execute_step(tc)
        assert res.status == "WARNING"

    def test_map_analysis_with_map_image(self):
        backend = FakeDashboardBackend()
        backend._chat_response = {"success": True, "message": "맵 생성 완료: map_test.png"}
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_ma", name="MapAnalysis", step="S2", type="map_analysis")
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_navigation_guardrail_rejected(self, tmp_path):
        limits_file = tmp_path / "ROBOT_LIMITS.json"
        limits_file.write_text('{"max_linear_velocity": 0.4}', encoding="utf-8")

        backend = FakeDashboardBackend()
        backend._chat_response = {"success": False, "message": "속도 제한 초과로 명령 거부"}
        cfg = ScenarioConfig(robot_limits_file=str(limits_file))
        runner = ScenarioRunnerService(backend, cfg)
        tc = ScenarioTestCase(
            id="tc_gr",
            name="가드레일",
            step="S3",
            type="navigation",
            params={"mode": "guardrail", "speed": 0.4},
        )
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_navigation_motion_success(self):
        backend = FakeDashboardBackend()
        backend._chat_response = {"success": True, "message": "주행 완료"}
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(
            id="tc_nav",
            name="주행",
            step="S3",
            type="navigation",
            params={"mode": "motion", "command": "앞으로 1m 가줘"},
        )
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_manipulation_success(self):
        backend = FakeDashboardBackend()
        backend._chat_response = {"success": True, "message": "접기 완료"}
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_manip", name="매니퓰레이션", step="S3", type="manipulation")
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_persona_evaluation(self, tmp_path):
        soul_file = tmp_path / "ROBOT.md"
        soul_file.write_text(
            "# 로봇 버틀러\n- 이름: 버틀러\n- 성격: 친절하고 정확함", encoding="utf-8"
        )

        backend = FakeDashboardBackend()
        backend._chat_response = {
            "success": True,
            "message": "저는 버틀러이고 친절하게 모시겠습니다.",
        }
        cfg = ScenarioConfig(robot_soul_file=str(soul_file))
        runner = ScenarioRunnerService(backend, cfg)
        tc = ScenarioTestCase(id="tc_p", name="페르소나", step="S4", type="persona")
        res = runner._execute_step(tc)
        assert res.status == "PASS"
        assert "버틀러" in res.detail

    def test_custom_step(self):
        backend = FakeDashboardBackend()
        backend._chat_response = {"success": True, "message": "헬로 월드"}
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(
            id="tc_c",
            name="커스텀",
            step="S5",
            type="custom",
            params={"prompt": "인사해줘", "expected_keywords": ["헬로"]},
        )
        res = runner._execute_step(tc)
        assert res.status == "PASS"

    def test_unknown_type_fails(self):
        backend = FakeDashboardBackend()
        runner = ScenarioRunnerService(backend)
        tc = ScenarioTestCase(id="tc_u", name="알 수 없음", step="S9", type="unsupported_type")
        res = runner._execute_step(tc)
        assert res.status == "FAIL"
