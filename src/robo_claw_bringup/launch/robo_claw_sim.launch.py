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
from launch_ros.substitutions import FindPackageShare

# ros2 launch 는 SourceFileLoader 로 이 파일을 로드하며 launch 파일 디렉토리를
# sys.path 에 추가하지 않는다. 동반 헬퍼 모듈(_common_launch_args) import를 위해
# 이 파일의 디렉토리를 명시적으로 sys.path 앞에 넣는다.
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)
from _common_launch_args import common_launch_arguments  # pyright: ignore[reportMissingImports]


def launch_setup(context, *args, **kwargs):
    use_sim_time = LaunchConfiguration("use_sim_time")
    robot_model = LaunchConfiguration("robot_model").perform(context)
    world = LaunchConfiguration("world").perform(context)
    sim_engine = LaunchConfiguration("sim_engine").perform(context)
    requested_camera_topic = LaunchConfiguration("camera_topic").perform(context)
    use_nav2 = LaunchConfiguration("use_nav2")
    use_slam = LaunchConfiguration("use_slam")
    map_file = LaunchConfiguration("map")

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

    # 월드 이름에 따른 맵 파일 자동 설정
    if sim_engine == "gz":
        tb4_nav_share = FindPackageShare("turtlebot4_navigation")
        default_map = PathJoinSubstitution([tb4_nav_share, "maps", world + ".yaml"])
        camera_topic_val = "/oakd/rgb/preview/image_raw"
    else:
        tb3_nav_share = FindPackageShare("nav2_bringup")
        default_map = PathJoinSubstitution([tb3_nav_share, "maps", "turtlebot3_world.yaml"])
        camera_topic_val = "/camera/image_raw"

    if requested_camera_topic:
        camera_topic_val = requested_camera_topic

    nodes_to_launch = []

    if sim_engine == "gz":
        # --- Jazzy (Turtlebot4 + Gazebo Harmonic) ---
        tb4_gz_share = FindPackageShare("turtlebot4_gz_bringup")
        is_nav2 = use_nav2.perform(context).lower() == "true"
        is_slam = use_slam.perform(context).lower() == "true"
        localization = "true" if (is_nav2 and not is_slam) else "false"
        final_map = map_file.perform(context)
        if not final_map:
            final_map = default_map.perform(context)

        # TurtleBot4 Gazebo는 /cmd_vel에 TwistStamped를 사용한다.
        custom_nav2_params = PathJoinSubstitution([bringup_share, "config", "nav2_params_gz.yaml"])

        nodes_to_launch.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    PathJoinSubstitution([tb4_gz_share, "launch", "turtlebot4_gz.launch.py"])
                ),
                launch_arguments={
                    "model": robot_model,
                    "world": world,
                    "nav2": "true" if is_nav2 else "false",
                    "slam": "true" if is_slam else "false",
                    "localization": localization,
                    "map": final_map,
                    "rviz": "true" if is_nav2 else "false",
                    "params_file": custom_nav2_params,  # 커스텀 파라미터 적용
                }.items(),
            )
        )
    else:
        # --- Humble (Turtlebot3 + Gazebo Classic) ---
        tb3_gazebo_share = FindPackageShare("turtlebot3_gazebo")
        actual_world = world if world != "warehouse" else "turtlebot3_world"
        world_path = PathJoinSubstitution([tb3_gazebo_share, "worlds", actual_world + ".world"])

        nodes_to_launch.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    PathJoinSubstitution(
                        [tb3_gazebo_share, "launch", "robot_state_publisher.launch.py"]
                    )
                ),
                launch_arguments={"use_sim_time": use_sim_time}.items(),
            )
        )

        nodes_to_launch.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    PathJoinSubstitution(
                        [FindPackageShare("gazebo_ros"), "launch", "gzserver.launch.py"]
                    )
                ),
                launch_arguments={"world": world_path, "verbose": "true"}.items(),
            )
        )

        nodes_to_launch.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    PathJoinSubstitution(
                        [FindPackageShare("gazebo_ros"), "launch", "gzclient.launch.py"]
                    )
                ),
                launch_arguments={"verbose": "true"}.items(),
            )
        )

        nodes_to_launch.append(
            Node(
                package="gazebo_ros",
                executable="spawn_entity.py",
                arguments=[
                    "-entity",
                    "turtlebot3_waffle",
                    "-file",
                    PathJoinSubstitution(
                        [tb3_gazebo_share, "models", "turtlebot3_waffle", "model.sdf"]
                    ),
                    "-x",
                    "-2.0",
                    "-y",
                    "-0.5",
                    "-z",
                    "0.01",
                ],
                output="screen",
            )
        )

        is_slam_str = "True" if use_slam.perform(context).lower() == "true" else "False"
        if use_nav2.perform(context).lower() == "true":
            nav2_bringup_share = FindPackageShare("nav2_bringup")
            # 커스텀 파라미터 파일 경로 사용
            custom_nav2_params = PathJoinSubstitution([bringup_share, "config", "nav2_params.yaml"])

            nodes_to_launch.append(
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(
                        PathJoinSubstitution([nav2_bringup_share, "launch", "bringup_launch.py"])
                    ),
                    launch_arguments={
                        "use_sim_time": use_sim_time,
                        "slam": is_slam_str,
                        "map": map_file if map_file.perform(context) != "" else default_map,
                        "params_file": custom_nav2_params,
                        "autostart": "True",
                        "use_composition": "True",
                        "use_rviz": "False",
                    }.items(),
                )
            )
            rviz_config_path = PathJoinSubstitution(
                [nav2_bringup_share, "rviz", "nav2_default_view.rviz"]
            )
            nodes_to_launch.append(
                Node(
                    package="rviz2",
                    executable="rviz2",
                    name="rviz2",
                    arguments=["-d", rviz_config_path],
                    parameters=[{"use_sim_time": use_sim_time}],
                    output="screen",
                )
            )

    # RoboClaw 핵심 노드들(agent_node, discovery_node, channel_node 등)을
    # robo_claw.launch.py를 인클루드하여 구동
    robo_claw_launch_file = PathJoinSubstitution([bringup_share, "launch", "robo_claw.launch.py"])

    nodes_to_launch.append(
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(robo_claw_launch_file),
            launch_arguments={
                "use_sim_time": use_sim_time,
                "run_core": "false",  # 시뮬레이션 환경에서는 core_node를 비활성화 (기존 sim launch 구조 유지)
                "llm_provider": llm_provider,
                "llm_model": llm_model,
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
            DeclareLaunchArgument("sim_engine", default_value="classic"),
            DeclareLaunchArgument("use_nav2", default_value="false"),
            DeclareLaunchArgument("use_slam", default_value="false"),
            DeclareLaunchArgument("map", default_value=""),
            DeclareLaunchArgument("run_core", default_value="false"),
            DeclareLaunchArgument("run_manipulator", default_value="false"),
            DeclareLaunchArgument("scan_interval_sec", default_value="10.0"),
            DeclareLaunchArgument("memory_path", default_value=""),
            DeclareLaunchArgument("rag_local_mirror", default_value=""),
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
