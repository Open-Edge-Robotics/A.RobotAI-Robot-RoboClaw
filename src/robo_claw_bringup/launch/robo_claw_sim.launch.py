import os
import sys

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    IncludeLaunchDescription,
    OpaqueFunction,
    SetEnvironmentVariable,
)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node

# ros2 launch 는 SourceFileLoader 로 이 파일을 로드하며 launch 파일 디렉토리를
# sys.path 에 추가하지 않는다. 동반 헬퍼 모듈(_common_launch_args) import를 위해
# 이 파일의 디렉토리를 명시적으로 sys.path 앞에 넣는다.
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)
from _common_launch_args import common_launch_arguments  # pyright: ignore[reportMissingImports]

# Gazebo 시뮬레이션은 Jazzy(Harmonic, ros_gz_sim)만 지원한다. Humble(Gazebo Classic)은
# 지원하지 않으므로 `sim_engine:=classic` 이나 humble 환경에서 조용히 다른 경로로
# 진행하지 않고 즉시 중단한다.
SUPPORTED_SIM_ENGINES = ("stub",)


def _validate_sim_engine(sim_engine: str) -> None:
    if sim_engine in SUPPORTED_SIM_ENGINES:
        return
    raise RuntimeError(
        f"지원하지 않는 sim_engine 입니다: {sim_engine!r}. "
        "Humble(Gazebo Classic) 시뮬레이션 지원은 제거되었습니다. "
        f"사용 가능: {', '.join(SUPPORTED_SIM_ENGINES)}"
    )


