"""RoboClaw 전체 시스템 런치 파일"""

import os
import sys

import yaml
from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

# ros2 launch 는 SourceFileLoader 로 이 파일을 로드하며 launch 파일 디렉토리를
# sys.path 에 추가하지 않는다. 동반 헬퍼 모듈(_common_launch_args) import를 위해
# 이 파일의 디렉토리를 명시적으로 sys.path 앞에 넣는다.
_LAUNCH_DIR = os.path.dirname(os.path.realpath(__file__))
if _LAUNCH_DIR not in sys.path:
    sys.path.insert(0, _LAUNCH_DIR)
from _common_launch_args import common_launch_arguments  # pyright: ignore[reportMissingImports]
from _system1_launch import system1_local_server_actions  # pyright: ignore[reportMissingImports]


def _safe_int(value, default=0):
    """런치 인자 문자열을 int 로 안전하게 변환한다. 무효 값은 기본값 사용."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0):
    """런치 인자 문자열을 float 로 안전하게 변환한다. 무효 값은 기본값 사용."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_robot_config(robot_config: str) -> dict:
    bringup_share = get_package_share_directory("robo_claw_bringup")
    config_path = os.path.join(bringup_share, "config", f"{robot_config}_config.yaml")
    if not os.path.exists(config_path):
        print(
            f"[RoboClaw][WARN] robot_config 설정 파일을 찾을 수 없습니다: {config_path} "
            f"(robot_config='{robot_config}' 오타 확인 필요, 기본값으로 계속 진행합니다)",
            file=sys.stderr,
        )
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as e:
        print(
            f"[RoboClaw][WARN] robot_config 설정 파일 로드 실패: {config_path} ({e}), "
            f"기본값으로 계속 진행합니다",
            file=sys.stderr,
        )
        return {}


def _resolve_camera_topic(robot_config: str, requested_camera_topic: str) -> str:
    if requested_camera_topic:
        return requested_camera_topic

    config = _load_robot_config(robot_config)

    sensor_topics = (
        config.get("robo_claw_core_node", {}).get("ros__parameters", {}).get("sensor_topics", {})
    )
    if not isinstance(sensor_topics, dict):
        return ""

    preferred_keys = ("realsense_camera", "oakd_camera", "rgb_camera", "camera")
    for key in preferred_keys:
        topic = sensor_topics.get(key)
        if topic:
            return str(topic)

    for sensor_name, topic in sensor_topics.items():
        if "camera" in str(sensor_name).lower() and topic:
            return str(topic)

    return ""


def _resolve_sensor_topic(
    robot_config: str,
    requested_topic: str,
    preferred_keys: tuple,
    fallback_substring: str,
) -> str:
    if requested_topic:
        return requested_topic

    config = _load_robot_config(robot_config)
    sensor_topics = (
        config.get("robo_claw_core_node", {}).get("ros__parameters", {}).get("sensor_topics", {})
    )
    if not isinstance(sensor_topics, dict):
        return ""

    for key in preferred_keys:
        topic = sensor_topics.get(key)
        if topic:
            return str(topic)

    for sensor_name, topic in sensor_topics.items():
        if fallback_substring in str(sensor_name).lower() and topic:
            return str(topic)

    return ""


def _resolve_depth_topic(robot_config: str, requested_depth_topic: str) -> str:
    return _resolve_sensor_topic(
        robot_config,
        requested_depth_topic,
        preferred_keys=("realsense_depth", "aligned_depth", "depth_camera", "depth"),
        fallback_substring="depth",
    )


def _resolve_camera_info_topic(robot_config: str, requested_camera_info_topic: str) -> str:
    return _resolve_sensor_topic(
        robot_config,
        requested_camera_info_topic,
        preferred_keys=("realsense_camera_info", "camera_info"),
        fallback_substring="camera_info",
    )


def _resolve_pointcloud_topic(robot_config: str, requested_pointcloud_topic: str) -> str:
    return _resolve_sensor_topic(
        robot_config,
        requested_pointcloud_topic,
        preferred_keys=("realsense_points", "pointcloud", "points"),
        fallback_substring="point",
    )


