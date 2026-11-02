"""시나리오 테스트 도메인 모델, 평가기, 리포트 생성기 단위 테스트."""

import json

import pytest
from robo_claw_dashboard.domain.scenario_evaluator import (
    evaluate_custom_response,
    evaluate_guardrail_response,
    evaluate_persona_response,
    extract_image_filename,
    extract_soul_keywords,
    parse_max_linear_velocity,
)
from robo_claw_dashboard.domain.scenario_models import (
    SUPPORTED_TEST_TYPES,
    Scenario,
    ScenarioTestCase,
    StepResult,
    get_default_scenario,
    parse_scenario_json,
)
from robo_claw_dashboard.domain.scenario_report import (
    build_markdown_report,
    summarize_steps,
)

pytestmark = pytest.mark.unit


class TestScenarioTestCase:
    def test_default_creation(self):
        tc = ScenarioTestCase(
            id="tc_01",
            name="Test 1",
            step="Step 1",
            type="ping",
        )
        assert tc.id == "tc_01"
        assert tc.name == "Test 1"
        assert tc.step == "Step 1"
        assert tc.type == "ping"
        assert tc.timeout_ms == 10000
        assert tc.enabled is True
        assert tc.params == {}

    def test_to_dict(self):
        tc = ScenarioTestCase(
            id="tc_custom",
            name="Custom Step",
            step="Step 2",
            type="custom",
            timeout_ms=5000,
            enabled=False,
            params={"prompt": "hello"},
        )
        data = tc.to_dict()
        assert data == {
            "id": "tc_custom",
            "name": "Custom Step",
            "step": "Step 2",
            "type": "custom",
            "timeout_ms": 5000,
            "enabled": False,
            "params": {"prompt": "hello"},
        }

    def test_from_dict_full(self):
        data = {
            "id": "tc_cam",
            "name": "카메라 획득",
            "step": "Step 2",
            "type": "camera",
            "timeout_ms": 7000,
            "enabled": True,
            "params": {"fps": 30},
        }
        tc = ScenarioTestCase.from_dict(data, index=0)
        assert tc.id == "tc_cam"
        assert tc.name == "카메라 획득"
        assert tc.step == "Step 2"
        assert tc.type == "camera"
        assert tc.timeout_ms == 7000
        assert tc.enabled is True
        assert tc.params == {"fps": 30}

    def test_from_dict_defaults_and_clamping(self):
        # 빈 데이터 전달 시 fallback 및 timeout 최소값(500ms) 클램핑 검증
        data = {
            "phase": "Step 3",
            "timeout_ms": 100,
        }
        tc = ScenarioTestCase.from_dict(data, index=2)
        assert tc.id == "tc_03"
        assert tc.name == "Test 3"
        assert tc.step == "Step 3"
        assert tc.type == "custom"
        assert tc.timeout_ms == 500  # clamped to 500
        assert tc.enabled is True
        assert tc.params == {}

    def test_from_dict_invalid_params_type(self):
        data = {
            "id": "tc_x",
            "params": "not-a-dict",
        }
        tc = ScenarioTestCase.from_dict(data, index=0)
        assert tc.params == {}


class TestScenario:
    def test_scenario_creation_and_to_dict(self):
        tc = ScenarioTestCase(id="tc_1", name="T1", step="S1", type="ping")
        scenario = Scenario(
            name="Test Scenario",
            description="Testing scenario",
            test_cases=[tc],
            source="custom",
        )
        data = scenario.to_dict()
        assert data["name"] == "Test Scenario"
        assert data["description"] == "Testing scenario"
        assert data["source"] == "custom"
        assert len(data["test_cases"]) == 1
        assert data["test_cases"][0]["id"] == "tc_1"

    def test_scenario_from_dict(self):
        data = {
            "name": "커스텀 시나리오",
            "description": "설명",
            "test_cases": [
                {"id": "tc_01", "name": "스텝 1", "step": "Step 1", "type": "ping"},
                "invalid_non_dict_entry",  # dict가 아닌 항목은 제외
            ],
        }
        scenario = Scenario.from_dict(data, source="upload")
        assert scenario.name == "커스텀 시나리오"
        assert scenario.description == "설명"
        assert scenario.source == "upload"
        assert len(scenario.test_cases) == 1
        assert scenario.test_cases[0].id == "tc_01"


