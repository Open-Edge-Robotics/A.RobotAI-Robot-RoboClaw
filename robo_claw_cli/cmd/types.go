package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
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

	// 신규 서버 필드도 캐시 JSON에서 보존한다.
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
	LangsmithTracing                  bool    `json:"langsmith_tracing"`
	LangsmithApiKey                   string  `json:"langsmith_api_key"`
	LangsmithProject                  string  `json:"langsmith_project"`
	LangsmithEndpoint                 string  `json:"langsmith_endpoint"`
	LangsmithWorkspaceId              string  `json:"langsmith_workspace_id"`
}

// NetworkError 원격 서버와의 통신 에러를 나타냅니다.
type NetworkError struct {
	URL        string
	StatusCode int
	Err        error
	ServerMsg  string // 서버가 반환한 추가 메시지(JSON error/details 등)
}

func (e *NetworkError) FriendlyMessage() string {
	if e.StatusCode == http.StatusUnauthorized {
		return "원격 설정 서버 인증에 실패했습니다. config.yaml의 device_token 값이 유효한지 확인하세요."
	}
	return "원격 설정 서버와의 네트워크 연결에 실패했습니다. Wi-Fi/이더넷 상태 및 config.yaml에 등록된 서버 설정(Host, Port)을 확인하고, 오프라인 환경인 경우 로컬 캐시를 사용해 구동하십시오."
}

func (e *NetworkError) Error() string {
	msg := e.Err.Error()
	if e.ServerMsg != "" {
		msg = fmt.Sprintf("%s (서버 응답: %s)", msg, e.ServerMsg)
	}
	if e.StatusCode > 0 {
		return fmt.Sprintf("원격 서버 통신 실패 (URL: %s, HTTP %d): %s", e.URL, e.StatusCode, msg)
	}
	return fmt.Sprintf("원격 서버 연결 실패 (URL: %s): %s", e.URL, msg)
}

func (e *NetworkError) Unwrap() error {
	return e.Err
}

// readServerError는 HTTP 응답 본문에서 서버가 반환한 오류 메시지를 추출합니다.
func readServerError(resp *http.Response) string {
	if resp == nil || resp.Body == nil {
		return ""
	}
	// 이미 소진된 본문은 읽을 수 없으므로 최대 4KB 까지만 읽습니다.
	limited := io.LimitReader(resp.Body, 4096)
	body, err := io.ReadAll(limited)
	if err != nil || len(body) == 0 {
		return ""
	}

	// JSON {"error":"...","details":"..."} 형식을 우선 파싱
	var details struct {
		Error   string `json:"error"`
		Details string `json:"details"`
		Message string `json:"message"`
	}
	if err := json.Unmarshal(body, &details); err == nil {
		if details.Details != "" {
			return details.Details
		}
		if details.Message != "" {
			return details.Message
		}
		if details.Error != "" {
			return details.Error
		}
	}
	return strings.TrimSpace(string(body))
}

// CacheError 로컬 설정 캐시 작업 오류를 나타냅니다.
type CacheError struct {
	Path string
	Op   string // "read", "write", "mkdir" 등
	Err  error
}

func (e *CacheError) FriendlyMessage() string {
	return fmt.Sprintf("로컬 캐시 작업(%s) 중 파일 오류가 발생했습니다. 경로(%s)의 권한이나 존재 여부, 그리고 디스크 용량을 확인하십시오.", e.Op, e.Path)
}

func (e *CacheError) Error() string {
	return fmt.Sprintf("캐시 파일 작업 실패 (%s, 경로: %s): %v", e.Op, e.Path, e.Err)
}

func (e *CacheError) Unwrap() error {
	return e.Err
}
