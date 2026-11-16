"""시나리오 step 디스패치·소요시간·유효 설정 사용 규칙 고정 테스트.

``_execute_step`` 은 step 타입을 핸들러로 보내고 결과에 소요시간을 채운다. 지원하지 않는
타입은 FAIL 로 떨어지며, step 핸들러는 startup 설정이 아니라 대시보드에서 입력한 유효
설정을 따른다. step 핸들러 모듈 분리 전후로 이 규칙이 유지되는지 고정한다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from robo_claw_dashboard.application.scenario_runner import (
    ScenarioConfig,
    ScenarioRunnerService,
)
from robo_claw_dashboard.domain.scenario_models import ScenarioTestCase

pytestmark = pytest.mark.unit

SUPPORTED_STEP_TYPES = [
    "ping",
    "robot_info",
    "battery",
    "camera",
    "map",
    "camera_analysis",
    "map_analysis",
    "navigation",
    "manipulation",
    "persona",
    "custom",
]

REMOTE_SCENARIO = {
    "name": "원격 시나리오",
    "test_cases": [{"id": "tc_ping", "name": "gRPC Ping", "step": "Step 1", "type": "ping"}],
}


class FakeBackend:
    """조회 API 가 정상값을 돌려주는 가짜 백엔드(상태/채팅 응답은 주입 가능)."""

    def __init__(
        self,
        *,
        state: str | None = '{"robot_name": "butler"}',
        chat_response: dict[str, Any] | None = None,
    ) -> None:
        self.state = state
        self.chat_response = chat_response or {
            "success": True,
            "message": "완료했습니다. scene_1.png",
        }
        self.chat_history: list[str] = []

    def query_state(self) -> str | None:
        return self.state

    def latest_agent_status(self) -> dict[str, Any]:
        return {"state_code": 1}

    def latest_telemetry(self) -> dict[str, Any]:
        return {"battery": {"percentage": "80.0%", "voltage": "24.5V"}}

    def latest_sensor_health(self) -> dict[str, Any]:
        return {"camera": True, "map": True}

    def latest_map(self) -> dict[str, Any]:
        return {"available": True, "topic": "/map", "width": 12, "height": 8}

    def send_chat(self, message: str) -> dict[str, Any]:
        self.chat_history.append(message)
        return dict(self.chat_response)


class Fetcher:
    """원격 시나리오 응답을 고정하는 가짜 HTTP 페처."""

    def __init__(self, payload: Any = None) -> None:
        self.payload = payload if payload is not None else REMOTE_SCENARIO

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
        return 200, json.dumps(self.payload).encode("utf-8")


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.mark.parametrize("step_type", SUPPORTED_STEP_TYPES)
def test_supported_step_type_reports_duration(step_type):
    runner = ScenarioRunnerService(FakeBackend(), ScenarioConfig(robot_name="butler"))
    tc = ScenarioTestCase(
        id="tc_" + step_type,
        name=step_type,
        step="S1",
        type=step_type,
        params={"prompt": "질문", "command": "명령"},
    )

    res = runner._execute_step(tc)

    assert res.status in ("PASS", "WARNING", "FAIL")
    assert isinstance(res.duration_ms, int)
    assert res.duration_ms >= 0
    assert res.type == step_type
    assert res.id == tc.id


def test_unsupported_step_type_fails_with_reason():
    runner = ScenarioRunnerService(FakeBackend())
    tc = ScenarioTestCase(id="tc_bad", name="알 수 없음", step="S9", type="teleport")

    res = runner._execute_step(tc)

    assert res.status == "FAIL"
    assert "지원되지 않는 테스트 타입" in res.detail
    assert res.duration_ms >= 0


def test_camera_analysis_default_prompt_and_detail_format():
    """카메라 분석 step 의 기본 프롬프트와 결과 문구를 고정한다."""
    backend = FakeBackend(
        chat_response={"success": True, "message": "장면에는 사람 한 명, scene_42.png"}
    )
    runner = ScenarioRunnerService(backend)
    tc = ScenarioTestCase(id="tc_ca", name="장면", step="S2", type="camera_analysis")

    res = runner._execute_step(tc)

    assert backend.chat_history == [
        "현재 카메라 장면에 무엇이 보이는지 분석하고 생성된 이미지 파일명"
        "(예: scene_*.png)을 답변에 포함해서 알려줘."
    ]
    assert res.status == "PASS"
    assert res.detail.startswith("장면 분석 성공 (이미지: scene_42.png)")
    assert "답변: 장면에는 사람 한 명, scene_42.png" in res.detail


def test_map_analysis_default_prompt_and_warning_detail_format():
    """맵 분석 step 은 파일명이 없을 때 WARNING 문구를 그대로 유지한다."""
    backend = FakeBackend(chat_response={"success": True, "message": "지도가 비어 있습니다"})
    runner = ScenarioRunnerService(backend)
    tc = ScenarioTestCase(id="tc_ma", name="맵", step="S2", type="map_analysis")

    res = runner._execute_step(tc)

    assert backend.chat_history == [
        "현재 지도를 분석하고 생성된 맵 이미지 파일명(예: map_*.png)을 답변에 포함해서 알려줘."
    ]
    assert res.status == "WARNING"
    assert res.detail == "맵 분석 응답 완료 (이미지 파일명 누락)\n답변: 지도가 비어 있습니다"


def test_robot_info_step_prefers_dashboard_user_config():
    """startup 설정에 로봇명이 없어도 대시보드 입력값을 따른다."""
    runner = ScenarioRunnerService(
        FakeBackend(state=None), ScenarioConfig(), http_fetcher=Fetcher()
    )
    runner.sync_scenario(
        {
            "config_server_url": "http://10.0.0.5:30180",
            "robot_name": "former",
            "environment": "kon47",
        }
    )
    tc = ScenarioTestCase(id="tc_info", name="로봇 정보", step="S1", type="robot_info")

    res = runner._execute_step(tc)

    assert res.status == "PASS"
    assert "former" in res.detail
