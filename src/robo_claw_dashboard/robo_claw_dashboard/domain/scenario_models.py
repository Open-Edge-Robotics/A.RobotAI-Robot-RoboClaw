"""시나리오 테스트 도메인 모델 — 테스트 케이스, 시나리오 및 실행 결과."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

SUPPORTED_TEST_TYPES: tuple[str, ...] = (
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
)


@dataclass
class ScenarioTestCase:
    """개별 테스트 케이스 정의 모델."""

    id: str
    name: str
    step: str
    type: str
    timeout_ms: int = 10000
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """직렬화 가능한 딕셔너리로 변환한다."""
        return {
            "id": self.id,
            "name": self.name,
            "step": self.step,
            "type": self.type,
            "timeout_ms": self.timeout_ms,
            "enabled": self.enabled,
            "params": dict(self.params),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], index: int = 0) -> ScenarioTestCase:
        """딕셔너리에서 ScenarioTestCase를 생성한다."""
        id_ = str(data.get("id") or f"tc_{index + 1:02d}")
        name = str(data.get("name") or f"Test {index + 1}")
        step = str(data.get("step") or data.get("phase") or "Step 1")
        test_type = str(data.get("type") or "custom").strip()

        try:
            raw_timeout = data.get("timeout_ms")
            timeout_ms = int(raw_timeout) if raw_timeout is not None else 10000
        except (ValueError, TypeError):
            timeout_ms = 10000
        timeout_ms = max(500, timeout_ms)

        enabled = bool(data.get("enabled", True))
        raw_params = data.get("params")
        params = dict(raw_params) if isinstance(raw_params, dict) else {}

        return cls(
            id=id_,
            name=name,
            step=step,
            type=test_type,
            timeout_ms=timeout_ms,
            enabled=enabled,
            params=params,
        )


@dataclass
class Scenario:
    """여러 테스트 케이스로 구성된 테스트 시나리오."""

    name: str
    description: str
    test_cases: list[ScenarioTestCase]
    source: str = "default"

    def to_dict(self) -> dict[str, Any]:
        """직렬화 가능한 딕셔너리로 변환한다."""
        return {
            "name": self.name,
            "description": self.description,
            "test_cases": [tc.to_dict() for tc in self.test_cases],
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, source: str = "custom") -> Scenario:
        """딕셔너리에서 Scenario를 생성한다."""
        name = str(data.get("name") or "Unnamed Scenario")
        description = str(data.get("description") or "")
        raw_cases = data.get("test_cases") or []
        test_cases = [
            ScenarioTestCase.from_dict(item, idx)
            for idx, item in enumerate(raw_cases)
            if isinstance(item, dict)
        ]
        scenario_source = str(data.get("source") or source)
        return cls(
            name=name,
            description=description,
            test_cases=test_cases,
            source=scenario_source,
        )


@dataclass
class StepResult:
    """개별 테스트 스텝의 실행 결과."""

    id: str
    step: str
    name: str
    type: str
    status: str  # "PENDING" | "RUNNING" | "PASS" | "FAIL" | "WARNING" | "SKIP"
    detail: str = ""
    duration_ms: int = 0
    image_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        """직렬화 가능한 딕셔너리로 변환한다."""
        return {
            "id": self.id,
            "step": self.step,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "detail": self.detail,
            "duration_ms": self.duration_ms,
            "image_path": self.image_path,
        }


def parse_scenario_json(
    raw: str | bytes | dict[str, Any],
    *,
    source: str = "custom",
) -> tuple[Scenario | None, str]:
    """JSON 문자열/바이트 또는 딕셔너리를 시나리오 객체로 파싱한다."""
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except Exception as err:
            return None, f"JSON 파싱 실패: {err}"
    elif isinstance(raw, dict):
        data = raw
    else:
        return None, "시나리오 데이터는 JSON 문자열 또는 dict여야 합니다."

    if not isinstance(data, dict):
        return None, "시나리오 루트는 JSON 객체(dict)여야 합니다."

    cases = data.get("test_cases")
    if not isinstance(cases, list) or len(cases) == 0:
        return None, "test_cases는 비어 있지 않은 리스트여야 합니다."

    for idx, item in enumerate(cases):
        if not isinstance(item, dict):
            return None, f"test_cases[{idx}] 항목은 객체(dict)여야 합니다."

    return Scenario.from_dict(data, source=source), ""


def get_default_scenario(
    *,
    run_navigation: bool = False,
    run_manipulation: bool = False,
    test_speed: float = 0.05,
) -> Scenario:
    """기본 내장 11개 표준 테스트 시나리오를 구성하여 반환한다."""
    return Scenario(
        name="RoboClaw 기본 테스트 시나리오",
        description="연결성 진단부터 AI 페르소나 매핑까지의 표준 자동화 테스트 세트",
        source="default",
        test_cases=[
            # Phase 1
            ScenarioTestCase(
                id="tc_ping",
                name="gRPC Ping 연결성",
                step="Step 1",
                type="ping",
                timeout_ms=3000,
                enabled=True,
            ),
            ScenarioTestCase(
                id="tc_robot_info",
                name="로봇 정보 조회 및 정합성",
                step="Step 1",
                type="robot_info",
                timeout_ms=3000,
                enabled=True,
            ),
            ScenarioTestCase(
                id="tc_battery",
                name="배터리 상태 점검",
                step="Step 1",
                type="battery",
                timeout_ms=10000,
                enabled=True,
            ),
            # Phase 2
            ScenarioTestCase(
                id="tc_camera",
                name="카메라 이미지 획득",
                step="Step 2",
                type="camera",
                timeout_ms=5000,
                enabled=True,
            ),
            ScenarioTestCase(
                id="tc_slam_map",
                name="SLAM 지도 맵 수신",
                step="Step 2",
                type="map",
                timeout_ms=5000,
                enabled=True,
            ),
            ScenarioTestCase(
                id="tc_camera_analysis",
                name="카메라 이미지 분석 및 전달 스킬",
                step="Step 2",
                type="camera_analysis",
                timeout_ms=25000,
                enabled=True,
            ),
            ScenarioTestCase(
                id="tc_map_analysis",
                name="맵 이미지 분석 및 전달 스킬",
                step="Step 2",
                type="map_analysis",
                timeout_ms=25000,
                enabled=True,
            ),
            # Phase 3
            ScenarioTestCase(
                id="tc_guardrail",
                name="물리 가드레일 속도 제약",
                step="Step 3",
                type="navigation",
                timeout_ms=15000,
                enabled=run_navigation,
                params={
                    "mode": "guardrail",
                    "speed": test_speed,
                },
            ),
            ScenarioTestCase(
                id="tc_motion_chat",
                name="자연어 기반 주행 스킬 기동",
                step="Step 3",
                type="navigation",
                timeout_ms=15000,
                enabled=run_navigation,
                params={
                    "mode": "motion",
                },
            ),
            # Phase 3-2
            ScenarioTestCase(
                id="tc_joint_control",
                name="매니퓰레이션 관절 구동 검증",
                step="Step 3",
                type="manipulation",
                timeout_ms=15000,
                enabled=run_manipulation,
            ),
            # Phase 4
            ScenarioTestCase(
                id="tc_persona",
                name="LLM 페르소나 및 응답 지능",
                step="Step 4",
                type="persona",
                timeout_ms=15000,
                enabled=True,
                params={
                    "prompt": "너의 정체성과 성격에 대해 한 문장으로 답변해줘.",
                },
            ),
        ],
    )