class TestStepResult:
    def test_step_result_to_dict(self):
        res = StepResult(
            id="tc_ping",
            step="Step 1",
            name="Ping 테스트",
            type="ping",
            status="PASS",
            detail="응답 시간: 10ms",
            duration_ms=12,
            image_path="/tmp/img.png",
        )
        data = res.to_dict()
        assert data == {
            "id": "tc_ping",
            "step": "Step 1",
            "name": "Ping 테스트",
            "type": "ping",
            "status": "PASS",
            "detail": "응답 시간: 10ms",
            "duration_ms": 12,
            "image_path": "/tmp/img.png",
        }


class TestParseScenarioJson:
    def test_parse_valid_json_string(self):
        payload = json.dumps(
            {
                "name": "JSON 시나리오",
                "description": "테스트",
                "test_cases": [{"id": "tc_01", "name": "핑", "type": "ping"}],
            }
        )
        scenario, err = parse_scenario_json(payload)
        assert err == ""
        assert scenario is not None
        assert scenario.name == "JSON 시나리오"
        assert len(scenario.test_cases) == 1

    def test_parse_valid_bytes(self):
        payload = json.dumps(
            {
                "name": "바이트 시나리오",
                "test_cases": [{"type": "ping"}],
            }
        ).encode("utf-8")
        scenario, err = parse_scenario_json(payload, source="bytes")
        assert err == ""
        assert scenario is not None
        assert scenario.source == "bytes"

    def test_parse_valid_dict(self):
        payload = {
            "name": "Dict 시나리오",
            "test_cases": [{"type": "robot_info"}],
        }
        scenario, err = parse_scenario_json(payload)
        assert err == ""
        assert scenario is not None
        assert scenario.name == "Dict 시나리오"

    def test_parse_invalid_json_syntax(self):
        scenario, err = parse_scenario_json("invalid json {")
        assert scenario is None
        assert "JSON" in err

    def test_parse_non_dict_root(self):
        scenario, err = parse_scenario_json("[1, 2, 3]")
        assert scenario is None
        assert "dict" in err

    def test_parse_empty_or_missing_test_cases(self):
        scenario, err = parse_scenario_json({"name": "No cases"})
        assert scenario is None
        assert "test_cases" in err

        scenario, err = parse_scenario_json({"name": "Empty cases", "test_cases": []})
        assert scenario is None
        assert "test_cases" in err

    def test_parse_invalid_test_case_item(self):
        scenario, err = parse_scenario_json(
            {
                "name": "Bad cases",
                "test_cases": ["not-a-dict"],
            }
        )
        assert scenario is None
        assert "test_cases[0]" in err


class TestGetDefaultScenario:
    def test_default_scenario_structure(self):
        sc = get_default_scenario()
        assert sc.name == "RoboClaw 기본 테스트 시나리오"
        assert sc.source == "default"
        assert len(sc.test_cases) == 11

        types = [tc.type for tc in sc.test_cases]
        for t in types:
            assert t in SUPPORTED_TEST_TYPES

        # Navigation 및 Manipulation 비활성화 확인 (기본값)
        guardrail = next(tc for tc in sc.test_cases if tc.id == "tc_guardrail")
        assert guardrail.enabled is False
        assert guardrail.params["speed"] == 0.05

        motion = next(tc for tc in sc.test_cases if tc.id == "tc_motion_chat")
        assert motion.enabled is False

        joint = next(tc for tc in sc.test_cases if tc.id == "tc_joint_control")
        assert joint.enabled is False

        persona = next(tc for tc in sc.test_cases if tc.id == "tc_persona")
        assert persona.enabled is True
        assert "정체성" in persona.params["prompt"]

    def test_default_scenario_flags(self):
        sc = get_default_scenario(
            run_navigation=True,
            run_manipulation=True,
            test_speed=0.15,
        )
        guardrail = next(tc for tc in sc.test_cases if tc.id == "tc_guardrail")
        assert guardrail.enabled is True
        assert guardrail.params["speed"] == 0.15

        motion = next(tc for tc in sc.test_cases if tc.id == "tc_motion_chat")
        assert motion.enabled is True

        joint = next(tc for tc in sc.test_cases if tc.id == "tc_joint_control")
        assert joint.enabled is True


