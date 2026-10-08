"""AgentNode 파라미터 선언 및 로드.

- ``declare_agent_parameters``: ROS2 파라미터를 선언만 한다 (읽지 않음).
- ``read_agent_parameters``: 선언된 파라미터를 읽어 ``AgentParams`` 구조체로 반환한다.

manipulation_* 파라미터는 여기서 선언만 하고, 실제 값은 manipulation 스킬이
직접 읽으므로 ``AgentParams`` 에는 포함하지 않는다.
"""

from dataclasses import dataclass, field


@dataclass
class AgentParams:
    """AgentNode 초기화에 사용하는 파라미터 값 모음."""

    agent_id: str = ""
    llm_provider: str = ""
    llm_model: str = ""
    llm_embedding: str = ""
    llm_base_url: str = ""
    llm_embedding_provider: str = ""
    llm_embedding_base_url: str = ""
    llm_embedding_api_key: str = ""
    azure_endpoint: str = ""
    azure_key: str = ""
    openai_key: str = ""
    anthropic_key: str = ""
    ollama_options_json: str = "{}"
    llm_timeout_sec: float = 120.0
    camera_topic: str = ""
    depth_topic: str = ""
    camera_info_topic: str = ""
    gripper_camera_topic: str = ""
    gripper_depth_topic: str = ""
    gripper_camera_info_topic: str = ""
    gripper_pointcloud_topic: str = ""
    lidar_topic: str = "/scan"
    imu_topic: str = "/imu"
    llm_fail_fast: bool = False

    # 헤드 카메라 upright 회전 보정(option 2) 시 depth 파이프라인 역회전 각(도).
    # 0.0 = 보정 없음. upright_rotater(target_x=-1.5708=-90°) 사용 시 -90.0(또는
    # 검증 결과에 따라 90.0). 방향은 로봇에서 픽셀 검증 후 설정(docs 참고).
    head_image_rotate_deg: float = 0.0

    # 헤드 카메라 파지 보조(dual-camera grasp). docs/DUAL_CAMERA_GRASP.md 참조.
    head_detections_topic: str = ""
    head_assist_pose: str = ""
    # ArUco 차분 보정. aruco 노드(stretch_core detect_aruco_markers)가 미실행이면
    # 자동으로 offset 0으로 동작하므로 기본값 그대로 두어도 안전하다.
    aruco_marker_frame_pairs_json: str = ""
    aruco_marker_max_age_sec: float = 1.5
    aruco_max_residual_m: float = 0.10

    memory_path: str | None = None
    memory_backend: str = "json"
    rag_vector_backend: str = "local"
    rag_top_k: int = 5
    rag_score_threshold: float = 0.55
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = ""
    qdrant_timeout_sec: float = 5.0
    rag_local_mirror: bool = True
    multiturn_n: int = 5
    enable_rag: bool = False

    enable_skill_learning: bool = False
    skill_learning_success_sample_rate: float = 0.1
    skill_learning_reflect_interval_sec: float = 0.0

    prompt_file: str | None = None
    compact_skill_prompt: bool = True
    soul_file: str | None = None
    skills_guide_file: str | None = None
    robot_limits_file: str | None = None
    troubleshooting_guide_file: str | None = None
    butler_script_dir: str | None = None
    agent_workspace_dir: str | None = None
    skill_modules: list[str] = field(default_factory=list)

    map_frame: str = "map"
    robot_base_frame: str = "base_link"
    mcp_enabled: bool = False
    mcp_servers_json: str = ""

    # 공통 스킬 정책 (HTTP /task, /skill, gRPC, 메신저, ROS service 공통 적용)
    skill_allowed_json: str = "[]"
    skill_blocked_json: str = "[]"

    # 설정 파일 로딩 strict 모드 (True 시 필수 파일 누락이 기동 실패)
    strict_config: bool = False

    # 태스크 큐 최대 대기열 크기 (0 = 큐 비활성화, 즉시 병렬 실행)
    task_queue_max_size: int = 8

    # 복합 명령 자동 분해(Task Decomposition) — SLM이 여러 절이 이어진 명령을
    # 한 번에 처리하지 못하는 문제를 보완하기 위해 복합 명령을 하위 지시문으로
    # 분해해 순차 실행한다.
    enable_task_decomposition: bool = True
    task_decomposition_max_steps: int = 6
    task_step_max_retries: int = 1
    # ExecuteTask 액션 완료 대기 여유 시간 상한(초). 복합 명령은 단계 수 × 재시도에
    # 비례해 여유를 늘리되 이 값을 넘지 않는다(오작동 시 무한정 대기 방지).
    task_decomposition_wait_margin_cap_sec: float = 1800.0


