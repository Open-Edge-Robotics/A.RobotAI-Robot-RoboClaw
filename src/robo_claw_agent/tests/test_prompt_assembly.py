import importlib.util
import re
from pathlib import Path

_PROMPTS_PATH = (
    Path(__file__).resolve().parents[1] / "robo_claw_agent" / "agent_node" / "prompts.py"
)
_SPEC = importlib.util.spec_from_file_location("prompt_assembly_under_test", _PROMPTS_PATH)
_PROMPTS = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_PROMPTS)
assemble_system_prompt = _PROMPTS.assemble_system_prompt
assemble_static_prompt_base = _PROMPTS.assemble_static_prompt_base
compact_schema_summary = _PROMPTS.compact_schema_summary
# 문구를 하드코딩하면 프롬프트 톤을 다듬을 때마다 테스트가 깨진다(실제로 origin/main 의
# 톤 완화 커밋에서 이 테스트가 갱신 없이 깨진 채 푸시됐다). 이 테스트가 검증하려는 것은
# **문구 내용이 아니라 배치**(응답 형식 안내가 교훈·soul 뒤 맨 마지막에 오는지)이므로,
# 프롬프트 모듈의 상수를 그대로 참조한다.
_RESPONSE_FORMAT_REMINDER = _PROMPTS._RESPONSE_FORMAT_REMINDER


def test_location_prompt_distinguishes_reverse_lookup_from_scene_description():
    prompt = _PROMPTS._DEFAULT_SYSTEM_PROMPT

    strategy = prompt.split("[도구 선택 전략]", 1)[1].split("2. **상태 조회**", 1)[0]
    assert '"로봇이 어디 있어?", "여기가 어디야?", "사방 둘러보고 위치 알려줘"' not in strategy
    assert '"여기가 어디야?"' in strategy
    assert "identify_location" in prompt
    assert "describe_surroundings" in prompt
    assert "adaptive_pick_object" in prompt
    assert "approach_target_distance_m" in prompt
    assert "restore_head_after" in prompt
    assert "use_base_camera_localize" not in prompt
    assert "prepare_before_observe" not in prompt
    assert "prepare_after_rotate" not in prompt
    assert "retry_on_empty_grasp" not in prompt

    rules = prompt.split("[파라미터 규칙]", 1)[1].split("[자동 전송 규칙]", 1)[0]
    numbers = [int(value) for value in re.findall(r"^([0-9]+)\. ", rules, re.MULTILINE)]
    assert numbers == list(range(1, 52))


class _Param:
    value = "moveit"


def _fake_list_skills(_self, include_internal=True):
    skills = [{"name": "navigate_to", "description": "이동"}]
    if include_internal:
        skills.append({"name": "internal_dummy", "description": "내부"})
    return skills


class _FakeNode:
    def __init__(self):
        self._system_prompt_base_cache = None
        self._system_prompt = "Base {skill_list}"
        self._skills = type(
            "Skills",
            (),
            {"list_skills": _fake_list_skills},
        )()
        self._skills_guide = "Guide"
        self._script_skills_guide = ""
        self._robot_limits = ""
        self._troubleshooting_guide = ""
        self._agent_workspace_dir = ""
        self._startup_knowledge_ctx = ""
        self._robot_soul = "Soul"

    def get_parameter(self, _name):
        return _Param()

    def _skill_lessons_section(self):
        return "[학습된 스킬 교훈]\n[navigate_to] 목적지는 target_name 사용"


def test_response_format_reminder_is_final_after_lessons_and_soul():
    prompt = assemble_system_prompt(_FakeNode())

    assert "Soul" in prompt
    assert "[학습된 스킬 교훈]" in prompt
    # 응답 형식 안내 블록이 **맨 마지막**에 통째로 붙어 있어야 한다(문구 무관).
    assert prompt.rstrip().endswith(_RESPONSE_FORMAT_REMINDER.rstrip())
    assert prompt.rfind("[응답 형식 (최종 확인") > prompt.rfind("[학습된 스킬 교훈]")
    assert "internal_dummy" not in prompt


def test_compact_schema_summary_preserves_call_contract():
    schema = {
        "type": "object",
        "properties": {
            "target_pose": {"type": "object"},
            "mode": {"type": "string", "enum": ["patrol", "goal"], "default": "patrol"},
        },
        "oneOf": [{"required": ["target_pose"]}, {"required": ["mode"]}],
        "required": ["mode"],
    }

    summary = compact_schema_summary(schema)

    assert "target_pose(object)" in summary
    assert "mode(string, 필수, 기본='patrol', enum=patrol/goal)" in summary
    assert "조건: target_pose 또는 mode" in summary
    assert len(summary) < len(str(schema))


def test_prompt_compact_mode_omits_raw_schema():
    node = _FakeNode()
    node._skills = type(
        "Skills",
        (),
        {
            "list_skills": lambda self, include_internal=True: [
                {
                    "name": "grasp",
                    "description": "파지",
                    "input_schema": {
                        "type": "object",
                        "properties": {"target_pose": {"type": "object"}},
                        "required": ["target_pose"],
                    },
                }
            ]
        },
    )()
    node._compact_skill_prompt = True

    prompt = assemble_static_prompt_base(node)

    assert "인자: target_pose(object, 필수)" in prompt
    assert "MCP 입력 JSON Schema" not in prompt

    detailed_node = _FakeNode()
    detailed_node._skills = node._skills
    detailed_node._compact_skill_prompt = False
    detailed_prompt = assemble_static_prompt_base(detailed_node)
    assert "MCP 입력 JSON Schema" in detailed_prompt
    assert len(prompt) < len(detailed_prompt)