def launch_setup(context, *args, **kwargs):
    sim_engine = LaunchConfiguration("sim_engine").perform(context)
    requested_camera_topic = LaunchConfiguration("camera_topic").perform(context)

    # LLM 및 메신저 설정
    llm_provider = LaunchConfiguration("llm_provider").perform(context)
    llm_model = LaunchConfiguration("llm_model").perform(context)
    use_channel = LaunchConfiguration("use_channel")
    llm_base_url = LaunchConfiguration("llm_base_url").perform(context)
    robot_soul_file = LaunchConfiguration("robot_soul_file").perform(context)
    system_prompt_file = LaunchConfiguration("system_prompt_file").perform(context)
    skills_guide_file = LaunchConfiguration("skills_guide_file").perform(context)
    robot_limits_file = LaunchConfiguration("robot_limits_file").perform(context)
    troubleshooting_guide_file = LaunchConfiguration("troubleshooting_guide_file").perform(context)
    robot_config = LaunchConfiguration("robot_config").perform(context)
    butler_script_dir = LaunchConfiguration("butler_script_dir").perform(context)
    use_grpc = LaunchConfiguration("use_grpc")

    # 추가 설정 (RAG, Ollama 옵션 등)
    agent_id = LaunchConfiguration("agent_id").perform(context)
    llm_embedding_model = LaunchConfiguration("llm_embedding_model").perform(context)
    llm_embedding_provider = LaunchConfiguration("llm_embedding_provider").perform(context)
    llm_embedding_base_url = LaunchConfiguration("llm_embedding_base_url").perform(context)
    llm_embedding_api_key = LaunchConfiguration("llm_embedding_api_key").perform(context)
    enable_rag = LaunchConfiguration("enable_rag").perform(context)
    rag_vector_backend = LaunchConfiguration("rag_vector_backend").perform(context)
    rag_top_k = LaunchConfiguration("rag_top_k").perform(context)
    rag_score_threshold = LaunchConfiguration("rag_score_threshold").perform(context)
    enable_skill_learning = LaunchConfiguration("enable_skill_learning").perform(context)
    skill_learning_success_sample_rate = LaunchConfiguration(
        "skill_learning_success_sample_rate"
    ).perform(context)
    skill_learning_reflect_interval_sec = LaunchConfiguration(
        "skill_learning_reflect_interval_sec"
    ).perform(context)
    qdrant_url = LaunchConfiguration("qdrant_url").perform(context)
    qdrant_api_key = LaunchConfiguration("qdrant_api_key").perform(context)
    qdrant_collection = LaunchConfiguration("qdrant_collection").perform(context)
    qdrant_timeout_sec = LaunchConfiguration("qdrant_timeout_sec").perform(context)
    agent_workspace_dir = LaunchConfiguration("agent_workspace_dir").perform(context)
    ollama_options_json = LaunchConfiguration("ollama_options_json").perform(context)

    bringup_share = get_package_share_directory("robo_claw_bringup")
    scenario_file = LaunchConfiguration("scenario_file").perform(context)

    _validate_sim_engine(sim_engine)
    # 지도/카메라는 robo_claw_sim 노드가 직접 발행한다(Gazebo 경로는 제거됨).
    camera_topic_val = "/sim/camera/image_raw"

    if requested_camera_topic:
        camera_topic_val = requested_camera_topic

    nodes_to_launch = []

    if sim_engine == "stub":
        # --- Gazebo 없는 기본 시뮬레이터 ---
        # 운동학 세계 + 센서 합성 + NavigateToPose/Spin/FollowWaypoints 액션 서버를
        # 한 노드에서 제공한다. GPU/distro별 Gazebo 자산이 필요 없다.
        sim_parameters = {
            "scenario_file": scenario_file,
            "backend": LaunchConfiguration("sim_backend"),
            "sim_viz": LaunchConfiguration("sim_viz"),
            "publish_camera": LaunchConfiguration("sim_publish_camera"),
            "camera_topic": camera_topic_val,
            "camera_depth_topic": "/sim/camera/depth/image_raw",
            "viewer": LaunchConfiguration("sim_viewer"),
            "use_sim_time": False,
            "serve_navigation": True,
            "update_rate_hz": LaunchConfiguration("sim_update_rate_hz"),
        }
        nodes_to_launch.append(
            Node(
                package="robo_claw_sim",
                executable="sim_node",
                name="robo_claw_sim_node",
                output="screen",
                parameters=[sim_parameters],
            )
        )

    # RoboClaw 핵심 노드들(agent_node, discovery_node, channel_node 등)을
    # robo_claw.launch.py를 인클루드하여 구동
    robo_claw_launch_file = PathJoinSubstitution([bringup_share, "launch", "robo_claw.launch.py"])

    # stub 엔진의 sim_node 는 실시간(20Hz)으로 동작하고 /clock 을 발행하지 않는다.
    # 이때 하위 스택에 use_sim_time:=true 를 넘기면 ROS 타이머가 전진하지 않아
    # heartbeat 가 30초 뒤 watchdog 에 의해 노드가 종료된다.
    stack_use_sim_time = "false"

    nodes_to_launch.append(
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(robo_claw_launch_file),
            launch_arguments={
                "use_sim_time": stack_use_sim_time,
                "run_core": LaunchConfiguration("run_core"),
                "llm_provider": llm_provider,
                "llm_model": llm_model,
                "llm_tool_calling": LaunchConfiguration("llm_tool_calling"),
                "compact_skill_prompt": LaunchConfiguration("compact_skill_prompt"),
                "agent_id": agent_id,
                "llm_embedding_model": llm_embedding_model,
                "llm_embedding_provider": llm_embedding_provider,
                "llm_embedding_base_url": llm_embedding_base_url,
                "llm_embedding_api_key": llm_embedding_api_key,
                "enable_rag": enable_rag,
                "rag_vector_backend": rag_vector_backend,
                "rag_top_k": rag_top_k,
                "rag_score_threshold": rag_score_threshold,
                "enable_skill_learning": enable_skill_learning,
                "skill_learning_success_sample_rate": skill_learning_success_sample_rate,
                "skill_learning_reflect_interval_sec": skill_learning_reflect_interval_sec,
                "qdrant_url": qdrant_url,
                "qdrant_api_key": qdrant_api_key,
                "qdrant_collection": qdrant_collection,
                "qdrant_timeout_sec": qdrant_timeout_sec,
                "task_queue_max_size": LaunchConfiguration("task_queue_max_size"),
                "llm_fail_fast": LaunchConfiguration("llm_fail_fast"),
                "strict_config": LaunchConfiguration("strict_config"),
                "enable_task_decomposition": LaunchConfiguration("enable_task_decomposition"),
                "task_decomposition_max_steps": LaunchConfiguration("task_decomposition_max_steps"),
                "task_step_max_retries": LaunchConfiguration("task_step_max_retries"),
                "task_decomposition_wait_margin_cap_sec": LaunchConfiguration(
                    "task_decomposition_wait_margin_cap_sec"
                ),
                "agent_workspace_dir": agent_workspace_dir,
                "ollama_options_json": ollama_options_json,
                "llm_base_url": llm_base_url,
                "robot_soul_file": robot_soul_file,
                "skills_guide_file": skills_guide_file,
                "robot_limits_file": robot_limits_file,
                "troubleshooting_guide_file": troubleshooting_guide_file,
                "system_prompt_file": system_prompt_file,
                "butler_script_dir": butler_script_dir,
                "use_channel": use_channel,
                "http_host": LaunchConfiguration("http_host"),
                "http_port": LaunchConfiguration("http_port"),
                "http_readonly_token": LaunchConfiguration("http_readonly_token"),
                "http_control_token": LaunchConfiguration("http_control_token"),
                "http_allowed_cidrs_json": LaunchConfiguration("http_allowed_cidrs_json"),
                "http_rate_limit_per_minute": LaunchConfiguration("http_rate_limit_per_minute"),
                "http_allowed_skills_json": LaunchConfiguration("http_allowed_skills_json"),
                "http_blocked_skills_json": LaunchConfiguration("http_blocked_skills_json"),
                "telegram_token": LaunchConfiguration("telegram_token"),
                "slack_app_token": LaunchConfiguration("slack_app_token"),
                "slack_bot_token": LaunchConfiguration("slack_bot_token"),
                "discord_token": LaunchConfiguration("discord_token"),
                "enable_discord": LaunchConfiguration("enable_discord"),
                "enable_telegram": LaunchConfiguration("enable_telegram"),
                "enable_slack": LaunchConfiguration("enable_slack"),
                "enable_grpc": LaunchConfiguration("enable_grpc"),
                "use_grpc": use_grpc,
                "robot_config": robot_config,
                "camera_topic": camera_topic_val,
                "azure_endpoint": LaunchConfiguration("azure_endpoint"),
                "azure_api_key": LaunchConfiguration("azure_api_key"),
                "openai_api_key": LaunchConfiguration("openai_api_key"),
                "anthropic_api_key": LaunchConfiguration("anthropic_api_key"),
                "grpc_peer_token": LaunchConfiguration("grpc_peer_token"),
                "grpc_peer_tokens_json": LaunchConfiguration("grpc_peer_tokens_json"),
                "grpc_max_file_bytes": LaunchConfiguration("grpc_max_file_bytes"),
                "grpc_dedup_path": LaunchConfiguration("grpc_dedup_path"),
                "mcp_enabled": LaunchConfiguration("mcp_enabled"),
                "mcp_servers_json": LaunchConfiguration("mcp_servers_json"),
                "rag_local_mirror": LaunchConfiguration("rag_local_mirror"),
                "memory_path": LaunchConfiguration("memory_path"),
                "target_peers_json": LaunchConfiguration("target_peers_json"),
                "target_host": LaunchConfiguration("target_host"),
                "target_port": LaunchConfiguration("target_port"),
                "use_client": LaunchConfiguration("use_client"),
                "robot_description_file": LaunchConfiguration("robot_description_file"),
                "depth_topic": LaunchConfiguration("depth_topic"),
                "camera_info_topic": LaunchConfiguration("camera_info_topic"),
                "pointcloud_topic": LaunchConfiguration("pointcloud_topic"),
                "gripper_camera_topic": LaunchConfiguration("gripper_camera_topic"),
                "gripper_depth_topic": LaunchConfiguration("gripper_depth_topic"),
                "gripper_camera_info_topic": LaunchConfiguration("gripper_camera_info_topic"),
                "gripper_pointcloud_topic": LaunchConfiguration("gripper_pointcloud_topic"),
                "use_depth_costmap": LaunchConfiguration("use_depth_costmap"),
                "use_vision": LaunchConfiguration("use_vision"),
                "vision_model_path": LaunchConfiguration("vision_model_path"),
                "vision_max_inference_hz": LaunchConfiguration("vision_max_inference_hz"),
                "use_gripper_vision": LaunchConfiguration("use_gripper_vision"),
                "gripper_vision_max_inference_hz": LaunchConfiguration(
                    "gripper_vision_max_inference_hz"
                ),
                "lidar_topic": LaunchConfiguration("lidar_topic"),
                "imu_topic": LaunchConfiguration("imu_topic"),
                "grpc_port": LaunchConfiguration("grpc_port"),
                "run_manipulator": LaunchConfiguration("run_manipulator"),
                "scan_interval_sec": LaunchConfiguration("scan_interval_sec"),
                "log_interval_sec": LaunchConfiguration("discovery_log_interval_sec"),
            }.items(),
        )
    )

    # 런치 환경 정보 콘솔 출력
    print("\n" + "=" * 50)
    print(" [RoboClaw Sim] 런치 환경 변수(env) 로딩 및 해석 정보")
    print(f"  * 로봇 설정 (robot_config)      : {robot_config}")
    print(f"  * LLM 공급자 (llm_provider)      : {llm_provider}")
    print(f"  * LLM 모델명 (llm_model)         : {llm_model}")
    if llm_base_url:
        print(f"  * Ollama 엔드포인트 (base_url)   : {llm_base_url}")
    if ollama_options_json and ollama_options_json != "{}":
        print(f"  * Ollama 세부 옵션 (options)     : {ollama_options_json}")
    if agent_workspace_dir:
        print(f"  * 에이전트 작업 공간 (workspace) : {agent_workspace_dir}")
    if robot_soul_file:
        print(f"  * 로봇 개성 가이드 (soul)        : {robot_soul_file}")
    if robot_limits_file:
        print(f"  * 로봇 구동 스펙 (limits)        : {robot_limits_file}")
    if troubleshooting_guide_file:
        print(f"  * 장애 조치 가이드 (troubleshoot): {troubleshooting_guide_file}")
    print("=" * 50 + "\n")

    return nodes_to_launch