def declare_agent_parameters(node) -> None:
    """AgentNode 의 모든 ROS2 파라미터를 선언한다."""
    # 공통/식별
    node.declare_parameter("agent_id", "robo_claw_agent")

    # LLM / 임베딩
    node.declare_parameter("llm_provider", "azure")
    node.declare_parameter("llm_model", "gpt-4.1")
    node.declare_parameter("llm_embedding_model", "text-embedding-3-small")
    node.declare_parameter("llm_base_url", "")
    node.declare_parameter("llm_embedding_provider", "")
    node.declare_parameter("llm_embedding_base_url", "")
    node.declare_parameter("llm_embedding_api_key", "")
    node.declare_parameter("ollama_options_json", "{}")
    node.declare_parameter("llm_timeout_sec", 120.0)
    node.declare_parameter("azure_openai_endpoint", "")
    node.declare_parameter("azure_openai_api_key", "")
    node.declare_parameter("openai_api_key", "")
    node.declare_parameter("anthropic_api_key", "")

    # RAG / 메모리
    node.declare_parameter("enable_rag", False)
    node.declare_parameter("memory_path", "")
    node.declare_parameter("memory_backend", "json")
    node.declare_parameter("rag_vector_backend", "local")
    node.declare_parameter("rag_top_k", 5)
    node.declare_parameter("rag_score_threshold", 0.55)
    node.declare_parameter("qdrant_url", "")
    node.declare_parameter("qdrant_api_key", "")
    node.declare_parameter("qdrant_collection", "robo_claw_knowledge")
    node.declare_parameter("qdrant_timeout_sec", 5.0)
    node.declare_parameter("rag_local_mirror", True)
    node.declare_parameter("multiturn_history_n", 5)

    # 스킬 자가 학습
    node.declare_parameter("enable_skill_learning", False)
    node.declare_parameter("skill_learning_success_sample_rate", 0.1)
    node.declare_parameter("skill_learning_reflect_interval_sec", 0.0)

    # 프롬프트/가이드 파일
    node.declare_parameter("system_prompt_file", "")
    node.declare_parameter("compact_skill_prompt", True)
    node.declare_parameter("robot_soul_file", "")
    node.declare_parameter("skills_guide_file", "")
    node.declare_parameter("robot_limits_file", "")
    node.declare_parameter("troubleshooting_guide_file", "")
    node.declare_parameter("skill_modules", ["robo_claw_agent.skills.navigation_skill"])
    node.declare_parameter("cloid_motion_catalog_file", "")
    node.declare_parameter("cloid_cleanup_indicator_enabled", False)
    node.declare_parameter("cloid_cleanup_indicator_motion_ids_json", "[]")
    node.declare_parameter("butler_script_dir", "/ros2_ws/butler_scripts")
    node.declare_parameter("agent_workspace_dir", "/ros2_ws/agent_workspace")

    # 좌표 프레임 / MCP
    node.declare_parameter("camera_topic", "")
    node.declare_parameter("depth_topic", "")
    node.declare_parameter("camera_info_topic", "")
    node.declare_parameter("gripper_camera_topic", "")
    node.declare_parameter("gripper_depth_topic", "")
    node.declare_parameter("gripper_camera_info_topic", "")
    node.declare_parameter("gripper_pointcloud_topic", "")
    node.declare_parameter("lidar_topic", "/scan")
    node.declare_parameter("imu_topic", "/imu")
    node.declare_parameter("cmd_vel_topic", "")
    # 헤드 카메라 upright 보정 시 depth 역회전 각(도). 0.0=보정 없음.
    node.declare_parameter("head_image_rotate_deg", 0.0)
    # 헤드 카메라 파지 보조 / ArUco 차분 보정.
    node.declare_parameter("head_detections_topic", "")
    node.declare_parameter("head_assist_pose", "")
    node.declare_parameter("aruco_marker_frame_pairs_json", "")
    node.declare_parameter("aruco_marker_max_age_sec", 1.5)
    node.declare_parameter("aruco_max_residual_m", 0.10)
    node.declare_parameter("map_frame", "map")
    node.declare_parameter("robot_base_frame", "base_link")
    node.declare_parameter("mcp_enabled", False)
    node.declare_parameter("mcp_servers_json", "")

    # manipulation (값은 manipulation 스킬이 직접 읽음)
    node.declare_parameter("manipulation_enabled", True)
    node.declare_parameter("manipulation_backend", "moveit")
    node.declare_parameter("manipulation_arm_group", "arm")
    node.declare_parameter("manipulation_gripper_group", "gripper")
    node.declare_parameter("manipulation_end_effector_link", "")
    node.declare_parameter("manipulation_base_frame", "base_link")
    node.declare_parameter("manipulation_tool_frame", "")
    node.declare_parameter(
        "manipulation_named_poses_json",
        '{"home":"home","ready":"ready","carry":"carry","stow":"stow"}',
    )
    node.declare_parameter("manipulation_gripper_presets_json", "{}")
    node.declare_parameter("manipulation_planning_pipeline", "")
    node.declare_parameter("manipulation_planner_id", "")
    node.declare_parameter("manipulation_cartesian_step", 0.01)
    node.declare_parameter("manipulation_velocity_scaling", 0.2)
    node.declare_parameter("manipulation_acceleration_scaling", 0.2)
    node.declare_parameter("manipulation_default_approach_distance_m", 0.1)
    node.declare_parameter("manipulation_default_retreat_distance_m", 0.1)
    node.declare_parameter("manipulation_cmd_vel_topic", "")

    # 공통 스킬 정책
    node.declare_parameter("skill_allowed_json", "[]")
    node.declare_parameter("skill_blocked_json", "[]")

    # LLM 시작 검증
    node.declare_parameter("llm_fail_fast", False)

    # 설정 파일 strict 모드
    node.declare_parameter("strict_config", False)

    # 태스크 큐 최대 대기열 크기
    node.declare_parameter("task_queue_max_size", 8)

    # 복합 명령 자동 분해(Task Decomposition)
    node.declare_parameter("enable_task_decomposition", True)
    node.declare_parameter("task_decomposition_max_steps", 6)
    node.declare_parameter("task_step_max_retries", 1)
    node.declare_parameter("task_decomposition_wait_margin_cap_sec", 1800.0)


