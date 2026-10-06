package launcher

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
)

// RoboClawConfig는 ai-config-server로부터 수신하는 설정 정보의 구조체입니다.
type RoboClawConfig struct {
	ID          uint      `json:"id"`
	CreatedAt   time.Time `json:"created_at"`
	UpdatedAt   time.Time `json:"updated_at"`
	Name        string    `json:"name"`
	RobotName   string    `json:"robot_name"`
	Environment string    `json:"environment"`
	IsActive    bool      `json:"is_active"`
	Description string    `json:"description"`

	// ROS 2 설정
	RosDomainId int    `json:"ros_domain_id"`
	AgentId     string `json:"agent_id"`

	// LLM 기본 설정
	LlmProvider string `json:"llm_provider"`
	LlmModel    string `json:"llm_model"`

	// Azure / API 자격 증명
	AzureOpenaiEndpoint string `json:"azure_openai_endpoint"`
	AzureOpenaiApiKey   string `json:"azure_openai_api_key"`
	OpenaiApiKey        string `json:"openai_api_key"`
	AnthropicApiKey     string `json:"anthropic_api_key"`

	// Ollama 상세 설정
	OllamaBaseUrl     string `json:"ollama_base_url"`
	OllamaOptionsJson string `json:"ollama_options_json"`

	// RAG / Qdrant 설정
	EnableRag            bool   `json:"enable_rag"`
	LlmEmbeddingModel    string `json:"llm_embedding_model"`
	LlmEmbeddingProvider string `json:"llm_embedding_provider"`
	LlmEmbeddingBaseUrl  string `json:"llm_embedding_base_url"`
	LlmEmbeddingApiKey   string `json:"llm_embedding_api_key"`
	RagVectorBackend     string `json:"rag_vector_backend"`
	QdrantUrl            string `json:"qdrant_url"`
	QdrantCollection     string `json:"qdrant_collection"`

	// 메신저 연동 활성화 설정
	EnableDiscord       bool   `json:"enable_discord"`
	EnableTelegram      bool   `json:"enable_telegram"`
	EnableSlack         bool   `json:"enable_slack"`
	EnableGrpc          bool   `json:"enable_grpc"`
	UseGrpc             bool   `json:"use_grpc"`
	EnableGrpcClient    bool   `json:"enable_grpc_client"`
	GrpcTargetHost      string `json:"grpc_target_host"`
	GrpcTargetPort      int    `json:"grpc_target_port"`
	GrpcTargetPeersJson string `json:"grpc_target_peers_json"`
	DiscordBotToken     string `json:"discord_bot_token"`
	SlackAppToken       string `json:"slack_app_token"`
	SlackBotToken       string `json:"slack_bot_token"`
	TelegramBotToken    string `json:"telegram_bot_token"`

	// 마크다운 및 JSON 파일 내용
	SoulContent            string `json:"soul_content"`
	SkillsContent          string `json:"skills_content"`
	TroubleshootingContent string `json:"troubleshooting_content"`
	LimitsContent          string `json:"limits_content"`

	// HTTP API 보안 설정
	HttpHost               string `json:"http_host"`
	HttpPort               int    `json:"http_port"`
	HttpReadonlyToken      string `json:"http_readonly_token"`
	HttpControlToken       string `json:"http_control_token"`
	HttpAllowedCidrsJson   string `json:"http_allowed_cidrs_json"`
	HttpRateLimitPerMinute int    `json:"http_rate_limit_per_minute"`
	HttpAllowedSkillsJson  string `json:"http_allowed_skills_json"`
	HttpBlockedSkillsJson  string `json:"http_blocked_skills_json"`

	// MCP(Model Context Protocol) 서버 연동
	EnableMcp      bool   `json:"enable_mcp"`
	McpServersJson string `json:"mcp_servers_json"`

	// RAG 추가 설정
	RagTopK           int     `json:"rag_top_k"`
	RagScoreThreshold float64 `json:"rag_score_threshold"`
	QdrantApiKey      string  `json:"qdrant_api_key"`
	QdrantTimeoutSec  float64 `json:"qdrant_timeout_sec"`

	// 경로 및 리소스 설정
	AgentWorkspaceDir    string `json:"agent_workspace_dir"`
	ConfigDir            string `json:"config_dir"`
	SystemPromptFile     string `json:"system_prompt_file"`
	RobotDescriptionFile string `json:"robot_description_file"`

	// 디버그 설정
	Debug bool `json:"debug"`

	// ── LangSmith 트레이싱 / 모니터링 설정 ──
	LangsmithTracing     bool   `gorm:"default:false" json:"langsmith_tracing"` // LANGSMITH_TRACING (트레이싱 활성화)
	LangsmithApiKey      string `json:"langsmith_api_key"`                      // LANGSMITH_API_KEY (LangSmith API 키)
	LangsmithProject     string `json:"langsmith_project"`                      // LANGSMITH_PROJECT (프로젝트 이름, 선택)
	LangsmithEndpoint    string `json:"langsmith_endpoint"`                     // LANGSMITH_ENDPOINT (자체 호스팅/프록시 엔드포인트, 선택)
	LangsmithWorkspaceId string `json:"langsmith_workspace_id"`                 // LANGSMITH_WORKSPACE_ID (workspace ID, 선택)

	RagLocalMirror                    bool    `json:"rag_local_mirror"`
	MemoryDir                         string  `json:"memory_dir"`
	ButlerScriptsDir                  string  `json:"butler_scripts_dir"`
	ButlerSourceDir                   string  `json:"butler_source_dir"`
	CameraTopic                       string  `json:"camera_topic"`
	UseVision                         bool    `json:"use_vision"`
	VisionModelPath                   string  `json:"vision_model_path"`
	GripperCameraTopic                string  `json:"gripper_camera_topic"`
	GripperDepthTopic                 string  `json:"gripper_depth_topic"`
	GripperCameraInfoTopic            string  `json:"gripper_camera_info_topic"`
	GripperPointcloudTopic            string  `json:"gripper_pointcloud_topic"`
	UseGripperVision                  bool    `json:"use_gripper_vision"`
	GripperVisionMaxInferenceHz       float64 `json:"gripper_vision_max_inference_hz"`
	LidarTopic                        string  `json:"lidar_topic"`
	ImuTopic                          string  `json:"imu_topic"`
	EnableSkillLearning               bool    `json:"enable_skill_learning"`
	SkillLearningSuccessSampleRate    float64 `json:"skill_learning_success_sample_rate"`
	SkillLearningReflectIntervalSec   int     `json:"skill_learning_reflect_interval_sec"`
	TaskQueueMaxSize                  int     `json:"task_queue_max_size"`
	LlmFailFast                       bool    `json:"llm_fail_fast"`
	StrictConfig                      bool    `json:"strict_config"`
	EnableTaskDecomposition           bool    `json:"enable_task_decomposition"`
	TaskDecompositionMaxSteps         int     `json:"task_decomposition_max_steps"`
	TaskStepMaxRetries                int     `json:"task_step_max_retries"`
	TaskDecompositionWaitMarginCapSec float64 `json:"task_decomposition_wait_margin_cap_sec"`
}

