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
    scenario_file = LaunchConfiguration("scenario_file").perform(context)

    # 월드 이름에 따른 맵 파일 자동 설정
    if sim_engine == "gz":
        tb4_nav_share = FindPackageShare("turtlebot4_navigation")
        default_map = PathJoinSubstitution([tb4_nav_share, "maps", world + ".yaml"])
        camera_topic_val = "/oakd/rgb/preview/image_raw"
    elif sim_engine == "stub":
        # 무거운 시뮬레이터: 지도/카메라는 robo_claw_sim 노드가 직접 발행한다.
        default_map = ""
        camera_topic_val = "/sim/camera/image_raw"
    else:
        tb3_nav_share = FindPackageShare("nav2_bringup")
        default_map = PathJoinSubstitution([tb3_nav_share, "maps", "turtlebot3_world.yaml"])
        camera_topic_val = "/camera/image_raw"

    if requested_camera_topic:
        camera_topic_val = requested_camera_topic

    nodes_to_launch = []

    if sim_engine == "stub":
        # --- Gazebo 없는 기본 시뮬레이터 ---
        # 운동학 세계 + 센서 합성 + NavigateToPose/Spin/FollowWaypoints 액션 서버를
        # 한 노드에서 제공한다. GPU/distro별 Gazebo 자산이 필요 없다.
        sim_parameters = {
            "scenario_file": scenario_file,
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

    if sim_engine == "gz":
        # --- Jazzy (Turtlebot4 + Gazebo Harmonic) ---
        tb4_gz_share = FindPackageShare("turtlebot4_gz_bringup")
        is_nav2 = use_nav2.perform(context).lower() == "true"
        is_slam = use_slam.perform(context).lower() == "true"
        final_map = map_file.perform(context)
        if not final_map:
            final_map = default_map.perform(context)

        # turtlebot4_gz.launch.py 가 선언한 인자는 namespace/rviz/world/model/pose 뿐이다.
        # nav2/slam/localization/map/params_file 을 여기에 넘기면 미선언 인자로
        # 조용히 무시되어 시뮬레이터만 뜨고 내비게이션 스택이 사라진다.
        # 내비게이션 계층은 turtlebot4_navigation 런치를 직접 include 한다.
        nodes_to_launch.append(
            IncludeLaunchDescription(
                PythonLaunchDescriptionSource(
                    PathJoinSubstitution([tb4_gz_share, "launch", "turtlebot4_gz.launch.py"])
                ),
                launch_arguments={
                    "model": robot_model,
                    "world": world,
                    "rviz": "true" if is_nav2 else "false",
                }.items(),
            )
        )

        # 지도 기반 위치추정(AMCL + map_server)은 SLAM 사용 시 비활성화한다.
        if is_nav2 and not is_slam:
            nodes_to_launch.append(
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(
                        PathJoinSubstitution([tb4_nav_share, "launch", "localization.launch.py"])
                    ),
                    launch_arguments={
                        "use_sim_time": use_sim_time,
                        "map": final_map,
                    }.items(),
                )
            )

        if is_slam:
            nodes_to_launch.append(
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(
                        PathJoinSubstitution([tb4_nav_share, "launch", "slam.launch.py"])
                    ),
                    launch_arguments={"use_sim_time": use_sim_time}.items(),
                )
            )

        if is_nav2:
            # TurtleBot4 Gazebo는 /cmd_vel에 TwistStamped를 사용한다.
            custom_nav2_params = PathJoinSubstitution(
                [bringup_share, "config", "nav2_params_gz.yaml"]
            )
            nodes_to_launch.append(
                IncludeLaunchDescription(
                    PythonLaunchDescriptionSource(
                        PathJoinSubstitution([tb4_nav_share, "launch", "nav2.launch.py"])
                    ),
                    launch_arguments={
                        "use_sim_time": use_sim_time,
                        "params_file": custom_nav2_params,
                    }.items(),
                )
            )
    elif sim_engine == "classic":
        # --- Humble (Turtlebot3 + Gazebo Classic) ---
        # bare `else` 로 두면 sim_engine:=stub 에서도 turtlebot3_gazebo/gazebo_ros 를
        # 찾다가 런치 전체가 예외로 종료된다. 엔진별 분기는 항상 명시적으로 비교한다.
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

    # stub 엔진의 sim_node 는 실시간(20Hz)으로 동작하고 /clock 을 발행하지 않는다.
    # 이때 하위 스택에 use_sim_time:=true 를 넘기면 ROS 타이머가 전진하지 않아
    # heartbeat 가 30초 뒤 watchdog 에 의해 노드가 종료된다.
    stack_use_sim_time = "false" if sim_engine == "stub" else use_sim_time.perform(context)

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
            DeclareLaunchArgument("sim_engine", default_value="classic"),
            DeclareLaunchArgument(
                "scenario_file",
                default_value="",
                description="sim_engine:=stub 에서 사용할 시나리오 YAML 경로",
            ),
            DeclareLaunchArgument("sim_update_rate_hz", default_value="20.0"),
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