def generate_launch_description() -> LaunchDescription:
    """시뮬레이션 런치 설명 생성.

    공통 인자는 ``_common_launch_args.common_launch_arguments()`` 에서 가져오고,
    시뮬레이션 전용 인자(use_sim_time=true, robot_model, world, sim_engine, use_nav2,
    use_slam, map, use_channel=true)는 여기서 별도 선언한다.
    """
    return LaunchDescription(
        common_launch_arguments()
        + [
            # --- 시뮬레이션 전용 인자 ---
            DeclareLaunchArgument("use_sim_time", default_value="true"),
            DeclareLaunchArgument("robot_model", default_value="standard"),
            DeclareLaunchArgument("world", default_value="warehouse"),
            DeclareLaunchArgument("sim_engine", default_value="stub"),
            DeclareLaunchArgument(
                "scenario_file",
                default_value="",
                description="sim_engine:=stub 에서 사용할 시나리오 YAML 경로",
            ),
            DeclareLaunchArgument("sim_update_rate_hz", default_value="20.0"),
            DeclareLaunchArgument(
                "sim_publish_camera",
                default_value="false",
                description="시뮬 카메라 RGB-D 발행(내장 검출기/vision 노드 입력용, MUJOCO_GL 필요)",
            ),
            DeclareLaunchArgument(
                "sim_viewer",
                default_value="false",
                description="MuJoCo 대화형 뷰어 사용 여부(backend:=mujoco, DISPLAY 필요)",
            ),
            DeclareLaunchArgument(
                "sim_viz",
                default_value="false",
                description="RViz 시각화용 마커(/sim/world_markers) 발행 여부",
            ),
            DeclareLaunchArgument(
                "sim_backend",
                default_value="kinematic",
                description=(
                    "RoboClaw 전용 시뮬레이터 백엔드: kinematic(기본, 외부 의존성 없음) "
                    "| mujoco(선택, 접촉/카메라 확장 기반)"
                ),
            ),
            DeclareLaunchArgument("use_nav2", default_value="false"),
            DeclareLaunchArgument("use_slam", default_value="false"),
            DeclareLaunchArgument("map", default_value=""),
            DeclareLaunchArgument("run_core", default_value="false"),
            DeclareLaunchArgument("run_manipulator", default_value="false"),
            DeclareLaunchArgument("scan_interval_sec", default_value="10.0"),
            DeclareLaunchArgument("discovery_log_interval_sec", default_value="600.0"),
            DeclareLaunchArgument("memory_path", default_value=""),
            DeclareLaunchArgument("rag_local_mirror", default_value=""),
            DeclareLaunchArgument(
                "robot_description_file",
                default_value="",
                description="URDF 파일 경로. 비우면 robot_config 설정의 robot_description_file을 사용",
            ),
            DeclareLaunchArgument("use_vision", default_value="true"),
            DeclareLaunchArgument(
                "vision_model_path", default_value="/ros2_ws/models/yolov8n.onnx"
            ),
            DeclareLaunchArgument("vision_max_inference_hz", default_value="10.0"),
            DeclareLaunchArgument("use_gripper_vision", default_value="true"),
            DeclareLaunchArgument("gripper_vision_max_inference_hz", default_value="5.0"),
            DeclareLaunchArgument("use_depth_costmap", default_value="false"),
            DeclareLaunchArgument("use_client", default_value="false"),
            DeclareLaunchArgument("target_host", default_value="127.0.0.1"),
            DeclareLaunchArgument("target_port", default_value="50052"),
            DeclareLaunchArgument("target_peers_json", default_value=""),
            DeclareLaunchArgument("grpc_port", default_value="50052"),
            # 시뮬레이션에서는 HTTP 채널을 기본 활성화
            DeclareLaunchArgument(
                "use_channel",
                default_value="true",
                description="HTTP 채널 노드 실행 여부 (true | false)",
            ),
            SetEnvironmentVariable("TURTLEBOT3_MODEL", "waffle"),
            OpaqueFunction(function=launch_setup),
        ]
    )