// RobotLimits ROBOT_LIMITS.json 파싱 구조체
type RobotLimits struct {
	Navigation struct {
		MaxLinearVelocityMps float64 `json:"max_linear_velocity_mps"`
	} `json:"navigation"`
	MaxLinearVelocity float64 `json:"max_linear_velocity"`
}

// LaunchFlags 커맨드 플래그 값을 담습니다.
type LaunchFlags struct {
	Debug      bool
	Sim        bool
	Nav2       bool
	Slam       bool
	Docker     bool
	World      string
	RobotModel string
	MapFile    string
	ImageName  string // (Docker) 사용할 이미지명. 비면 기본값(robo-claw).
	ImageTag   string // (Docker) 사용할 이미지 태그. 비면 "latest".
	Pull       bool   // (Docker) 실행 직전 docker pull 수행 여부.
	ForceGrpc  bool   // gRPC 서버 노드(use_grpc) 강제 활성화. robo_claw_cli test 연동에 필요.
}

// LaunchContext launch 커맨드 실행에 필요한 모든 상태를 보관합니다.
type LaunchContext struct {
	LaunchFlags

	// 식별 정보
	RobotName string
	EnvName   string

	// 파생 경로
	CacheDir     string
	EnvFilePath  string
	ProjectRoot  string
	LaunchTarget string

	// ROS 설정
	RosDistro  string
	ActualPort string

	// 로드된 설정
	ActiveConfig RoboClawConfig
	EnvSlice     []string
	EnvMap       map[string]string

	// 공통 런치 아규먼트 (buildCommonLaunchArgs 결과)
	CommonLaunchArgs []string
}

