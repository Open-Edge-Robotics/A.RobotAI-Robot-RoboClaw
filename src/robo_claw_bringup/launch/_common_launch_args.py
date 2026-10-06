"""robo_claw.launch.py / robo_claw_sim.launch.py 가 공유하는 DeclareLaunchArgument.

두 launch 파일에 걸쳐 동일한 이름/기본값/description 으로 선언되던 인자들을
한 곳에서 관리한다. 한쪽에서만 의미가 있거나 기본값이 다른 인자
(use_sim_time, use_channel, run_core, run_manipulator, sim_engine, use_nav2,
SLAM/map, vision, gRPC client target_* 등)는 각 launch 파일에서 개별 선언한다.
"""

from launch.actions import DeclareLaunchArgument


def common_launch_arguments():
    """LLM/RAG/메신저/HTTP/파일경로/카메라-센서 토픽 등 공통 인자 목록 반환.

    반환되는 DeclareLaunchArgument 객체는 두 launch 파일에서 동일하게 사용되므로
    기본값/설명을 변경할 때는 두 파일 모두에 영향을 준다는 점에 유의.
    """
    return [
        # --- LLM ---
        DeclareLaunchArgument(
            "llm_provider",
            default_value="azure",
            description="LLM 제공자: ollama | openai | azure | anthropic",
        ),
        DeclareLaunchArgument(
            "llm_model",
            default_value="gpt-4.1",
            description="LLM 모델 이름",
        ),
        DeclareLaunchArgument(
            "llm_embedding_model",
            default_value="",
            description="임베딩 모델 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "llm_embedding_provider",
            default_value="",
            description="임베딩 전용 provider 오버라이드. 비우면 메인 LLM provider를 사용",
        ),
        DeclareLaunchArgument(
            "llm_embedding_base_url",
            default_value="",
            description="임베딩 전용 엔드포인트 URL 오버라이드",
        ),
        DeclareLaunchArgument(
            "llm_embedding_api_key",
            default_value="",
            description="임베딩 전용 API 키 오버라이드",
        ),
        DeclareLaunchArgument(
            "llm_base_url",
            default_value="",
            description="LLM 엔드포인트 URL. Ollama 사용 시 설정 (예: http://localhost:11434)",
        ),
        DeclareLaunchArgument(
            "ollama_options_json",
            default_value="{}",
            description="Ollama 추가 생성 옵션 JSON 문자열",
        ),
        # --- LLM 키 ---
        DeclareLaunchArgument("azure_endpoint", default_value=""),
        DeclareLaunchArgument("azure_api_key", default_value=""),
        DeclareLaunchArgument("openai_api_key", default_value=""),
        DeclareLaunchArgument("anthropic_api_key", default_value=""),
        # --- 에이전트 / 프롬프트 파일 ---
        DeclareLaunchArgument(
            "agent_id",
            default_value="robo_claw_agent",
            description="에이전트 식별자",
        ),
        DeclareLaunchArgument(
            "agent_workspace_dir",
            default_value="",
            description="에이전트 작업 공간 폴더 경로. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "robot_soul_file",
            default_value="",
            description="ROBOT.md 절대 경로. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "skills_guide_file",
            default_value="",
            description="SKILLS.md 절대 경로. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "robot_limits_file",
            default_value="",
            description="ROBOT_LIMITS.json 절대 경로. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "troubleshooting_guide_file",
            default_value="",
            description="TROUBLESHOOTING.md 절대 경로. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "system_prompt_file",
            default_value="",
            description="커스텀 시스템 프롬프트 파일 절대 경로. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "compact_skill_prompt",
            default_value="true",
            description="스킬 schema를 프롬프트에 compact 형식으로 주입할지 여부",
        ),
        DeclareLaunchArgument(
            "butler_script_dir",
            default_value="/ros2_ws/butler_scripts",
            description="Butler 로봇 전용 스크립트 디렉토리 경로",
        ),
        # --- RAG / Qdrant / 스킬 자가학습 ---
        DeclareLaunchArgument(
            "enable_rag",
            default_value="",
            description="RAG 활성화 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "rag_vector_backend",
            default_value="",
            description="RAG 벡터 저장소 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "rag_top_k",
            default_value="",
            description="RAG top-k 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "rag_score_threshold",
            default_value="",
            description="RAG score threshold 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "enable_skill_learning",
            default_value="",
            description="스킬 자가학습 수집 활성화 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "skill_learning_success_sample_rate",
            default_value="",
            description="스킬 성공 경험 샘플링 비율 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "skill_learning_reflect_interval_sec",
            default_value="",
            description="스킬 교훈 자동 추출 간격(초) 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "qdrant_url",
            default_value="",
            description="Qdrant URL 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "qdrant_api_key",
            default_value="",
            description="Qdrant API 키 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "qdrant_collection",
            default_value="",
            description="Qdrant 컬렉션 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "qdrant_timeout_sec",
            default_value="",
            description="Qdrant 타임아웃 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        # --- 태스크 큐 / 복합 명령 자동 분해 ---
        DeclareLaunchArgument(
            "task_queue_max_size",
            default_value="",
            description="태스크 큐 최대 대기열 크기 오버라이드(0=큐 비활성화). 비우면 코드 기본값(8) 사용",
        ),
        DeclareLaunchArgument(
            "llm_fail_fast",
            default_value="",
            description="시작 시 LLM 검증/헬스체크 실패 시 기동 중단 오버라이드. 비우면 코드 기본값(false) 사용",
        ),
        DeclareLaunchArgument(
            "strict_config",
            default_value="",
            description="필수 설정 파일 누락 시 기동 중단 오버라이드. 비우면 코드 기본값(false) 사용",
        ),
        DeclareLaunchArgument(
            "enable_task_decomposition",
            default_value="",
            description="복합 명령 자동 분해 활성화 오버라이드. 비우면 코드 기본값(true) 사용",
        ),
        DeclareLaunchArgument(
            "task_decomposition_max_steps",
            default_value="",
            description="복합 명령 분해 최대 단계 수 오버라이드. 비우면 코드 기본값(6) 사용",
        ),
        DeclareLaunchArgument(
            "task_step_max_retries",
            default_value="",
            description="분해된 하위 단계 실패 시 재시도 횟수 오버라이드. 비우면 코드 기본값(1) 사용",
        ),
        DeclareLaunchArgument(
            "task_decomposition_wait_margin_cap_sec",
            default_value="",
            description="복합 명령 액션 대기 여유 시간 상한(초) 오버라이드. 비우면 코드 기본값(1800.0) 사용",
        ),
        # --- HTTP 채널 ---
        DeclareLaunchArgument(
            "http_port",
            default_value="8080",
            description="HTTP 채널 포트",
        ),
        DeclareLaunchArgument(
            "http_host",
            default_value="127.0.0.1",
            description="HTTP 채널 바인드 주소",
        ),
        DeclareLaunchArgument(
            "http_readonly_token",
            default_value="",
            description="HTTP 읽기 전용 토큰",
        ),
        DeclareLaunchArgument(
            "http_control_token",
            default_value="",
            description="HTTP 제어 토큰",
        ),
        DeclareLaunchArgument(
            "http_allowed_cidrs_json",
            default_value='["127.0.0.1/32", "::1/128"]',
            description="HTTP 허용 CIDR JSON 리스트",
        ),
        DeclareLaunchArgument(
            "http_rate_limit_per_minute",
            default_value="60",
            description="HTTP 분당 요청 제한",
        ),
        DeclareLaunchArgument(
            "http_allowed_skills_json",
            default_value="[]",
            description="HTTP로 허용할 skill 이름 JSON 리스트",
        ),
        DeclareLaunchArgument(
            "http_blocked_skills_json",
            default_value='["delete_file", "emergency_stop", "rag_delete", "rag_reindex"]',
            description="HTTP에서 차단할 skill 이름 JSON 리스트",
        ),
        # --- Dashboard 웹 노드 ---
        DeclareLaunchArgument(
            "dashboard_host",
            default_value="127.0.0.1",
            description="대시보드 웹 노드 바인드 주소",
        ),
        DeclareLaunchArgument(
            "dashboard_port",
            default_value="9090",
            description="대시보드 웹 노드 HTTP 포트 (HTTP 채널 포트 8080과 구분)",
        ),
        # --- MCP(Model Context Protocol) 서버 연동 ---
        DeclareLaunchArgument(
            "mcp_enabled",
            default_value="",
            description="MCP 사용 여부 오버라이드. 비우면 agent.yaml 값을 사용",
        ),
        DeclareLaunchArgument(
            "mcp_servers_json",
            default_value="",
            description="MCP 서버 설정 JSON 배열 (mcp_adapter.MCPManager.load_servers 형식)",
        ),
        # --- 메신저 ---
        DeclareLaunchArgument("enable_discord", default_value="true"),
        DeclareLaunchArgument("enable_telegram", default_value="false"),
        DeclareLaunchArgument("enable_slack", default_value="false"),
        DeclareLaunchArgument("enable_grpc", default_value="true"),
        DeclareLaunchArgument(
            "grpc_peer_token",
            default_value="",
            description="로봇 간 gRPC 인증 토큰. 비어 있으면 인증하지 않음",
        ),
        DeclareLaunchArgument(
            "grpc_peer_tokens_json",
            default_value="{}",
            description="peer_name별 gRPC 인증 토큰 JSON object",
        ),
        DeclareLaunchArgument("grpc_max_file_bytes", default_value="67108864"),
        DeclareLaunchArgument(
            "grpc_dedup_path",
            default_value="/tmp/robo_claw_peer_dedup.sqlite3",
        ),
        DeclareLaunchArgument("telegram_token", default_value=""),
        DeclareLaunchArgument("slack_app_token", default_value=""),
        DeclareLaunchArgument("slack_bot_token", default_value=""),
        DeclareLaunchArgument("discord_token", default_value=""),
        # --- 로봇 설정 / 센서 토픽 ---
        DeclareLaunchArgument(
            "robot_config",
            default_value="sim",
            description="로봇 설정 이름 (stretch3 | former | butler | cloid | sim)",
        ),
        DeclareLaunchArgument(
            "camera_topic",
            default_value="",
            description="카메라 토픽 오버라이드. 비우면 robot_config 설정을 사용",
        ),
        DeclareLaunchArgument(
            "depth_topic",
            default_value="",
            description="정렬된 깊이 이미지 토픽 오버라이드. 비우면 robot_config 설정을 사용",
        ),
        DeclareLaunchArgument(
            "camera_info_topic",
            default_value="",
            description="카메라 인트린식(CameraInfo) 토픽 오버라이드. 비우면 robot_config 설정을 사용",
        ),
        DeclareLaunchArgument(
            "pointcloud_topic",
            default_value="",
            description="실제 깊이 포인트클라우드 토픽 오버라이드 (Nav2 relay 입력). 비우면 robot_config 설정을 사용",
        ),
        DeclareLaunchArgument(
            "gripper_camera_topic",
            default_value="",
            description="Stretch3 그리퍼 RGB 카메라 토픽 오버라이드",
        ),
        DeclareLaunchArgument(
            "gripper_depth_topic",
            default_value="",
            description="Stretch3 그리퍼 정렬 depth 이미지 토픽 오버라이드",
        ),
        DeclareLaunchArgument(
            "gripper_camera_info_topic",
            default_value="",
            description="Stretch3 그리퍼 depth CameraInfo 토픽 오버라이드",
        ),
        DeclareLaunchArgument(
            "gripper_pointcloud_topic",
            default_value="",
            description="Stretch3 그리퍼 pointcloud 토픽 오버라이드",
        ),
        DeclareLaunchArgument(
            "use_depth_costmap",
            default_value="false",
            description="실제 깊이 카메라 포인트클라우드를 Nav2 costmap 장애물원으로 사용할지 여부 "
            "(topic_tools relay 노드 실행 + nav2_params.yaml의 depth_obstacle_layer 활성화 전제)",
        ),
        DeclareLaunchArgument(
            "lidar_topic",
            default_value="",
            description="자가진단용 라이다 토픽 오버라이드. 비우면 robot_config 설정을 사용",
        ),
        DeclareLaunchArgument(
            "imu_topic",
            default_value="",
            description="자가진단용 IMU 토픽 오버라이드. 비우면 robot_config 설정을 사용",
        ),
        # --- gRPC 서버 ---
        DeclareLaunchArgument(
            "use_grpc",
            default_value="false",
            description="robo_claw_grpc 노드 실행 여부 (true | false)",
        ),
    ]
