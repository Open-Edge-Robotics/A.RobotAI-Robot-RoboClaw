package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
	"robo_claw_cli/contract"
	"robo_claw_cli/launcher"
)

// BuildRunLaunchArgs .env와 오버라이드 값들로부터 ros2 launch 인자 슬라이스를 구성합니다.
func BuildRunLaunchArgs(envMap map[string]string, llmProvider, llmModel, channel string, extraArgs []string) []string {
	var args []string

	provider := llmProvider
	if provider == "" {
		provider = envMap["RC_LLM_PROVIDER"]
		if provider == "" {
			provider = "azure"
		}
	}

	model := llmModel
	if model == "" {
		model = envMap["RC_LLM_MODEL"]
		if model == "" {
			model = "gpt-4.1"
		}
	}

	useChannel := channel
	if useChannel == "" {
		useChannel = "true"
	}

	args = append(args,
		fmt.Sprintf("llm_provider:=%s", provider),
		fmt.Sprintf("llm_model:=%s", model),
		fmt.Sprintf("use_channel:=%s", useChannel),
		fmt.Sprintf("enable_discord:=%s", getEnvOrDefault(envMap, "ENABLE_DISCORD", "false")),
		fmt.Sprintf("enable_telegram:=%s", getEnvOrDefault(envMap, "ENABLE_TELEGRAM", "false")),
		fmt.Sprintf("enable_slack:=%s", getEnvOrDefault(envMap, "ENABLE_SLACK", "false")),
		fmt.Sprintf("enable_grpc:=%s", getEnvOrDefault(envMap, "ENABLE_GRPC", "true")),
		fmt.Sprintf("use_grpc:=%s", getEnvOrDefault(envMap, "USE_GRPC", "false")),
		fmt.Sprintf("use_client:=%s", getEnvOrDefault(envMap, "ENABLE_GRPC_CLIENT", "false")),
	)

	// 단순 환경변수 매핑 테이블
	envMappings := []struct {
		envKey   string
		paramKey string
	}{
		{"GRPC_TARGET_HOST", "target_host"},
		{"GRPC_TARGET_PORT", "target_port"},
		{"GRPC_TARGET_PEERS_JSON", "target_peers_json"},
		{"RC_AGENT_ID", "agent_id"},
		{"GRPC_PEER_TOKEN", "grpc_peer_token"},
		{"GRPC_PEER_TOKENS_JSON", "grpc_peer_tokens_json"},
		{"GRPC_MAX_FILE_BYTES", "grpc_max_file_bytes"},
		{"GRPC_DEDUP_PATH", "grpc_dedup_path"},
		{"RC_ROBOT_CONFIG", "robot_config"},
		{"RC_HTTP_HOST", "http_host"},
		{"RC_HTTP_PORT", "http_port"},
		{"RC_HTTP_READONLY_TOKEN", "http_readonly_token"},
		{"RC_HTTP_CONTROL_TOKEN", "http_control_token"},
		{"RC_HTTP_ALLOWED_CIDRS_JSON", "http_allowed_cidrs_json"},
		{"RC_HTTP_RATE_LIMIT_PER_MINUTE", "http_rate_limit_per_minute"},
		{"RC_HTTP_ALLOWED_SKILLS_JSON", "http_allowed_skills_json"},
		{"RC_HTTP_BLOCKED_SKILLS_JSON", "http_blocked_skills_json"},
		{"RC_USE_DASHBOARD", "use_dashboard"},
		{"RC_DASHBOARD_HOST", "dashboard_host"},
		{"RC_DASHBOARD_PORT", "dashboard_port"},
		{"RC_ENABLE_MCP", "mcp_enabled"},
		{"RC_MCP_SERVERS_JSON", "mcp_servers_json"},
		{"RC_ROBOT_DESCRIPTION_FILE", "robot_description_file"},
		{"RC_ROBOT_SOUL_FILE", "robot_soul_file"},
		{"RC_SKILLS_GUIDE_FILE", "skills_guide_file"},
		{"RC_ROBOT_LIMITS_FILE", "robot_limits_file"},
		{"RC_TROUBLESHOOTING_GUIDE_FILE", "troubleshooting_guide_file"},
		{"RC_SYSTEM_PROMPT_FILE", "system_prompt_file"},
		{"RC_AGENT_WORKSPACE_DIR", "agent_workspace_dir"},
		{"RC_CAMERA_TOPIC", "camera_topic"},
		{"RC_USE_VISION", "use_vision"},
		{"RC_GRIPPER_CAMERA_TOPIC", "gripper_camera_topic"},
		{"RC_GRIPPER_DEPTH_TOPIC", "gripper_depth_topic"},
		{"RC_GRIPPER_CAMERA_INFO_TOPIC", "gripper_camera_info_topic"},
		{"RC_GRIPPER_POINTCLOUD_TOPIC", "gripper_pointcloud_topic"},
		{"RC_USE_GRIPPER_VISION", "use_gripper_vision"},
		{"RC_GRIPPER_VISION_MAX_INFERENCE_HZ", "gripper_vision_max_inference_hz"},
		{"RC_LIDAR_TOPIC", "lidar_topic"},
		{"RC_IMU_TOPIC", "imu_topic"},
		{"RC_OLLAMA_OPTIONS_JSON", "ollama_options_json"},
		{"RC_LLM_EMBEDDING_MODEL", "llm_embedding_model"},
		{"RC_LLM_EMBEDDING_PROVIDER", "llm_embedding_provider"},
		{"RC_LLM_EMBEDDING_BASE_URL", "llm_embedding_base_url"},
		{"RC_LLM_EMBEDDING_API_KEY", "llm_embedding_api_key"},
		{"RC_OLLAMA_BASE_URL", "llm_base_url"},
		{"RC_ENABLE_RAG", "enable_rag"},
		{"RC_RAG_VECTOR_BACKEND", "rag_vector_backend"},
		{"RC_RAG_TOP_K", "rag_top_k"},
		{"RC_RAG_SCORE_THRESHOLD", "rag_score_threshold"},
		{"RC_RAG_LOCAL_MIRROR", "rag_local_mirror"},
		{"RC_ENABLE_SKILL_LEARNING", "enable_skill_learning"},
		{"RC_SKILL_LEARNING_SUCCESS_SAMPLE_RATE", "skill_learning_success_sample_rate"},
		{"RC_SKILL_LEARNING_REFLECT_INTERVAL_SEC", "skill_learning_reflect_interval_sec"},
		{"QDRANT_URL", "qdrant_url"},
		{"QDRANT_API_KEY", "qdrant_api_key"},
		{"RC_QDRANT_COLLECTION", "qdrant_collection"},
		{"RC_QDRANT_TIMEOUT_SEC", "qdrant_timeout_sec"},
		{"RC_TASK_QUEUE_MAX_SIZE", "task_queue_max_size"},
		{"RC_LLM_FAIL_FAST", "llm_fail_fast"},
		{"RC_STRICT_CONFIG", "strict_config"},
		{"RC_ENABLE_TASK_DECOMPOSITION", "enable_task_decomposition"},
		{"RC_TASK_DECOMPOSITION_MAX_STEPS", "task_decomposition_max_steps"},
		{"RC_TASK_STEP_MAX_RETRIES", "task_step_max_retries"},
		{"RC_TASK_DECOMPOSITION_WAIT_MARGIN_CAP_SEC", "task_decomposition_wait_margin_cap_sec"},
		{"AZURE_OPENAI_ENDPOINT", "azure_endpoint"},
		{"AZURE_OPENAI_API_KEY", "azure_api_key"},
		{"OPENAI_API_KEY", "openai_api_key"},
		{"ANTHROPIC_API_KEY", "anthropic_api_key"},
		{"TELEGRAM_BOT_TOKEN", "telegram_token"},
		{"SLACK_APP_TOKEN", "slack_app_token"},
		{"SLACK_BOT_TOKEN", "slack_bot_token"},
		{"DISCORD_BOT_TOKEN", "discord_token"},
	}

	for _, m := range envMappings {
		if v := strings.TrimSpace(envMap[m.envKey]); v != "" {
			args = append(args, fmt.Sprintf("%s:=%s", m.paramKey, v))
		}
	}

	if v := envMap[contract.RuntimeFields["messenger.server.port"].Env]; v != "" {
		launchArg := contract.RuntimeFields["messenger.server.port"].LaunchArgument
		args = append(args, fmt.Sprintf("%s:=%s", launchArg, v))
	}

	if memDir := envMap["RC_MEMORY_DIR"]; memDir != "" {
		args = append(args, fmt.Sprintf("memory_path:=%s", filepath.Join(memDir, "memory.json")))
	}

	// 비전 모델 경로 기본값
	root := launcher.GetProjectRoot()
	modelName := getEnvOrDefault(envMap, "RC_MODEL_NAME", "yolov8n")
	defaultVisionPath := filepath.Join(root, "models", modelName+".onnx")
	visionModelPath := getEnvOrDefault(envMap, "RC_VISION_MODEL_PATH", defaultVisionPath)
	args = append(args, fmt.Sprintf("vision_model_path:=%s", visionModelPath))

	args = append(args, extraArgs...)
	return args
}