// LoadLaunchContext 원격/로컬 캐시 설정을 읽어 LaunchContext를 초기화합니다.
// 순환 참조 방지를 위해 fetchConfig 함수를 주입받아 처리합니다.
func LoadLaunchContext(
	ctx *wcli.Context,
	robotName, envName string,
	flags LaunchFlags,
	fetchFn func(*wcli.Context, string, string) error,
) (*LaunchContext, error) {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("홈 디렉토리를 가져올 수 없습니다: %w", err)
	}

	cacheDir := filepath.Join(homeDir, ".robo_claw", "config_cache", fmt.Sprintf("%s_%s", robotName, envName))

	// 원격 설정 동기화 (실패 시 로컬 캐시로 폴백)
	logging.Info("원격 서버에서 설정 동기화 시도 중... (로봇: %s, 환경: %s)", robotName, envName)
	rich.Println("[cyan]⚡[/cyan] 원격 서버에서 설정 동기화 시도 중...")
	if fetchErr := fetchFn(ctx, robotName, envName); fetchErr != nil {
		logging.Warn("원격 설정 서버 연결 실패: %v. 로컬 캐시 폴백을 시도합니다.", fetchErr)
		rich.Println("[yellow]⚠ 경고: 원격 설정 서버 연결 실패 (%v). 로컬 캐시를 사용합니다.[/yellow]", fetchErr)

		configPath := filepath.Join(cacheDir, "config.json")
		if _, err := os.Stat(configPath); os.IsNotExist(err) {
			// 이 에러는 launcher 패키지가 wcli의 에러나 cmd의 에러 등을 직접 던지기보다는
			// 표준 error를 래핑하거나 launcher 내부 에러 구조체로 처리하는 것이 좋습니다.
			return nil, fmt.Errorf("설정 캐시를 찾을 수 없습니다. 원격 서버 및 로컬 캐시가 모두 존재하지 않습니다 (네트워크 오류: %w)", fetchErr)
		}
	} else {
		logging.Info("원격 설정 동기화 성공")
		rich.Println("[green]✔[/green] 원격 설정 동기화 성공")
	}

	// config.json 로드
	configPath := filepath.Join(cacheDir, "config.json")
	logging.Debug("설정 파일 로드 시도: %s", configPath)
	configData, err := os.ReadFile(configPath)
	if err != nil {
		return nil, fmt.Errorf("설정 파일 읽기 실패 (%s): %w", configPath, err)
	}
	var activeConfig RoboClawConfig
	if err := json.Unmarshal(configData, &activeConfig); err != nil {
		return nil, fmt.Errorf("설정 파일 역직렬화 실패: %w", err)
	}

	// .env 파일 파싱
	envFilePath := filepath.Join(cacheDir, ".env")
	logging.Debug(".env 파일 파싱 시도: %s", envFilePath)
	envSlice, envMap, err := ParseEnvFile(envFilePath)
	if err != nil {
		logging.Warn("캐시된 .env 파일 파싱 실패: %v", err)
		rich.Println("[yellow]⚠ 경고: 캐시된 .env 파일이 없거나 파싱 실패했습니다. (%v)[/yellow]", err)
		envMap = make(map[string]string)
	}

	// ROS Distro 감지
	rosDistro := os.Getenv("ROS_DISTRO")
	if rosDistro == "" {
		rosDistro = "jazzy" // 기본값
	}
	logging.Debug("감지된 ROS_DISTRO: %s", rosDistro)

	// 채널 서버 포트 결정 (충돌 회피)
	httpPort := envMap["RC_HTTP_PORT"]
	if httpPort == "" {
		httpPort = "8080"
	}
	actualPort := httpPort
	if !IsPortAvailable(httpPort) {
		actualPort = FindAvailablePort(httpPort)
		logging.Warn("포트 %s가 사용 중이므로 %s로 자동 조정합니다.", httpPort, actualPort)
		rich.Println("[yellow]⚠ 포트 %s가 이미 사용 중입니다. 채널 서버 포트를 %s로 자동 조정합니다.[/yellow]", httpPort, actualPort)
	}

	// 런치 파일 타겟 결정
	launchTarget := "robo_claw.launch.py"
	if flags.Sim {
		launchTarget = "robo_claw_sim.launch.py"
	}
	logging.Debug("설정된 Launch 타겟: %s", launchTarget)

	lctx := &LaunchContext{
		LaunchFlags:  flags,
		RobotName:    robotName,
		EnvName:      envName,
		CacheDir:     cacheDir,
		EnvFilePath:  envFilePath,
		ProjectRoot:  GetProjectRoot(),
		LaunchTarget: launchTarget,
		RosDistro:    rosDistro,
		ActualPort:   actualPort,
		ActiveConfig: activeConfig,
		EnvSlice:     envSlice,
		EnvMap:       envMap,
	}
	lctx.CommonLaunchArgs = BuildCommonLaunchArgs(lctx)

	return lctx, nil
}