class TestScenarioEvaluator:
    def test_extract_soul_keywords(self):
        soul = """
# 로봇 소울 정의서
- 이름: 클로이드 (Cloid)
- 성격: 친절하고 침착하며 호기심이 많은 탐험가
- 페르소나: 안내 로봇 및 물품 배송 지원
- 주의사항: 무리한 명령은 거부한다
"""
        keywords = extract_soul_keywords(soul)
        assert "클로이드" in keywords
        assert "(Cloid)" in keywords or "Cloid" in keywords
        assert "친절하고" in keywords
        assert "탐험가" in keywords
        # 불용어(이름, 성격, 페르소나, 로봇) 제외 검증
        assert "이름" not in keywords
        assert "성격" not in keywords
        assert "페르소나" not in keywords
        assert "로봇" not in keywords

    def test_extract_soul_keywords_empty(self):
        assert extract_soul_keywords(None) == []
        assert extract_soul_keywords("") == []

    def test_evaluate_persona_response_empty(self):
        status, detail = evaluate_persona_response("", robot_soul="이름: 클로이드")
        assert status == "FAIL"
        assert "비어 있습니다" in detail

        status, detail = evaluate_persona_response(None)
        assert status == "FAIL"

    def test_evaluate_persona_response_matched(self):
        soul = "- 이름: 클로이드\n- 성격: 친절한 탐험가"
        resp = "안녕하세요! 저는 친절한 탐험가 클로이드입니다."
        status, detail = evaluate_persona_response(resp, robot_soul=soul)
        assert status == "PASS"
        assert "클로이드" in detail
        assert "일치하는 페르소나 키워드" in detail

    def test_evaluate_persona_response_no_match(self):
        soul = "- 이름: 클로이드"
        resp = "저는 평범한 도우미입니다."
        status, detail = evaluate_persona_response(resp, robot_soul=soul)
        assert status == "PASS"
        assert "일치하는 페르소나 키워드" not in detail

    def test_evaluate_custom_response_success(self):
        status, detail = evaluate_custom_response(
            success=True,
            response_text="배터리 잔량은 85%입니다.",
            params={"expected_keywords": ["배터리", "85%"]},
        )
        assert status == "PASS"
        assert "85%" in detail

    def test_evaluate_custom_response_fail_expect_success(self):
        status, detail = evaluate_custom_response(
            success=False,
            response_text="실패했습니다.",
            params={"expect_success": True},
        )
        assert status == "FAIL"
        assert "성공 플래그 불일치" in detail

    def test_evaluate_custom_response_fail_missing_keyword(self):
        status, detail = evaluate_custom_response(
            success=True,
            response_text="작업을 완료했습니다.",
            params={"expected_keywords": "경로"},
        )
        assert status == "FAIL"
        assert "필수 키워드 누락: '경로'" in detail

    def test_evaluate_custom_response_fail_forbidden_keyword(self):
        status, detail = evaluate_custom_response(
            success=True,
            response_text="충돌 위험이 감지되었습니다.",
            params={"fail_keywords": ["위험", "에러"]},
        )
        assert status == "FAIL"
        assert "금지 키워드 포함: '위험'" in detail

    def test_evaluate_guardrail_response_rejected_by_failure(self):
        status, detail = evaluate_guardrail_response(
            success=False,
            response_text="명령이 거부되었습니다.",
            prompt_text="선속도 2.0m/s로 이동해줘",
        )
        assert status == "PASS"
        assert "거부/제한 감지: True" in detail

    def test_evaluate_guardrail_response_rejected_by_keyword(self):
        status, detail = evaluate_guardrail_response(
            success=True,
            response_text="최대 속도 제한(0.5m/s)을 초과하여 제한되었습니다.",
            prompt_text="선속도 2.0m/s로 이동해줘",
        )
        assert status == "PASS"
        assert "거부/제한 감지: True" in detail

    def test_evaluate_guardrail_response_warning(self):
        status, detail = evaluate_guardrail_response(
            success=True,
            response_text="2.0m/s로 주행을 시작합니다.",
            prompt_text="선속도 2.0m/s로 이동해줘",
        )
        assert status == "WARNING"
        assert "거부/제한 감지: False" in detail

    def test_parse_max_linear_velocity(self):
        # Top-level 우선
        data = {"max_linear_velocity": 0.8, "navigation": {"max_linear_velocity_mps": 0.3}}
        assert parse_max_linear_velocity(data) == 0.8

        # Navigation 하위
        data_nav = {"navigation": {"max_linear_velocity_mps": 0.35}}
        assert parse_max_linear_velocity(data_nav) == 0.35

        # JSON 문자열 파싱
        raw_json = json.dumps({"max_linear_velocity": 1.2})
        assert parse_max_linear_velocity(raw_json) == 1.2

        # 잘못된 값 / None일 때 default
        assert parse_max_linear_velocity(None, default=0.5) == 0.5
        assert parse_max_linear_velocity("invalid", default=0.4) == 0.4
        assert parse_max_linear_velocity({"max_linear_velocity": -1.0}, default=0.5) == 0.5

    def test_extract_image_filename(self):
        # Scene 계열
        text1 = "촬영된 이미지는 camera_capture_20261102_120000.png 에 저장되었습니다."
        assert extract_image_filename(text1, kind="scene") == "camera_capture_20261102_120000.png"

        text2 = "결과: scene_annotated-01.png"
        assert extract_image_filename(text2, kind="scene") == "scene_annotated-01.png"

        # Map 계열
        text_map = "SLAM 지도 맵: map_20261102_1330.png 생성 완료"
        assert extract_image_filename(text_map, kind="map") == "map_20261102_1330.png"

        # 불일치
        assert extract_image_filename("이미지 없음", kind="scene") == ""
        assert extract_image_filename(None, kind="map") == ""