func getEnvOrDefault(envMap map[string]string, key, fallback string) string {
	if v, ok := envMap[key]; ok && strings.TrimSpace(v) != "" {
		return strings.TrimSpace(v)
	}
	if v := os.Getenv(key); strings.TrimSpace(v) != "" {
		return strings.TrimSpace(v)
	}
	return fallback
}

func executeRosLaunch(launchFile string, launchArgs []string, debug bool, isSim bool) error {
	projectRoot := launcher.GetProjectRoot()

	// 1. 기존 프로세스 정리
	logging.Info("이전 RoboClaw 프로세스 정리 중...")
	_ = KillRoboClawProcs([]int{8080, 50051, 50052})

	// 2. 환경변수 준비
	env := os.Environ()
	env = append(env,
		"RMW_FASTRTPS_PUBLICATION_MODE=ASYNCHRONOUS",
		"RMW_IMPLEMENTATION=rmw_cyclonedds_cpp",
		"CYCLONEDDS_URI=<CycloneDDS><Domain><General><MaxMessageSize>131072B</MaxMessageSize><FragmentSize>1300B</FragmentSize></General><Internal><Watermarks><WhcHigh>2097152B</WhcHigh></Watermarks></Internal></Domain></CycloneDDS>",
	)

	if debug {
		env = append(env, "RCUTILS_LOGGING_MIN_SEVERITY_LEVEL=DEBUG")
	}

	if isSim {
		env = append(env, "RCUTILS_COLORIZED_OUTPUT=1", "QT_QPA_PLATFORM=xcb", "GZ_RENDERING_BACKEND=ogre2")
		gzModelPath := filepath.Join(projectRoot, "install", "robo_claw_bringup", "share", "robo_claw_bringup", "models")
		env = append(env, fmt.Sprintf("GZ_SIM_RESOURCE_PATH=%s", gzModelPath))
	}

	argsToExec := append([]string{"launch", "robo_claw_bringup", launchFile}, launchArgs...)
	logging.Info("실행 명령어: ros2 %s", strings.Join(argsToExec, " "))
	rich.Println("[green]▶[/green] 실행 명령어: [bold]ros2 %s[/bold]", strings.Join(argsToExec, " "))

	helperScript := filepath.Join(projectRoot, "scripts", "run_in_ros_env.sh")
	var cmd *exec.Cmd
	if launcher.PathExists(helperScript) {
		cmdArgs := append([]string{"--ws", "ros2"}, argsToExec...)
		cmd = exec.Command(helperScript, cmdArgs...)
	} else {
		cmd = exec.Command("ros2", argsToExec...)
	}

	cmd.Env = env
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin

	return cmd.Run()
}

