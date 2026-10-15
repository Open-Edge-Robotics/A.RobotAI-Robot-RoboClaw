"""플래너가 사용하는 스킬 분류·검색 제외 상수."""



# 자동 RAG 검색에서 제외할 운영/메타 항목 타입.
# 명시적 rag_search(skills/system_skill/rag.py)와 동일하게 적용해,
# 스킬 기록·이동 좌표 로그가 저장된 장소 사실을 밀어내지 않도록 한다.
_RAG_EXCLUDED_TYPES = [
    "skill_episode",
    "skill_lesson",
    "navigated_coordinate",
    "blocked_coordinate",
]

# 읽기 전용 종료형 스킬: 물리적 동작 없이 정보만 반환.
# 성공 시 추가 LLM 추론 라운드를 생략해 토큰 비용을 절감한다.
_READONLY_TERMINAL_SKILLS: frozenset[str] = frozenset(
    {
        "say",
        "get_status",
        "get_distance",
        "capture_camera_image",
        "capture_map",
        "get_map_visual",
        "annotate_map",
        "analyze_map",
        "find_reachable_places",
        "describe_surroundings",
        "detect_object",
        "find_object",
        "scan_room",
        "get_detections",
        "analyze_scene",
        "annotate_image",
        "read_text_file",
        "list_files",
        "get_datetime",
        "rag_search",
        "rag_list",
        "rag_status",
        "log_observation",
        "get_location",
    }
)

# 물리적 동작을 수반하는 종료형 스킬: 실행 전 추가 검증 필요.
_ACTION_TERMINAL_SKILLS: frozenset[str] = frozenset(
    {
        "navigate_to",
        "rotate",
        "stop",
        "emergency_stop",
        "arm_pose",
        "move_joints",
        "move_pose",
        "open_gripper",
        "close_gripper",
        "grasp",
        "place",
        # 자체완결형 복합 조작 스킬 (allow_with_others=False) — 터미널 액션으로 일관 처리
        "adaptive_pick_object",
        "vla_pick_gripper_object",
        "vla_pick_front_object",
        "pick_front_object",
        "pick_from_right_side_zone",
        "prepare_right_side_pick",
        "servo_gripper_to_object",
        "stop_autonomous",
        "stop_patrol",
        "stop_monitor",
        "rag_add",
        "rag_delete",
        "rag_reindex",
        "reflect_skills",
        "run_butler_script",
    }
)

_TERMINAL_SKILLS: frozenset[str] = _READONLY_TERMINAL_SKILLS | _ACTION_TERMINAL_SKILLS
