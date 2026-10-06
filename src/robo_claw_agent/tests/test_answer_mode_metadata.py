"""스킬 ``answer_mode`` 메타데이터 계약 테스트 (AST 기반, ROS import 없음).

``answer_mode`` 는 "스킬 성공 후 사용자 답변을 어떻게 만드는가"를 결정한다.
- ``action``(기본): 스킬 message 를 그대로 보고. 물리 동작 스킬은 추가 LLM 라운드가
  두 번째 물리 동작을 계획할 위험이 있어 기본값으로 기존 동작을 보존한다.
- ``informational``: 결과 데이터를 LLM에 넘겨 최종 답변을 합성한다.

목록이 조용히 늘어나면 물리 동작 스킬에 요약 라운드가 붙을 수 있으므로, 선언 집합을
테스트로 고정한다.
"""

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

SKILLS_ROOT = pathlib.Path(__file__).resolve().parents[1] / "robo_claw_agent" / "skills"

# 결과 데이터를 사용자에게 정리해 답해야 하는 정보성 스킬 집합(계약).
EXPECTED_INFORMATIONAL = frozenset(
    {
        # 시스템/지식
        "get_status",
        "get_datetime",
        "ros_command",
        "list_topics",
        "get_location",
        "identify_location",
        "list_cloid_motions",
        "rag_search",
        "rag_list",
        "rag_status",
        # 파일/스크립트
        "list_files",
        "read_text_file",
        "analyze_stored_file",
        "list_butler_scripts",
        # 협업 조회
        "list_peer_robots",
        "query_peer_status",
        "query_peer_capabilities",
        # 인식/센서
        "get_detections",
        "detect_object",
        "find_object",
        "get_distance",
        "scan_room",
        "describe_surroundings",
        "analyze_scene",
        "annotate_image",
        "capture_camera_image",
        "estimate_gripper_object_pose",
        "observe_head_target",
        "search_object",
        "classify_object_surface",
        # 맵
        "analyze_map",
        "find_reachable_places",
        "capture_map",
        "get_map_visual",
        "annotate_map",
    }
)

# 물리적 부작용이 있거나 백그라운드로 도는 스킬은 절대 정보성으로 분류하면 안 된다.
MUST_STAY_ACTION = frozenset(
    {
        "navigate_to",
        "move_relative",
        "rotate",
        "face_direction",
        "approach_object",
        "follow_waypoints",
        "patrol",
        "explore",
        "reactive_navigate",
        "condition_reactive",
        "autonomous_act",
        "autonomous_cooperate",
        "grasp",
        "place",
        "adaptive_pick_object",
        "vla_pick_front_object",
        "vla_pick_gripper_object",
        "pick_front_object",
        "pick_from_right_side_zone",
        "prepare_right_side_pick",
        "servo_gripper_to_object",
        "open_gripper",
        "close_gripper",
        "arm_pose",
        "move_joints",
        "move_pose",
        "emergency_stop",
        "reset_emergency_stop",
        "stow_for_navigation",
        "switch_stretch_mode",
        "monitor_detection",
        "send_message",
        "say",
        "run_script",
        "run_butler_script",
        "delete_file",
        "write_text_file",
        "edit_text_file",
    }
)


def _declared_modes() -> dict[str, str]:
    modes: dict[str, str] = {}
    for path in sorted(SKILLS_ROOT.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            name_value = None
            for statement in node.body:
                if isinstance(statement, ast.Assign):
                    targets = [t.id for t in statement.targets if isinstance(t, ast.Name)]
                    if "name" in targets:
                        try:
                            name_value = ast.literal_eval(statement.value)
                        except (ValueError, TypeError):
                            name_value = None
                    elif "answer_mode" in targets:
                        try:
                            modes[name_value or node.name] = str(ast.literal_eval(statement.value))
                        except (ValueError, TypeError):
                            pass
            # answer_mode 미선언 = 기본값 action
            if isinstance(name_value, str) and name_value:
                modes.setdefault(name_value, "action")
    return modes


def test_informational_skill_set_matches_contract():
    modes = _declared_modes()
    informational = {name for name, mode in modes.items() if mode == "informational"}

    assert informational == set(EXPECTED_INFORMATIONAL), (
        f"예상 밖 추가: {sorted(informational - EXPECTED_INFORMATIONAL)} / "
        f"누락: {sorted(set(EXPECTED_INFORMATIONAL) - informational)}"
    )


def test_physical_and_background_skills_are_not_informational():
    modes = _declared_modes()
    offenders = sorted(name for name in MUST_STAY_ACTION if modes.get(name, "action") != "action")
    assert offenders == [], f"물리 동작 스킬이 정보성으로 분류됨: {offenders}"


def test_ros_command_is_informational():
    """message 가 \"실행 완료\"뿐이라 실제 결과가 사라지던 대표 사례."""
    assert _declared_modes()["ros_command"] == "informational"


def test_list_topics_is_informational():
    assert _declared_modes()["list_topics"] == "informational"
