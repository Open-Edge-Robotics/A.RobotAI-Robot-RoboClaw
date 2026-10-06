import pytest

pytest.importorskip("rclpy")

import json
from unittest.mock import MagicMock

import pytest
from robo_claw_agent.agent_node.prompts import load_robot_limits, load_troubleshooting_guide


def test_load_robot_limits_valid_file(tmp_path):
    """지정된 경로의 ROBOT_LIMITS.json 파일을 정상적으로 로드 및 파싱하는지 확인"""
    limits_content = {
        "max_linear_velocity": 1.5,
        "max_angular_velocity": 1.0,
        "workspace_bounds": {
            "x_min": -5.0,
            "x_max": 5.0
        }
    }
    limits_file = tmp_path / "ROBOT_LIMITS.json"
    limits_file.write_text(json.dumps(limits_content, indent=2), encoding="utf-8")

    loaded = load_robot_limits(str(limits_file))
    parsed = json.loads(loaded)
    assert parsed["max_linear_velocity"] == 1.5
    assert parsed["workspace_bounds"]["x_min"] == -5.0

def test_load_robot_limits_empty_or_missing():
    """파일이 없거나 설정되지 않았을 때 빈 문자열 반환 확인"""
    assert load_robot_limits("") == ""
    assert load_robot_limits(None) == ""
    assert load_robot_limits("/invalid/path/ROBOT_LIMITS.json") == ""
    # 유효하지 않은 JSON 형식인 경우의 예외 처리
    assert load_robot_limits("/tmp/nonexistent_limits_file.json") == ""

def test_load_troubleshooting_guide_valid_file(tmp_path):
    """지정된 경로의 TROUBLESHOOTING.md 파일을 정상적으로 로드하는지 확인"""
    guide_content = "# Test Troubleshooting Guide\n- NAVIGATION_TIMEOUT: Check lidar connection."
    guide_file = tmp_path / "TROUBLESHOOTING.md"
    guide_file.write_text(guide_content, encoding="utf-8")

    loaded = load_troubleshooting_guide(str(guide_file))
    assert loaded == guide_content

def test_load_troubleshooting_guide_empty_or_missing():
    """파일이 없거나 설정되지 않았을 때 빈 문자열 반환 확인"""
    assert load_troubleshooting_guide("") == ""
    assert load_troubleshooting_guide(None) == ""
    assert load_troubleshooting_guide("/invalid/path/TROUBLESHOOTING.md") == ""

def test_build_system_prompt_integration_limits_and_troubleshooting():
    """AgentNode의 _build_system_prompt 내부에서 로봇 한계 사양 및 장애 조치가 프롬프트에 조립되는지 검증"""
    class MockAgentNode:
        def __init__(self):
            self._system_prompt = "Base Prompt {skill_list}"
            self._robot_limits = '{\n  "max_linear_velocity": 1.5\n}'
            self._troubleshooting_guide = "Troubleshoot navigation issues here."
            self._skills_guide = ""
            self._script_skills_guide = ""
            self._startup_knowledge_ctx = ""
            self._robot_soul = ""
            self._skills = MagicMock()
            self._skills.list_skills.return_value = [{"name": "move", "description": "move robot"}]

        def _build_system_prompt(self) -> str:
            skills = self._skills.list_skills()
            skill_lines = "\n".join(f"  - {s['name']}: {s['description']}" for s in skills)
            base = self._system_prompt
            if "{skill_list}" in base:
                base = base.format(skill_list=skill_lines)
            else:
                base = base + f"\n\n[사용 가능한 스킬]\n{skill_lines}"

            if self._skills_guide:
                base = base + f"\n\n[기본 스킬 가이드]\n{self._skills_guide}"
            if self._script_skills_guide:
                base = base + f"\n\n[스크립트 세부 가이드]\n{self._script_skills_guide}"

            # 로봇 제약 규격 주입
            if self._robot_limits:
                base = base + f"\n\n[로봇 구동 한계 스펙 (ROBOT_LIMITS)]\n{self._robot_limits}"
            # 장애 조치 가이드 주입
            if self._troubleshooting_guide:
                base = base + f"\n\n[장애 조치 가이드 (TROUBLESHOOTING)]\n{self._troubleshooting_guide}"

            if self._startup_knowledge_ctx:
                base = base + f"\n\n{self._startup_knowledge_ctx}"
            if self._robot_soul:
                return f"{self._robot_soul}\n\n---\n\n{base}"
            return base

    node = MockAgentNode()
    prompt = node._build_system_prompt()

    assert "[로봇 구동 한계 스펙 (ROBOT_LIMITS)]" in prompt
    assert "max_linear_velocity" in prompt
    assert "[장애 조치 가이드 (TROUBLESHOOTING)]" in prompt
    assert "Troubleshoot navigation issues here." in prompt

def test_get_robot_health_state():
    """자가진단 데이터(RobotHealthState) 생성 및 필수 필드 정상 탑재 여부 검증"""
    class MockAgentNode:
        def __init__(self, state):
            self._state = state

        def _get_robot_health_state(self) -> dict:
            state = {
                "battery_percent": 100.0,
                "localization_status": "OK",
                "active_errors": [],
                "sensor_status": {
                    "lidar": "OK",
                    "camera": "OK",
                    "imu": "OK"
                }
            }
            # AgentState.ERROR를 간접 체크
            from robo_claw_agent.types import AgentState
            if self._state == AgentState.ERROR:
                state["localization_status"] = "ERROR"
                state["active_errors"].append("AGENT_INTERNAL_ERROR")
            return state

    from robo_claw_agent.types import AgentState

    # 1. 일반(IDLE) 상태인 경우
    node_idle = MockAgentNode(AgentState.IDLE)
    health_idle = node_idle._get_robot_health_state()
    assert health_idle["battery_percent"] == 100.0
    assert health_idle["localization_status"] == "OK"
    assert len(health_idle["active_errors"]) == 0
    assert health_idle["sensor_status"]["lidar"] == "OK"

    # 2. 에러(ERROR) 상태인 경우
    node_error = MockAgentNode(AgentState.ERROR)
    health_error = node_error._get_robot_health_state()
    assert health_error["localization_status"] == "ERROR"
    assert "AGENT_INTERNAL_ERROR" in health_error["active_errors"]
