package launcher

import (
	"fmt"
	"path/filepath"
	"strings"

	"robo_claw_cli/contract"
)

// BuildCommonLaunchArgs config.json 및 .env 기반의 공통 ROS 런치 아규먼트를 빌드합니다.
// Docker/로컬 모드 모두에서 사용되는 인수들을 포함합니다.
func BuildCommonLaunchArgs(lctx *LaunchContext) []string {
	var args []string
	cfg := lctx.ActiveConfig
	env := lctx.EnvMap

	// config.json의 LLM 및 메신저 설정값 주입
	args = append(args,
		fmt.Sprintf("llm_provider:=%s", cfg.LlmProvider),
		fmt.Sprintf("llm_model:=%s", cfg.LlmModel),
		"use_channel:=true",
		fmt.Sprintf("enable_discord:=%t", cfg.EnableDiscord),
		fmt.Sprintf("enable_telegram:=%t", cfg.EnableTelegram),
		fmt.Sprintf("enable_slack:=%t", cfg.EnableSlack),
		fmt.Sprintf("enable_grpc:=%t", cfg.EnableGrpc),
		fmt.Sprintf("use_grpc:=%t", cfg.UseGrpc || lctx.ForceGrpc),
		fmt.Sprintf("use_client:=%t", cfg.EnableGrpcClient),
	)
	if agentID := firstNonEmpty(env["RC_AGENT_ID"], cfg.AgentId); agentID != "" {
		args = append(args, fmt.Sprintf("agent_id:=%s", agentID))
	}
	if cfg.GrpcTargetHost != "" {
		args = append(args, fmt.Sprintf("target_host:=%s", cfg.GrpcTargetHost))
	}
	if cfg.GrpcTargetPort > 0 {
		args = append(args, fmt.Sprintf("target_port:=%d", cfg.GrpcTargetPort))
	}
	// JSON 문자열 인자는 launch 파일에서 ParameterValue(value_type=str)로 감싸
	// YAML 재해석을 막으므로, launch 인자는 raw 값 그대로 전달한다.
	// (따옴표를 덧붙이면 값에 작은따옴표가 남아 노드의 json.loads가 실패한다)
	if v := strings.TrimSpace(cfg.GrpcTargetPeersJson); v != "" {
		args = append(args, fmt.Sprintf("target_peers_json:=%s", v))
	}
	if v := env["GRPC_PEER_TOKEN"]; v != "" {
		args = append(args, fmt.Sprintf("grpc_peer_token:=%s", v))
	}
	if v := env["GRPC_PEER_TOKENS_JSON"]; v != "" {
		args = append(args, fmt.Sprintf("grpc_peer_tokens_json:=%s", v))
	}
	if v := env["GRPC_MAX_FILE_BYTES"]; v != "" {
		args = append(args, fmt.Sprintf("grpc_max_file_bytes:=%s", v))
	}
	if v := env["GRPC_DEDUP_PATH"]; v != "" {
		args = append(args, fmt.Sprintf("grpc_dedup_path:=%s", v))
	}
	if v := env[contract.RuntimeFields["messenger.server.port"].Env]; v != "" {
		launchArgument := contract.RuntimeFields["messenger.server.port"].LaunchArgument
		args = append(args, fmt.Sprintf("%s:=%s", launchArgument, v))
	}

	// HTTP 설정
	if v := env["RC_HTTP_HOST"]; v != "" {
		args = append(args, fmt.Sprintf("http_host:=%s", v))
	}
	args = append(args, fmt.Sprintf("http_port:=%s", lctx.ActualPort))

	if v := env["RC_OLLAMA_BASE_URL"]; v != "" {
		args = append(args, fmt.Sprintf("llm_base_url:=%s", v))
	}
	if v := env["RC_OLLAMA_OPTIONS_JSON"]; v != "" {
		args = append(args, fmt.Sprintf("ollama_options_json:=%s", v))
	}

	// HTTP 보안 설정
	if v := env["RC_HTTP_READONLY_TOKEN"]; v != "" {
		args = append(args, fmt.Sprintf("http_readonly_token:=%s", v))
	}
	if v := env["RC_HTTP_CONTROL_TOKEN"]; v != "" {
		args = append(args, fmt.Sprintf("http_control_token:=%s", v))
	}
	if v := env["RC_HTTP_RATE_LIMIT_PER_MINUTE"]; v != "" {
		args = append(args, fmt.Sprintf("http_rate_limit_per_minute:=%s", v))
	}
	if v := env["RC_HTTP_ALLOWED_CIDRS_JSON"]; v != "" {
		args = append(args, fmt.Sprintf("http_allowed_cidrs_json:=%s", v))
	}
	if v := env["RC_HTTP_ALLOWED_SKILLS_JSON"]; v != "" {
		args = append(args, fmt.Sprintf("http_allowed_skills_json:=%s", v))
	}
	if v := env["RC_HTTP_BLOCKED_SKILLS_JSON"]; v != "" {
		args = append(args, fmt.Sprintf("http_blocked_skills_json:=%s", v))
	}

	// MCP(Model Context Protocol) 서버 연동 설정
	if v := env["RC_ENABLE_MCP"]; v != "" {
		args = append(args, fmt.Sprintf("mcp_enabled:=%s", v))
	}
	if v := env["RC_MCP_SERVERS_JSON"]; v != "" {
		args = append(args, fmt.Sprintf("mcp_servers_json:=%s", v))
	}

	// LLM API 자격 증명 (OpenAI, Azure, Anthropic)
	if v := env["AZURE_OPENAI_ENDPOINT"]; v != "" {
		args = append(args, fmt.Sprintf("azure_endpoint:=%s", v))
	}
	if v := env["AZURE_OPENAI_API_KEY"]; v != "" {
		args = append(args, fmt.Sprintf("azure_api_key:=%s", v))
	}
	if v := env["OPENAI_API_KEY"]; v != "" {
		args = append(args, fmt.Sprintf("openai_api_key:=%s", v))
	}
	if v := env["ANTHROPIC_API_KEY"]; v != "" {
		args = append(args, fmt.Sprintf("anthropic_api_key:=%s", v))
	}

	// RAG / Qdrant 설정
	if v := env["RC_ENABLE_RAG"]; v != "" {
		args = append(args, fmt.Sprintf("enable_rag:=%s", v))
	}
	if v := env["RC_LLM_EMBEDDING_MODEL"]; v != "" {
		args = append(args, fmt.Sprintf("llm_embedding_model:=%s", v))
	}
	if v := env["RC_LLM_EMBEDDING_PROVIDER"]; v != "" {
		args = append(args, fmt.Sprintf("llm_embedding_provider:=%s", v))
	}
	if v := env["RC_LLM_EMBEDDING_BASE_URL"]; v != "" {
		args = append(args, fmt.Sprintf("llm_embedding_base_url:=%s", v))
	}
	if v := env["RC_LLM_EMBEDDING_API_KEY"]; v != "" {
		args = append(args, fmt.Sprintf("llm_embedding_api_key:=%s", v))
	}
	if v := env["RC_RAG_VECTOR_BACKEND"]; v != "" {
		args = append(args, fmt.Sprintf("rag_vector_backend:=%s", v))
	}
	if v := env["RC_RAG_TOP_K"]; v != "" {
		args = append(args, fmt.Sprintf("rag_top_k:=%s", v))
	}
	if v := env["RC_RAG_SCORE_THRESHOLD"]; v != "" {
		args = append(args, fmt.Sprintf("rag_score_threshold:=%s", v))
	}
	if v := env["QDRANT_URL"]; v != "" {
		args = append(args, fmt.Sprintf("qdrant_url:=%s", v))
	}
	if v := env["QDRANT_API_KEY"]; v != "" {
		args = append(args, fmt.Sprintf("qdrant_api_key:=%s", v))
	}
	if v := env["RC_QDRANT_COLLECTION"]; v != "" {
		args = append(args, fmt.Sprintf("qdrant_collection:=%s", v))
	}
	if v := env["RC_QDRANT_TIMEOUT_SEC"]; v != "" {
		args = append(args, fmt.Sprintf("qdrant_timeout_sec:=%s", v))
	}
	if v := env["RC_RAG_LOCAL_MIRROR"]; v != "" {
		args = append(args, fmt.Sprintf("rag_local_mirror:=%s", v))
	}

	// 스킬 자가학습 설정 (실패는 항상 수집, 성공은 샘플링; enable_rag 필요)
	if v := env["RC_ENABLE_SKILL_LEARNING"]; v != "" {
		args = append(args, fmt.Sprintf("enable_skill_learning:=%s", v))
	}
	if v := env["RC_SKILL_LEARNING_SUCCESS_SAMPLE_RATE"]; v != "" {
		args = append(args, fmt.Sprintf("skill_learning_success_sample_rate:=%s", v))
	}
	if v := env["RC_SKILL_LEARNING_REFLECT_INTERVAL_SEC"]; v != "" {
		args = append(args, fmt.Sprintf("skill_learning_reflect_interval_sec:=%s", v))
	}

	// 태스크 큐 / 복합 명령 자동 분해 설정
	if v := env["RC_TASK_QUEUE_MAX_SIZE"]; v != "" {
		args = append(args, fmt.Sprintf("task_queue_max_size:=%s", v))
	}
	if v := env["RC_LLM_FAIL_FAST"]; v != "" {
		args = append(args, fmt.Sprintf("llm_fail_fast:=%s", v))
	}
	if v := env["RC_STRICT_CONFIG"]; v != "" {
		args = append(args, fmt.Sprintf("strict_config:=%s", v))
	}
	if v := env["RC_ENABLE_TASK_DECOMPOSITION"]; v != "" {
		args = append(args, fmt.Sprintf("enable_task_decomposition:=%s", v))
	}
	if v := env["RC_TASK_DECOMPOSITION_MAX_STEPS"]; v != "" {
		args = append(args, fmt.Sprintf("task_decomposition_max_steps:=%s", v))
	}
	if v := env["RC_TASK_STEP_MAX_RETRIES"]; v != "" {
		args = append(args, fmt.Sprintf("task_step_max_retries:=%s", v))
	}
	if v := env["RC_TASK_DECOMPOSITION_WAIT_MARGIN_CAP_SEC"]; v != "" {
		args = append(args, fmt.Sprintf("task_decomposition_wait_margin_cap_sec:=%s", v))
	}

	// 메신저 토큰
	if v := env["TELEGRAM_BOT_TOKEN"]; v != "" {
		args = append(args, fmt.Sprintf("telegram_token:=%s", v))
	}
	if v := env["SLACK_APP_TOKEN"]; v != "" {
		args = append(args, fmt.Sprintf("slack_app_token:=%s", v))
	}
	if v := env["SLACK_BOT_TOKEN"]; v != "" {
		args = append(args, fmt.Sprintf("slack_bot_token:=%s", v))
	}
	if v := env["DISCORD_BOT_TOKEN"]; v != "" {
		args = append(args, fmt.Sprintf("discord_token:=%s", v))
	}

	// URDF 로봇 사양 및 기타 변수
	if v := env["RC_ROBOT_DESCRIPTION_FILE"]; v != "" {
		args = append(args, fmt.Sprintf("robot_description_file:=%s", v))
	}
	if v := env["USE_GRPC"]; v != "" {
		args = append(args, fmt.Sprintf("use_grpc:=%s", v))
	}

	// 카메라/비전 설정 — 원격 설정으로 카메라 토픽과 ONNX 비전 노드를 제어한다.
	// run_robo_claw_docker.sh의 RC_CAMERA_TOPIC/RC_USE_VISION 매핑과 동일.
	if v := env["RC_CAMERA_TOPIC"]; v != "" {
		args = append(args, fmt.Sprintf("camera_topic:=%s", v))
	}
	if v := env["RC_GRIPPER_CAMERA_TOPIC"]; v != "" {
		args = append(args, fmt.Sprintf("gripper_camera_topic:=%s", v))
	}
	if v := env["RC_GRIPPER_DEPTH_TOPIC"]; v != "" {
		args = append(args, fmt.Sprintf("gripper_depth_topic:=%s", v))
	}
	if v := env["RC_GRIPPER_CAMERA_INFO_TOPIC"]; v != "" {
		args = append(args, fmt.Sprintf("gripper_camera_info_topic:=%s", v))
	}
	if v := env["RC_GRIPPER_POINTCLOUD_TOPIC"]; v != "" {
		args = append(args, fmt.Sprintf("gripper_pointcloud_topic:=%s", v))
	}
	if v := env["RC_USE_VISION"]; v != "" {
		args = append(args, fmt.Sprintf("use_vision:=%s", v))
	}
	if v := env["RC_USE_GRIPPER_VISION"]; v != "" {
		args = append(args, fmt.Sprintf("use_gripper_vision:=%s", v))
	}
	if v := env["RC_GRIPPER_VISION_MAX_INFERENCE_HZ"]; v != "" {
		args = append(args, fmt.Sprintf("gripper_vision_max_inference_hz:=%s", v))
	}
	if v := resolveVisionModelPath(lctx, env["RC_VISION_MODEL_PATH"]); v != "" {
		args = append(args, fmt.Sprintf("vision_model_path:=%s", v))
	}
	// 자가진단용 센서 토픽 — robot_config YAML 기본값을 .env로 override.
	if v := env["RC_LIDAR_TOPIC"]; v != "" {
		args = append(args, fmt.Sprintf("lidar_topic:=%s", v))
	}
	if v := env["RC_IMU_TOPIC"]; v != "" {
		args = append(args, fmt.Sprintf("imu_topic:=%s", v))
	}

	return args
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return strings.TrimSpace(value)
		}
	}
	return ""
}

