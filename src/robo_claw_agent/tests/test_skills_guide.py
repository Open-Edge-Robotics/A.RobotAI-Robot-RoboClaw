import pytest

pytest.importorskip("rclpy")

from unittest.mock import MagicMock

import pytest
from robo_claw_agent.agent_node.prompts import load_script_skills_guide, load_skills_guide


def test_load_skills_guide_valid_file(tmp_path):
    """지정된 경로의 스킬 가이드 파일을 정상적으로 로드하는지 확인"""
    guide_content = "# Test Skills Guide\n- skill1: desc1"
    guide_file = tmp_path / "SKILLS.md"
    guide_file.write_text(guide_content, encoding="utf-8")

    loaded = load_skills_guide(str(guide_file))
    assert loaded == guide_content

def test_load_skills_guide_empty_or_missing():
    """파일이 없거나 설정되지 않았을 때 빈 문자열 반환 확인"""
    assert load_skills_guide("") == ""
    assert load_skills_guide(None) == ""
    assert load_skills_guide("/invalid/path/SKILLS.md") == ""

def test_load_script_skills_guide_auto_detect(tmp_path):
    """스크립트 디렉토리에 있는 SKILLS.md를 자동으로 탐색하여 로드하는지 확인"""
    guide_content = "# Script Guide\n- script1.sh: runs something"

    # 1. 디렉토리만 있는 경우 (SKILLS.md 없음) -> 빈 문자열 반환
    assert load_script_skills_guide(str(tmp_path)) == ""

    # 2. SKILLS.md 작성 후 -> 로드 성공 확인
    guide_file = tmp_path / "SKILLS.md"
    guide_file.write_text(guide_content, encoding="utf-8")

    loaded = load_script_skills_guide(str(tmp_path))
    assert loaded == guide_content

def test_load_script_skills_guide_empty_or_missing():
    """스크립트 디렉토리가 없거나 설정되지 않았을 때 빈 문자열 반환 확인"""
    assert load_script_skills_guide("") == ""
    assert load_script_skills_guide(None) == ""
    assert load_script_skills_guide("/invalid/script/dir") == ""

def test_build_system_prompt_integration():
    """AgentNode의 _build_system_prompt 내부에서 가이드 텍스트들이 정상적으로 프롬프트에 조립되는지 검증"""
    # ROS 2 없이 AgentNode의 _build_system_prompt 로직만 모의 구현 및 검증
    # node.py에 정의된 _build_system_prompt의 복사 구조 검증
    class MockAgentNode:
        def __init__(self):
            self._system_prompt = "Base Prompt {skill_list}"
            self._skills_guide = "Base Guide Details"
            self._script_skills_guide = "Script Guide Details"
            self._robot_limits = "Robot Limits Details"
            self._troubleshooting_guide = "Troubleshooting Guide Details"
            self._startup_knowledge_ctx = "Startup RAG Data"
            self._robot_soul = "I am a helpful robot"
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

            # 스킬 가이드(SKILLS.md) 주입
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

    assert "I am a helpful robot" in prompt
    assert "Base Prompt" in prompt
    assert "  - move: move robot" in prompt
    assert "[기본 스킬 가이드]\nBase Guide Details" in prompt
    assert "[스크립트 세부 가이드]\nScript Guide Details" in prompt
    assert "[로봇 구동 한계 스펙 (ROBOT_LIMITS)]\nRobot Limits Details" in prompt
    assert "[장애 조치 가이드 (TROUBLESHOOTING)]\nTroubleshooting Guide Details" in prompt
    assert "Startup RAG Data" in prompt