// RunCmd 로컬 설정을 기반으로 RoboClaw 에이전트를 기동합니다.
func RunCmd() *wcli.Command {
	var debug bool

	cmd := &wcli.Command{
		Use:   "run [llm] [model] [channel]",
		Short: "로컬 환경(.env)을 기반으로 RoboClaw 시스템을 기동합니다.",
		Run: func(ctx *wcli.Context) error {
			projectRoot := launcher.GetProjectRoot()
			envFile := filepath.Join(projectRoot, ".env")
			_, envMap, _ := launcher.ParseEnvFile(envFile)
			if envMap == nil {
				envMap = make(map[string]string)
			}

			var positional []string
			var extraArgs []string
			for _, arg := range ctx.Args {
				if strings.Contains(arg, ":=") {
					extraArgs = append(extraArgs, arg)
				} else {
					positional = append(positional, arg)
				}
			}

			llmProvider := ""
			llmModel := ""
			channel := ""
			if len(positional) > 0 {
				llmProvider = positional[0]
			}
			if len(positional) > 1 {
				llmModel = positional[1]
			}
			if len(positional) > 2 {
				channel = positional[2]
			}

			launchArgs := BuildRunLaunchArgs(envMap, llmProvider, llmModel, channel, extraArgs)
			return executeRosLaunch("robo_claw.launch.py", launchArgs, debug, false)
		},
	}

	cmd.Flags().BoolVar(&debug, "debug", "d", false, "ROS 2 디버그 로깅 활성화")
	return cmd
}