def _s(node, name: str) -> str:
    return node.get_parameter(name).get_parameter_value().string_value


def _i(node, name: str) -> int:
    return node.get_parameter(name).get_parameter_value().integer_value


def _d(node, name: str) -> float:
    return node.get_parameter(name).get_parameter_value().double_value


def _b(node, name: str) -> bool:
    return node.get_parameter(name).get_parameter_value().bool_value


def read_agent_parameters(node) -> AgentParams:
    """선언된 파라미터를 읽어 ``AgentParams`` 로 반환한다."""
    return AgentParams(
        agent_id=_s(node, "agent_id"),
        llm_provider=_s(node, "llm_provider"),
        llm_model=_s(node, "llm_model"),
        llm_embedding=_s(node, "llm_embedding_model"),
        llm_base_url=_s(node, "llm_base_url"),
        llm_embedding_provider=_s(node, "llm_embedding_provider"),
        llm_embedding_base_url=_s(node, "llm_embedding_base_url"),
        llm_embedding_api_key=_s(node, "llm_embedding_api_key"),
        azure_endpoint=_s(node, "azure_openai_endpoint"),
        azure_key=_s(node, "azure_openai_api_key"),
        openai_key=_s(node, "openai_api_key"),
        anthropic_key=_s(node, "anthropic_api_key"),
        ollama_options_json=_s(node, "ollama_options_json"),
        llm_timeout_sec=_d(node, "llm_timeout_sec"),
        camera_topic=_s(node, "camera_topic"),
        depth_topic=_s(node, "depth_topic"),
        camera_info_topic=_s(node, "camera_info_topic"),
        gripper_camera_topic=_s(node, "gripper_camera_topic"),
        gripper_depth_topic=_s(node, "gripper_depth_topic"),
        gripper_camera_info_topic=_s(node, "gripper_camera_info_topic"),
        gripper_pointcloud_topic=_s(node, "gripper_pointcloud_topic"),
        lidar_topic=_s(node, "lidar_topic"),
        imu_topic=_s(node, "imu_topic"),
        head_image_rotate_deg=_d(node, "head_image_rotate_deg"),
        head_detections_topic=_s(node, "head_detections_topic"),
        head_assist_pose=_s(node, "head_assist_pose"),
        aruco_marker_frame_pairs_json=_s(node, "aruco_marker_frame_pairs_json"),
        aruco_marker_max_age_sec=_d(node, "aruco_marker_max_age_sec"),
        aruco_max_residual_m=_d(node, "aruco_max_residual_m"),
        memory_path=_s(node, "memory_path") or None,
        memory_backend=_s(node, "memory_backend"),
        rag_vector_backend=_s(node, "rag_vector_backend"),
        rag_top_k=_i(node, "rag_top_k"),
        rag_score_threshold=_d(node, "rag_score_threshold"),
        qdrant_url=_s(node, "qdrant_url"),
        qdrant_api_key=_s(node, "qdrant_api_key"),
        qdrant_collection=_s(node, "qdrant_collection"),
        qdrant_timeout_sec=_d(node, "qdrant_timeout_sec"),
        rag_local_mirror=_b(node, "rag_local_mirror"),
        multiturn_n=_i(node, "multiturn_history_n"),
        enable_rag=_b(node, "enable_rag"),
        enable_skill_learning=_b(node, "enable_skill_learning"),
        skill_learning_success_sample_rate=_d(node, "skill_learning_success_sample_rate"),
        skill_learning_reflect_interval_sec=_d(node, "skill_learning_reflect_interval_sec"),
        prompt_file=_s(node, "system_prompt_file") or None,
        compact_skill_prompt=_b(node, "compact_skill_prompt"),
        soul_file=_s(node, "robot_soul_file") or None,
        skills_guide_file=_s(node, "skills_guide_file") or None,
        robot_limits_file=_s(node, "robot_limits_file") or None,
        troubleshooting_guide_file=_s(node, "troubleshooting_guide_file") or None,
        butler_script_dir=_s(node, "butler_script_dir") or None,
        agent_workspace_dir=_s(node, "agent_workspace_dir") or None,
        skill_modules=list(
            node.get_parameter("skill_modules").get_parameter_value().string_array_value
        ),
        map_frame=_s(node, "map_frame") or "map",
        robot_base_frame=_s(node, "robot_base_frame") or "base_link",
        mcp_enabled=_b(node, "mcp_enabled"),
        mcp_servers_json=_s(node, "mcp_servers_json"),
        skill_allowed_json=_s(node, "skill_allowed_json"),
        skill_blocked_json=_s(node, "skill_blocked_json"),
        llm_fail_fast=_b(node, "llm_fail_fast"),
        strict_config=_b(node, "strict_config"),
        task_queue_max_size=_i(node, "task_queue_max_size"),
        enable_task_decomposition=_b(node, "enable_task_decomposition"),
        task_decomposition_max_steps=_i(node, "task_decomposition_max_steps"),
        task_step_max_retries=_i(node, "task_step_max_retries"),
        task_decomposition_wait_margin_cap_sec=_d(node, "task_decomposition_wait_margin_cap_sec"),
    )