def _resolve_robot_description(robot_config: str, requested_robot_description_file: str) -> str:
    robot_description_file = requested_robot_description_file
    if not robot_description_file:
        config = _load_robot_config(robot_config)
        robot_description_file = (
            config.get("robo_claw_core_node", {})
            .get("ros__parameters", {})
            .get("robot_description_file", "")
        )

    if not robot_description_file:
        return ""

    if not os.path.isabs(robot_description_file):
        bringup_share = get_package_share_directory("robo_claw_bringup")
        robot_description_file = os.path.join(bringup_share, robot_description_file)

    try:
        with open(robot_description_file, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        print(
            f"[RoboClaw][WARN] robot_description 파일 로드 실패: {robot_description_file} ({e}), "
            f"robot_description 파라미터를 빈 값으로 진행합니다",
            file=sys.stderr,
        )
        return ""


def _launch_setup(context, *args, **kwargs):
    del args, kwargs

    bringup_share = get_package_share_directory("robo_claw_bringup")
    agent_yaml = os.path.join(bringup_share, "config", "agent.yaml")
    robot_config = LaunchConfiguration("robot_config").perform(context)
    llm_provider = LaunchConfiguration("llm_provider").perform(context)
    llm_model = LaunchConfiguration("llm_model").perform(context)
    robot_yaml = os.path.join(bringup_share, "config", f"{robot_config}_config.yaml")

    camera_topic = _resolve_camera_topic(
        robot_config=robot_config,
        requested_camera_topic=LaunchConfiguration("camera_topic").perform(context),
    )
    depth_topic = _resolve_depth_topic(
        robot_config=robot_config,
        requested_depth_topic=LaunchConfiguration("depth_topic").perform(context),
    )
    camera_info_topic = _resolve_camera_info_topic(
        robot_config=robot_config,
        requested_camera_info_topic=LaunchConfiguration("camera_info_topic").perform(context),
    )
    pointcloud_topic = _resolve_pointcloud_topic(
        robot_config=robot_config,
        requested_pointcloud_topic=LaunchConfiguration("pointcloud_topic").perform(context),
    )
    gripper_camera_topic = LaunchConfiguration("gripper_camera_topic").perform(context)
    gripper_depth_topic = LaunchConfiguration("gripper_depth_topic").perform(context)
    gripper_camera_info_topic = LaunchConfiguration("gripper_camera_info_topic").perform(context)
    gripper_pointcloud_topic = LaunchConfiguration("gripper_pointcloud_topic").perform(context)
    robot_description = _resolve_robot_description(
        robot_config=robot_config,
        requested_robot_description_file=LaunchConfiguration("robot_description_file").perform(
            context
        ),
    )
    llm_embedding_model = LaunchConfiguration("llm_embedding_model").perform(context)
    llm_embedding_provider = LaunchConfiguration("llm_embedding_provider").perform(context)
    llm_embedding_base_url = LaunchConfiguration("llm_embedding_base_url").perform(context)
    llm_embedding_api_key = LaunchConfiguration("llm_embedding_api_key").perform(context)
    llm_base_url = LaunchConfiguration("llm_base_url").perform(context)
    robot_soul_file = LaunchConfiguration("robot_soul_file").perform(context)
    system_prompt_file = LaunchConfiguration("system_prompt_file").perform(context)
    skills_guide_file = LaunchConfiguration("skills_guide_file").perform(context)
    # robot_config 전용 스킬 가이드 기본값 (예: stretch3 → SKILLS.stretch3.md)
    # 명시적으로 skills_guide_file 이 지정되지 않은 경우에만 per-robot 가이드를 사용한다.
    # CLOiD 처럼 같은 기종의 개체별 문서(SKILLS.<robot_config>.<개체번호>.md)를 두는
    # 프로필은 개체 번호를 기동 인자로 지정할 수 없으므로, 기본값은 1호 문서로 둔다.
    # 다른 개체는 skills_guide_file(또는 RC_SKILLS_GUIDE_FILE)로 명시한다.
    if not skills_guide_file:
        for guide_name in (
            f"SKILLS.{robot_config}.md",
            f"SKILLS.{robot_config}.1.md",
        ):
            config_skills_guide = os.path.join(bringup_share, "config", guide_name)
            if not os.path.exists(config_skills_guide):
                continue
            skills_guide_file = config_skills_guide
            if guide_name.endswith(".1.md"):
                print(
                    "[RoboClaw][WARN] 개체 번호가 지정되지 않아 "
                    f"{guide_name} 을(를) 스킬 가이드 기본값으로 사용합니다. "
                    "다른 개체는 skills_guide_file:=<개체 가이드 경로> 로 지정하세요.",
                    file=sys.stderr,
                )
            break
    robot_limits_file = LaunchConfiguration("robot_limits_file").perform(context)
    # robot_config 전용 limits 파일 기본값 (예: stretch3 → ROBOT_LIMITS.stretch3.json)
    if not robot_limits_file:
        config_limits = os.path.join(bringup_share, "config", f"ROBOT_LIMITS.{robot_config}.json")
        if os.path.exists(config_limits):
            robot_limits_file = config_limits
    troubleshooting_guide_file = LaunchConfiguration("troubleshooting_guide_file").perform(context)
    resolved_butler_script_dir = LaunchConfiguration("butler_script_dir").perform(context)
    agent_workspace_dir = LaunchConfiguration("agent_workspace_dir").perform(context)
    ollama_options_json = LaunchConfiguration("ollama_options_json").perform(context)
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
    rag_local_mirror = LaunchConfiguration("rag_local_mirror").perform(context)
    memory_path = LaunchConfiguration("memory_path").perform(context)
    task_queue_max_size = LaunchConfiguration("task_queue_max_size").perform(context)
    llm_fail_fast = LaunchConfiguration("llm_fail_fast").perform(context)
    strict_config = LaunchConfiguration("strict_config").perform(context)
    enable_task_decomposition = LaunchConfiguration("enable_task_decomposition").perform(context)
    task_decomposition_max_steps = LaunchConfiguration("task_decomposition_max_steps").perform(
        context
    )
    task_step_max_retries = LaunchConfiguration("task_step_max_retries").perform(context)
    task_decomposition_wait_margin_cap_sec = LaunchConfiguration(
        "task_decomposition_wait_margin_cap_sec"
    ).perform(context)
    mcp_enabled = LaunchConfiguration("mcp_enabled").perform(context)
    mcp_servers_json = LaunchConfiguration("mcp_servers_json").perform(context)

    core_parameters = []
    if os.path.exists(robot_yaml):
        core_parameters.append(robot_yaml)
    core_parameters.append(
        {
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "agent_id": ParameterValue(LaunchConfiguration("agent_id"), value_type=str),
            "loop_rate_hz": 50.0,
        }
    )
    if robot_description:
        core_parameters[1]["robot_description"] = robot_description

    core_node = Node(
        package="robo_claw_core",
        executable="core_node",
        name="robo_claw_core_node",
        output="screen",
        parameters=core_parameters,
        condition=IfCondition(LaunchConfiguration("run_core")),
    )

    agent_parameters = []
    if os.path.exists(agent_yaml):
        agent_parameters.append(agent_yaml)
    if os.path.exists(robot_yaml):
        agent_parameters.append(robot_yaml)
    agent_parameters.append(
        {
            "use_sim_time": LaunchConfiguration("use_sim_time"),
            "agent_id": ParameterValue(LaunchConfiguration("agent_id"), value_type=str),
            "llm_provider": llm_provider,
            "llm_model": llm_model,
            "compact_skill_prompt": LaunchConfiguration("compact_skill_prompt"),
            "azure_openai_endpoint": ParameterValue(
                LaunchConfiguration("azure_endpoint"), value_type=str
            ),
            "azure_openai_api_key": ParameterValue(
                LaunchConfiguration("azure_api_key"), value_type=str
            ),
            "openai_api_key": ParameterValue(LaunchConfiguration("openai_api_key"), value_type=str),
            "anthropic_api_key": ParameterValue(
                LaunchConfiguration("anthropic_api_key"), value_type=str
            ),
        }
    )
    if llm_embedding_model:
        agent_parameters[-1]["llm_embedding_model"] = llm_embedding_model
    if llm_embedding_provider:
        agent_parameters[-1]["llm_embedding_provider"] = llm_embedding_provider
    if llm_embedding_base_url:
        agent_parameters[-1]["llm_embedding_base_url"] = llm_embedding_base_url
    if llm_embedding_api_key:
        agent_parameters[-1]["llm_embedding_api_key"] = llm_embedding_api_key
    if llm_base_url:
        agent_parameters[-1]["llm_base_url"] = llm_base_url
    if robot_soul_file:
        agent_parameters[-1]["robot_soul_file"] = robot_soul_file
    if system_prompt_file:
        agent_parameters[-1]["system_prompt_file"] = system_prompt_file
    if skills_guide_file:
        agent_parameters[-1]["skills_guide_file"] = skills_guide_file
    if robot_limits_file:
        agent_parameters[-1]["robot_limits_file"] = robot_limits_file
    if troubleshooting_guide_file:
        agent_parameters[-1]["troubleshooting_guide_file"] = troubleshooting_guide_file
    if resolved_butler_script_dir:
        agent_parameters[-1]["butler_script_dir"] = resolved_butler_script_dir
    if agent_workspace_dir:
        agent_parameters[-1]["agent_workspace_dir"] = agent_workspace_dir
    if ollama_options_json:
        agent_parameters[-1]["ollama_options_json"] = ollama_options_json
    if enable_rag:
        agent_parameters[-1]["enable_rag"] = enable_rag.lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if rag_vector_backend:
        agent_parameters[-1]["rag_vector_backend"] = rag_vector_backend
    if rag_top_k:
        agent_parameters[-1]["rag_top_k"] = _safe_int(rag_top_k)
    if rag_score_threshold:
        agent_parameters[-1]["rag_score_threshold"] = _safe_float(rag_score_threshold)
    if enable_skill_learning:
        agent_parameters[-1]["enable_skill_learning"] = enable_skill_learning.lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if skill_learning_success_sample_rate:
        agent_parameters[-1]["skill_learning_success_sample_rate"] = _safe_float(
            skill_learning_success_sample_rate
        )
    if skill_learning_reflect_interval_sec:
        agent_parameters[-1]["skill_learning_reflect_interval_sec"] = _safe_float(
            skill_learning_reflect_interval_sec
        )
    if qdrant_url:
        agent_parameters[-1]["qdrant_url"] = qdrant_url
    if qdrant_api_key:
        agent_parameters[-1]["qdrant_api_key"] = qdrant_api_key
    if qdrant_collection:
        agent_parameters[-1]["qdrant_collection"] = qdrant_collection
    if qdrant_timeout_sec:
        agent_parameters[-1]["qdrant_timeout_sec"] = _safe_float(qdrant_timeout_sec)
    if rag_local_mirror:
        agent_parameters[-1]["rag_local_mirror"] = rag_local_mirror.lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if memory_path:
        agent_parameters[-1]["memory_path"] = memory_path
    if task_queue_max_size:
        agent_parameters[-1]["task_queue_max_size"] = _safe_int(task_queue_max_size)
    if llm_fail_fast:
        agent_parameters[-1]["llm_fail_fast"] = llm_fail_fast.lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if strict_config:
        agent_parameters[-1]["strict_config"] = strict_config.lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if enable_task_decomposition:
        agent_parameters[-1]["enable_task_decomposition"] = enable_task_decomposition.lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if task_decomposition_max_steps:
        agent_parameters[-1]["task_decomposition_max_steps"] = _safe_int(
            task_decomposition_max_steps
        )
    if task_step_max_retries:
        agent_parameters[-1]["task_step_max_retries"] = _safe_int(task_step_max_retries)
    if task_decomposition_wait_margin_cap_sec:
        agent_parameters[-1]["task_decomposition_wait_margin_cap_sec"] = _safe_float(
            task_decomposition_wait_margin_cap_sec
        )
    if camera_topic:
        agent_parameters[-1]["camera_topic"] = camera_topic
    if depth_topic:
        agent_parameters[-1]["depth_topic"] = depth_topic
    if camera_info_topic:
        agent_parameters[-1]["camera_info_topic"] = camera_info_topic
    if gripper_camera_topic:
        agent_parameters[-1]["gripper_camera_topic"] = gripper_camera_topic
    if gripper_depth_topic:
        agent_parameters[-1]["gripper_depth_topic"] = gripper_depth_topic
    if gripper_camera_info_topic:
        agent_parameters[-1]["gripper_camera_info_topic"] = gripper_camera_info_topic
    if gripper_pointcloud_topic:
        agent_parameters[-1]["gripper_pointcloud_topic"] = gripper_pointcloud_topic

    # 자가진단용 센서 토픽 오버라이드 (robot_config YAML 기본값보다 우선)
    lidar_topic_override = LaunchConfiguration("lidar_topic").perform(context)
    imu_topic_override = LaunchConfiguration("imu_topic").perform(context)
    if lidar_topic_override:
        agent_parameters[-1]["lidar_topic"] = lidar_topic_override
    if imu_topic_override:
        agent_parameters[-1]["imu_topic"] = imu_topic_override

    # 공통 스킬 정책 (HTTP /task, /skill, gRPC, 메신저, ROS service 공통 적용)
    agent_parameters[-1]["skill_allowed_json"] = ParameterValue(
        LaunchConfiguration("http_allowed_skills_json"),
        value_type=str,
    )
    agent_parameters[-1]["skill_blocked_json"] = ParameterValue(
        LaunchConfiguration("http_blocked_skills_json"),
        value_type=str,
    )

    # MCP(Model Context Protocol) 서버 연동 (비어있으면 agent.yaml 기본값 유지)
    if mcp_enabled:
        agent_parameters[-1]["mcp_enabled"] = mcp_enabled.lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if mcp_servers_json:
        agent_parameters[-1]["mcp_servers_json"] = ParameterValue(
            LaunchConfiguration("mcp_servers_json"),
            value_type=str,
        )

    # 로봇 설정에 따른 추가 스킬 모듈 주입
    skill_modules = [
        "robo_claw_agent.skills.navigation_skill",
        "robo_claw_agent.skills.manipulation_skill",
        "robo_claw_agent.skills.perception_skill",
        "robo_claw_agent.skills.system_skill",
        "robo_claw_agent.skills.vision_skill",
        "robo_claw_agent.skills.map_skill",
        "robo_claw_agent.skills.hri_skill",
        "robo_claw_agent.skills.cooperate_skill",
        "robo_claw_agent.skills.cooperation_skill",
        "robo_claw_agent.skills.file_skill",
        "robo_claw_agent.skills.explore_skill",
        "robo_claw_agent.skills.autonomous_skill",
        "robo_claw_agent.skills.tidy_home_skill",
    ]
    if robot_config == "butler":
        skill_modules.append("robo_claw_agent.skills.butler_skill")
    elif robot_config == "cloid":
        skill_modules.append("robo_claw_agent.skills.cloid_motion_skill")
    agent_parameters[-1]["skill_modules"] = skill_modules

    # YAML 설정에서 odom_topic 동적 파싱
    config = _load_robot_config(robot_config)
    odom_topic = (
        config.get("robo_claw_core_node", {}).get("ros__parameters", {}).get("odom_topic", "/odom")
    )

    agent_remappings = []
    if odom_topic and odom_topic != "/odom":
        agent_remappings.append(("/odom", odom_topic))

    agent_node = Node(
        package="robo_claw_agent",
        executable="agent_node",
        name="robo_claw_agent_node",
        output="screen",
        parameters=agent_parameters,
        remappings=agent_remappings,
    )

    discovery_node = Node(
        package="robo_claw_discovery",
        executable="discovery_node",
        name="robo_claw_discovery_node",
        output="screen",
        parameters=[
            {
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "scan_interval_sec": LaunchConfiguration("scan_interval_sec"),
            }
        ],
    )

    channel_node = Node(
        package="robo_claw_channel",
        executable="channel_node",
        name="robo_claw_channel_node",
        output="screen",
        parameters=[
            agent_yaml,
            {
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "http_host": LaunchConfiguration("http_host"),
                "http_port": LaunchConfiguration("http_port"),
                "http_readonly_token": ParameterValue(
                    LaunchConfiguration("http_readonly_token"), value_type=str
                ),
                "http_control_token": ParameterValue(
                    LaunchConfiguration("http_control_token"), value_type=str
                ),
                "http_allowed_cidrs_json": ParameterValue(
                    LaunchConfiguration("http_allowed_cidrs_json"),
                    value_type=str,
                ),
                "http_rate_limit_per_minute": LaunchConfiguration("http_rate_limit_per_minute"),
                "http_allowed_skills_json": ParameterValue(
                    LaunchConfiguration("http_allowed_skills_json"),
                    value_type=str,
                ),
                "http_blocked_skills_json": ParameterValue(
                    LaunchConfiguration("http_blocked_skills_json"),
                    value_type=str,
                ),
                "telegram_token": ParameterValue(
                    LaunchConfiguration("telegram_token"), value_type=str
                ),
                "slack_app_token": ParameterValue(
                    LaunchConfiguration("slack_app_token"), value_type=str
                ),
                "slack_bot_token": ParameterValue(
                    LaunchConfiguration("slack_bot_token"), value_type=str
                ),
                "discord_token": ParameterValue(
                    LaunchConfiguration("discord_token"), value_type=str
                ),
                "enable_discord": LaunchConfiguration("enable_discord"),
                "enable_telegram": LaunchConfiguration("enable_telegram"),
                "enable_slack": LaunchConfiguration("enable_slack"),
                "enable_grpc": LaunchConfiguration("enable_grpc"),
                "grpc_port": LaunchConfiguration("grpc_port"),
                "grpc_peer_token": ParameterValue(
                    LaunchConfiguration("grpc_peer_token"), value_type=str
                ),
                "grpc_peer_tokens_json": ParameterValue(
                    LaunchConfiguration("grpc_peer_tokens_json"), value_type=str
                ),
                "grpc_max_file_bytes": LaunchConfiguration("grpc_max_file_bytes"),
                "grpc_dedup_path": LaunchConfiguration("grpc_dedup_path"),
            },
        ],
        condition=IfCondition(LaunchConfiguration("use_channel")),
    )

    dashboard_node = Node(
        package="robo_claw_dashboard",
        executable="dashboard_node",
        name="robo_claw_dashboard_node",
        output="screen",
        parameters=[
            {
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "dashboard_host": LaunchConfiguration("dashboard_host"),
                "dashboard_port": LaunchConfiguration("dashboard_port"),
                "agent_name": "robo_claw_agent_node",
                "http_readonly_token": ParameterValue(
                    LaunchConfiguration("http_readonly_token"), value_type=str
                ),
                "http_control_token": ParameterValue(
                    LaunchConfiguration("http_control_token"), value_type=str
                ),
                "http_allowed_cidrs_json": ParameterValue(
                    LaunchConfiguration("http_allowed_cidrs_json"), value_type=str
                ),
                "http_rate_limit_per_minute": LaunchConfiguration("http_rate_limit_per_minute"),
                "http_blocked_skills_json": ParameterValue(
                    LaunchConfiguration("http_blocked_skills_json"), value_type=str
                ),
            }
        ],
        condition=IfCondition(LaunchConfiguration("use_dashboard")),
    )

    client_node = Node(
        package="robo_claw_channel",
        executable="client_node",
        name="robo_claw_messenger_client_node",
        output="screen",
        parameters=[
            {
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "target_host": LaunchConfiguration("target_host"),
                "target_port": LaunchConfiguration("target_port"),
                "target_peers_json": ParameterValue(
                    LaunchConfiguration("target_peers_json"), value_type=str
                ),
                "grpc_peer_token": ParameterValue(
                    LaunchConfiguration("grpc_peer_token"), value_type=str
                ),
                "grpc_peer_tokens_json": ParameterValue(
                    LaunchConfiguration("grpc_peer_tokens_json"), value_type=str
                ),
                "agent_name": "robo_claw_agent_node",
                "agent_id": ParameterValue(LaunchConfiguration("agent_id"), value_type=str),
            }
        ],
        condition=IfCondition(LaunchConfiguration("use_client")),
    )

    vision_node = Node(
        package="robo_claw_vision",
        executable="object_detector_node",
        name="object_detector_node",
        output="screen",
        parameters=[
            {
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "camera_topic": camera_topic or "/oakd/rgb/preview/image_raw",
                "model_path": LaunchConfiguration("vision_model_path"),
                # Tier 1.1: 헤드 카메라 confidence threshold 인하(0.5→0.25).
                # YOLOv8n/s는 0.5에서 컵/병을 놓치는 경우가 많다. 낮춰서 후보를
                # 더 많이 publish하고, Python 쪽 min_score로 2차 필터링한다.
                "confidence_threshold": 0.25,
                "nms_threshold": 0.45,
                "num_threads": 4,
                "max_inference_hz": ParameterValue(
                    LaunchConfiguration("vision_max_inference_hz"), value_type=float
                ),
                # Tier 1.5 / 1.4 / 1.3: CLAHE + bbox 검증 + temporal 확인 필터.
                # Tier 2.1: 면적 하한 인하(0.001→0.0002)와 종횡비 확대, temporal 을
                # 2-of-5 로 완화해 원거리/소형 물체와 탐색 중 깜빡임을 보존한다.
                "use_clahe": True,
                "clahe_clip_limit": 2.0,
                "clahe_tile_grid_size": 8,
                "min_aspect_ratio": 0.1,
                "max_aspect_ratio": 10.0,
                "min_bbox_area_ratio": 0.0002,
                "max_bbox_area_ratio": 0.8,
                "temporal_history_size": 5,
                "temporal_confirmation_frames": 2,
            }
        ],
        condition=IfCondition(LaunchConfiguration("use_vision")),
    )

    gripper_vision_node = Node(
        package="robo_claw_vision",
        executable="object_detector_node",
        name="gripper_object_detector_node",
        output="screen",
        parameters=[
            {
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "camera_topic": gripper_camera_topic or "/gripper_camera/color/image_rect_raw",
                "model_path": LaunchConfiguration("vision_model_path"),
                "confidence_threshold": 0.1,
                "nms_threshold": 0.45,
                "num_threads": 4,
                "max_inference_hz": ParameterValue(
                    LaunchConfiguration("gripper_vision_max_inference_hz"),
                    value_type=float,
                ),
                # Tier 1.5 / 1.4 / 1.3: 그리퍼 카메라에도 동일 적용
                "use_clahe": True,
                "clahe_clip_limit": 2.0,
                "clahe_tile_grid_size": 8,
                "min_aspect_ratio": 0.1,
                "max_aspect_ratio": 10.0,
                "min_bbox_area_ratio": 0.0002,
                "max_bbox_area_ratio": 0.8,
                "temporal_history_size": 5,
                "temporal_confirmation_frames": 2,
            }
        ],
        condition=IfCondition(LaunchConfiguration("use_gripper_vision")),
    )

    # 실제 깊이 카메라 pointcloud를 Nav2 costmap이 참조하는 고정 토픽(/depth_camera/points)으로
    # 릴레이한다. 로봇마다 실제 카메라 드라이버 토픽 이름이 다르므로(예: stretch3/former/butler가
    # 각기 다른 네임스페이스 사용), 정적인 nav2_params.yaml이 로봇별 토픽 이름 차이에 영향받지
    # 않도록 이 relay로 흡수한다.
    depth_relay_node = Node(
        package="topic_tools",
        executable="relay",
        name="depth_pointcloud_relay",
        output="screen",
        arguments=[
            pointcloud_topic or "/camera/camera/depth/color/points",
            "/depth_camera/points",
        ],
        parameters=[{"use_sim_time": LaunchConfiguration("use_sim_time")}],
        condition=IfCondition(LaunchConfiguration("use_depth_costmap")),
    )

    grpc_node = Node(
        package="robo_claw_grpc",
        executable="robo_claw_grpc",
        name="robo_claw_grpc_node",
        output="screen",
        parameters=[
            {
                "use_sim_time": LaunchConfiguration("use_sim_time"),
                "camera_topic": camera_topic or "/camera/image_raw",
                "camera_compressed_topic": camera_topic + "/compressed"
                if camera_topic
                else "/camera/image_raw/compressed",
                "battery_topic": "/battery_state",
                "pose_topic": "/amcl_pose",
                "navigate_to_topic": "/robo_claw_grpc/navigate_to",
                "map_topic": "/map",
            }
        ],
        condition=IfCondition(LaunchConfiguration("use_grpc")),
    )

    manipulator_parameters = []
    if os.path.exists(robot_yaml):
        manipulator_parameters.append(robot_yaml)
    manipulator_parameters.append(
        {
            "use_sim_time": LaunchConfiguration("use_sim_time"),
        }
    )
    if robot_description:
        manipulator_parameters[1]["robot_description"] = robot_description

    manipulator_node = Node(
        package="robo_claw_core",
        executable="manipulator_node",
        name="robo_claw_manipulator_node",
        output="screen",
        parameters=manipulator_parameters,
    )

    # MoveIt 기반 manipulator_node(C++) 실행 여부.
    # Stretch3 는 MoveIt 대신 자체 stretch_driver 백엔드를 쓰므로 robot_config 가
    # stretch3 면 항상 제외한다. run_manipulator:=false 로 다른 로봇에서도 끌 수 있다.
    run_manipulator = LaunchConfiguration("run_manipulator").perform(context)
    use_moveit_manipulator = (
        run_manipulator.lower() in ("1", "true", "yes", "on") and robot_config != "stretch3"
    )
    print("\n" + "=" * 50)
    print(" [RoboClaw] 런치 환경 변수(env) 로딩 및 해석 정보")
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

    launch_nodes = [
        core_node,
        agent_node,
        discovery_node,
        channel_node,
        dashboard_node,
        vision_node,
        depth_relay_node,
        grpc_node,
        client_node,
    ]
    if robot_config == "stretch3":
        launch_nodes.append(gripper_vision_node)
    if use_moveit_manipulator:
        launch_nodes.append(manipulator_node)
    # SYSTEM1_LOCAL_SERVER=true 이면 같은 컨테이너에서 Laya 서버를 함께 띄운다(docs/SYSTEM1_FAST_ROUTER.md).
    launch_nodes.extend(system1_local_server_actions(os.environ))
    return launch_nodes


def generate_launch_description() -> LaunchDescription:
    """로보클로 전체 시스템 런치 설명 생성.

    공통 인자는 ``_common_launch_args.common_launch_arguments()`` 에서 가져오고,
    이 launch 파일에서만 의미가 있는 인자(use_sim_time, run_core, run_manipulator,
    vision, gRPC client target_*, memory_path 등)는 여기서 별도 선언한다.
    """
    return LaunchDescription(
        common_launch_arguments()
        + [
            # --- 이 launch 파일 전용 인자 ---
            DeclareLaunchArgument(
                "use_sim_time",
                default_value="false",
                description="시뮬레이션 시간(use_sim_time) 활성화 여부 (true | false)",
            ),
            DeclareLaunchArgument(
                "run_core",
                default_value="true",
                description="robo_claw_core_node 실행 여부 (true | false)",
            ),
            DeclareLaunchArgument(
                "run_manipulator",
                default_value="true",
                description="MoveIt 기반 manipulator_node 실행 여부 (true | false). "
                "stretch3 설정에서는 자체 driver 백엔드를 쓰므로 무조건 비활성화된다.",
            ),
            DeclareLaunchArgument(
                "scan_interval_sec",
                default_value="10.0",
                description="Discovery 스캔 간격 (초)",
            ),
            DeclareLaunchArgument(
                "use_channel",
                default_value="false",
                description="HTTP 채널 노드 실행 여부 (true | false)",
            ),
            DeclareLaunchArgument(
                "use_dashboard",
                default_value="false",
                description="Dashboard 웹 노드 실행 여부 (true | false)",
            ),
            DeclareLaunchArgument(
                "memory_path",
                default_value="",
                description="메모리/로컬 벡터 저장 파일 경로 오버라이드. 비우면 ~/.robo_claw 사용",
            ),
            DeclareLaunchArgument(
                "rag_local_mirror",
                default_value="",
                description="Qdrant 사용 시 로컬에도 동시 저장(미러). 비우면 agent.yaml 값을 사용",
            ),
            DeclareLaunchArgument(
                "robot_description_file",
                default_value="",
                description="URDF 파일 경로. 비우면 robot_config 설정의 robot_description_file을 사용",
            ),
            DeclareLaunchArgument(
                "use_vision",
                default_value="true",
                description="ONNX 객체 인식 노드 실행 여부 (true | false)",
            ),
            DeclareLaunchArgument(
                "vision_model_path",
                default_value="/ros2_ws/models/yolov8s.onnx",
                description="YOLOv8 ONNX 모델 파일 경로 (기본 yolov8s)",
            ),
            DeclareLaunchArgument(
                "vision_max_inference_hz",
                default_value="10.0",
                description=(
                    "ONNX 추론 최대 주기(Hz). 카메라 프레임레이트보다 낮게 제한해 "
                    "저사양 로봇 부담을 줄인다 (0 이하 = 제한 없음)"
                ),
            ),
            DeclareLaunchArgument(
                "use_gripper_vision",
                default_value="true",
                description="Stretch3 그리퍼 카메라 ONNX 객체 인식 노드 실행 여부 (true | false)",
            ),
            DeclareLaunchArgument(
                "gripper_vision_max_inference_hz",
                default_value="5.0",
                description="그리퍼 카메라 ONNX 추론 최대 주기(Hz). 0 이하 = 제한 없음",
            ),
            DeclareLaunchArgument(
                "use_client",
                default_value="false",
                description="gRPC 메신저 클라이언트 실행 여부 (true | false)",
            ),
            DeclareLaunchArgument(
                "target_host",
                default_value="127.0.0.1",
                description="gRPC 기본 연결 대상 로봇 IP",
            ),
            DeclareLaunchArgument(
                "target_port",
                default_value="50052",
                description="gRPC 기본 연결 대상 로봇 Port",
            ),
            DeclareLaunchArgument(
                "grpc_port",
                default_value="50052",
                description="channel_node 내장 RoboMessenger gRPC 서버 리슨 포트 "
                "(robo_claw_grpc 텔레메트리 서버의 기본 포트 50051과 겹치지 않도록 주의)",
            ),
            DeclareLaunchArgument(
                "target_peers_json",
                default_value="",
                description="gRPC 연결 대상 로봇 피어 목록 JSON",
            ),
            OpaqueFunction(function=_launch_setup),
        ]
    )