// SimCmd 로컬 설정을 기반으로 시뮬레이션 환경을 기동합니다.
func SimCmd() *wcli.Command {
	var debug bool
	var nav2 bool
	var slam bool
	var world string
	var model string
	var mapFile string

	cmd := &wcli.Command{
		Use:   "sim [llm] [model] [channel]",
		Short: "로컬 환경(.env)을 기반으로 시뮬레이션 환경을 기동합니다.",
		Run: func(ctx *wcli.Context) error {
			projectRoot := launcher.GetProjectRoot()
			envFile := filepath.Join(projectRoot, ".env")
			_, envMap, _ := launcher.ParseEnvFile(envFile)
			if envMap == nil {
				envMap = make(map[string]string)
			}

			var positional []string
			var extraArgs []string
			for _, arg := range ctx.Args {
				if strings.Contains(arg, ":=") {
					extraArgs = append(extraArgs, arg)
				} else {
					positional = append(positional, arg)
				}
			}

			if nav2 {
				extraArgs = append(extraArgs, "use_nav2:=true")
			}
			if slam {
				extraArgs = append(extraArgs, "use_slam:=true", "use_nav2:=true")
			}
			if world != "" {
				extraArgs = append(extraArgs, fmt.Sprintf("world:=%s", world))
			}
			if model != "" {
				extraArgs = append(extraArgs, fmt.Sprintf("robot_model:=%s", model))
			}
			if mapFile != "" {
				extraArgs = append(extraArgs, fmt.Sprintf("map:=%s", mapFile))
			}

			llmProvider := ""
			llmModel := ""
			channel := ""
			if len(positional) > 0 {
				llmProvider = positional[0]
			}
			if len(positional) > 1 {
				llmModel = positional[1]
			}
			if len(positional) > 2 {
				channel = positional[2]
			}

			launchArgs := BuildRunLaunchArgs(envMap, llmProvider, llmModel, channel, extraArgs)
			return executeRosLaunch("robo_claw_sim.launch.py", launchArgs, debug, true)
		},
	}

	cmd.Flags().BoolVar(&debug, "debug", "d", false, "ROS 2 디버그 로깅 활성화")
	cmd.Flags().BoolVar(&nav2, "nav2", "", false, "Nav2 내비게이션 활성화")
	cmd.Flags().BoolVar(&slam, "slam", "", false, "SLAM 및 Nav2 동시 활성화")
	cmd.Flags().StringVar(&world, "world", "", "", "시뮬레이션 월드 파일명")
	cmd.Flags().StringVar(&model, "model", "", "", "로봇 모델 지정")
	cmd.Flags().StringVar(&mapFile, "map", "", "", "맵 파일 경로 지정")

	return cmd
}