func resolveVisionModelPath(lctx *LaunchContext, requested string) string {
	if requested == "" || !lctx.Docker {
		return requested
	}

	projectModelsDir := filepath.Join(lctx.ProjectRoot, "models")
	if filepath.IsAbs(requested) {
		modelName := filepath.Base(requested)
		if PathExists(filepath.Join(projectModelsDir, modelName)) {
			return filepath.Join("/ros2_ws/models", modelName)
		}
		if PathExists(requested) {
			return requested
		}
	}

	for _, modelName := range []string{"yolov8n.onnx", "yolov8s.onnx", "yolov8m.onnx", "yolov8l.onnx", "yolov8x.onnx"} {
		if PathExists(filepath.Join(projectModelsDir, modelName)) {
			return filepath.Join("/ros2_ws/models", modelName)
		}
	}

	return requested
}

// BuildSimArgs 시뮬레이션 모드 전용 런치 아규먼트를 빌드합니다.
func BuildSimArgs(lctx *LaunchContext) []string {
	var args []string

	simEngine := "classic"
	if lctx.RosDistro == "jazzy" {
		simEngine = "gz"
	}
	args = append(args, fmt.Sprintf("sim_engine:=%s", simEngine))

	// Slam이 Nav2보다 우선: Slam 활성화 시 Nav2도 함께 활성화됩니다.
	if lctx.Slam {
		args = append(args, "use_slam:=true", "use_nav2:=true")
	} else if lctx.Nav2 {
		args = append(args, "use_nav2:=true")
	}

	if lctx.World != "" {
		args = append(args, fmt.Sprintf("world:=%s", lctx.World))
	}
	if lctx.RobotModel != "" {
		args = append(args, fmt.Sprintf("robot_model:=%s", lctx.RobotModel))
	}
	if lctx.MapFile != "" {
		args = append(args, fmt.Sprintf("map:=%s", lctx.MapFile))
	}

	return args
}