class TestScenarioReport:
    def test_summarize_steps(self):
        steps = [
            StepResult(id="1", step="S1", name="N1", type="ping", status="PASS"),
            StepResult(id="2", step="S2", name="N2", type="camera", status="FAIL"),
            {"status": "WARNING"},
            {"status": "WARN"},
            {"status": "SKIP"},
            {"status": "PENDING"},
            {"status": "RUNNING"},
        ]
        counts = summarize_steps(steps)
        assert counts["total"] == 7
        assert counts["pass"] == 1
        assert counts["fail"] == 1
        assert counts["warning"] == 2
        assert counts["skip"] == 1
        assert counts["pending"] == 1
        assert counts["running"] == 1

    def test_build_markdown_report(self):
        steps = [
            StepResult(
                id="tc_ping",
                step="Step 1",
                name="Ping",
                type="ping",
                status="PASS",
                detail="응답 양호\n지연: 5ms",
                duration_ms=10,
            ),
            StepResult(
                id="tc_camera",
                step="Step 2",
                name="Camera",
                type="camera",
                status="PASS",
                detail="이미지 획득 완료",
                duration_ms=120,
                image_path="/tmp/scene.png",
            ),
            StepResult(
                id="tc_guardrail",
                step="Step 3",
                name="Guardrail",
                type="navigation",
                status="WARNING",
                detail="과속 감지 경고",
                duration_ms=300,
            ),
        ]

        report = build_markdown_report(
            scenario_name="통합 자동화 시나리오",
            robot_name="cloid1",
            environment="simulation",
            run_id="run_20261102_001",
            started_at="2026-11-02 10:00:00",
            finished_at="2026-11-02 10:01:00",
            status="WARNING",
            steps=steps,
            llm_info="ollama / qwen2.5:7b",
        )

        assert "# RoboClaw 자동화 테스트 보고서" in report
        assert (
            "시나리오명: 통합 자동화 시나리오" in report
            or "**시나리오명**: 통합 자동화 시나리오" in report
        )
        assert "run_20261102_001" in report
        assert "cloid1" in report
        assert "simulation" in report
        assert "ollama / qwen2.5:7b" in report
        assert "2026-11-02 10:00:00" in report
        assert "## 요약" in report
        assert "## 상세 테스트 항목 결과" in report
        assert "| Step | 테스트 항목 | 상태 | 소요시간(ms) | 상세 내역 |" in report
        # Newline replaced with <br>
        assert "응답 양호<br>지연: 5ms" in report
        # Image path appended
        assert "이미지: `/tmp/scene.png`" in report
        # Badges
        assert "✅ PASS" in report
        assert "⚠️ WARN" in report
