package launcher

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/wkqco33/wcli/rich"
	"robo_claw_cli/contract"
)

// ConfigSummarySource 요약 항목 값의 출처를 나타냅니다.
type ConfigSummarySource string

const (
	// SourceConfig ai-config-server의 config.json 값.
	SourceConfig ConfigSummarySource = "config.json"
	// SourceEnv 서버가 내려준 .env override 값.
	SourceEnv ConfigSummarySource = ".env"
	// SourceRuntime launch 시점에 계산/해석된 값.
	SourceRuntime ConfigSummarySource = "runtime"
)

// ConfigSummaryEntry 적용 설정 요약의 한 항목입니다.
type ConfigSummaryEntry struct {
	Label  string
	Value  string
	Source ConfigSummarySource
	// Note 값에 덧붙는 보조 설명(예: 무시되는 .env 값 안내). 시크릿을 담지 않습니다.
	Note string
}

// ConfigSummarySection 관련 항목을 묶은 섹션입니다.
type ConfigSummarySection struct {
	Title   string
	Entries []ConfigSummaryEntry
}

// maxConfigSummaryLogs 보관하는 적용 설정 로그 파일 최대 개수입니다.
const maxConfigSummaryLogs = 30

// maskPlaceholder 시크릿 값 대체 문자열입니다.
const maskPlaceholder = "********"

// llmOptionOrder Ollama 옵션을 사람이 읽기 좋은 순서로 보여주기 위한 우선순위입니다.
var llmOptionOrder = []string{
	"num_ctx",
	"num_predict",
	"temperature",
	"top_p",
	"top_k",
	"min_p",
	"repeat_penalty",
	"repeat_last_n",
	"seed",
}

type summaryBuilder struct {
	sections []ConfigSummarySection
	env      map[string]string
	cfg      RoboClawConfig
}

func (b *summaryBuilder) start(title string) {
	b.sections = append(b.sections, ConfigSummarySection{Title: title})
}

func (b *summaryBuilder) row(label, value string, source ConfigSummarySource) {
	b.rowNote(label, value, source, "")
}

// rowNote 항목에 보조 설명을 덧붙입니다.
func (b *summaryBuilder) rowNote(label, value string, source ConfigSummarySource, note string) {
	if len(b.sections) == 0 {
		return
	}
	last := len(b.sections) - 1
	b.sections[last].Entries = append(b.sections[last].Entries, ConfigSummaryEntry{
		Label:  label,
		Value:  strings.TrimSpace(value),
		Source: source,
		Note:   strings.TrimSpace(note),
	})
}

// rowValue 값이 비어 있으면 항목을 생략합니다.
func (b *summaryBuilder) rowValue(label, value string, source ConfigSummarySource) {
	if strings.TrimSpace(value) == "" {
		return
	}
	b.row(label, value, source)
}

// envRow .env에만 있는 값을 표시합니다.
func (b *summaryBuilder) envRow(label, envKey string) {
	b.rowValue(label, b.env[envKey], SourceEnv)
}

// envOrCfgRow .env 우선, 없으면 config.json 값을 표시합니다.
// launch 인자 생성(BuildCommonLaunchArgs)과 같은 우선순위를 따른다.
func (b *summaryBuilder) envOrCfgRow(label, envKey, cfgValue string) {
	if v := strings.TrimSpace(b.env[envKey]); v != "" {
		b.row(label, v, SourceEnv)
		return
	}
	b.rowValue(label, cfgValue, SourceConfig)
}

// envOrCfgBoolRow .env 우선, 없으면 config.json bool 값을 표시합니다.
func (b *summaryBuilder) envOrCfgBoolRow(label, envKey string, cfgValue bool) {
	if v := strings.TrimSpace(b.env[envKey]); v != "" {
		b.row(label, v, SourceEnv)
		return
	}
	b.row(label, strconv.FormatBool(cfgValue), SourceConfig)
}

// secretRow 시크릿은 설정 여부와 길이만 노출합니다.
func (b *summaryBuilder) secretRow(label, value string, source ConfigSummarySource) {
	b.row(label, MaskSecretValue(value), source)
}

// MaskSecretValue 시크릿 값을 원문 없이 마스킹합니다.
func MaskSecretValue(value string) string {
	trimmed := strings.TrimSpace(value)
	if trimmed == "" {
		return "(미설정)"
	}
	count := len([]rune(trimmed))
	if count <= 4 {
		return maskPlaceholder + " (설정됨)"
	}
	return fmt.Sprintf("%s (설정됨, %d자)", maskPlaceholder, count)
}

// isSecretArgumentName launch 인자/환경변수 이름이 시크릿을 담는지 판정합니다.
func isSecretArgumentName(name string) bool {
	lower := strings.ToLower(strings.TrimSpace(name))
	if lower == "" {
		return false
	}
	for _, token := range []string{"token", "api_key", "apikey", "password", "secret"} {
		if strings.Contains(lower, token) {
			return true
		}
	}
	return strings.HasSuffix(lower, "_key")
}

// isJSONContainerArgument 하위 항목에 인증 정보을 담을 수 있는 JSON 인자인지 판정합니다.
// 전체를 가리면 디버깅에 필요한 구조 정보까지 사라지므로 내부 secret 키만 마스킹합니다.
func isJSONContainerArgument(name string) bool {
	switch strings.ToLower(strings.TrimSpace(name)) {
	case "target_peers_json", "mcp_servers_json":
		return true
	}
	return false
}

// MaskCommandSecrets launch 인자 목록에서 시크릿 값을 마스킹한 문자열을 만듭니다.
func MaskCommandSecrets(args []string) string {
	masked := make([]string, len(args))
	for i, arg := range args {
		idx := strings.Index(arg, ":=")
		if idx <= 0 {
			masked[i] = arg
			continue
		}
		name, value := arg[:idx], arg[idx+2:]
		switch {
		case isSecretArgumentName(name):
			masked[i] = name + ":=" + maskPlaceholder
		case isJSONContainerArgument(name):
			redacted, ok := maskJSONSecrets(value)
			if !ok {
				masked[i] = name + ":=" + maskPlaceholder
				continue
			}
			masked[i] = name + ":=" + redacted
		default:
			masked[i] = arg
		}
	}
	return strings.Join(masked, " ")
}

// maskJSONSecrets JSON 문자열의 secret 키 값만 마스킹합니다.
func maskJSONSecrets(raw string) (string, bool) {
	var parsed interface{}
	if err := json.Unmarshal([]byte(raw), &parsed); err != nil {
		return "", false
	}
	redacted, err := json.Marshal(redactJSONSecrets(parsed))
	if err != nil {
		return "", false
	}
	return string(redacted), true
}

func redactJSONSecrets(value interface{}) interface{} {
	switch typed := value.(type) {
	case map[string]interface{}:
		out := make(map[string]interface{}, len(typed))
		for key, item := range typed {
			if isSecretArgumentName(key) {
				out[key] = maskPlaceholder
				continue
			}
			out[key] = redactJSONSecrets(item)
		}
		return out
	case []interface{}:
		out := make([]interface{}, len(typed))
		for i, item := range typed {
			out[i] = redactJSONSecrets(item)
		}
		return out
	default:
		return value
	}
}

// BuildConfigSummary 구동에 적용되는 설정을 섹션별 요약으로 만듭니다.
func BuildConfigSummary(lctx *LaunchContext) []ConfigSummarySection {
	b := &summaryBuilder{env: lctx.EnvMap, cfg: lctx.ActiveConfig}
	b.appendProfileSection(lctx)
	b.appendLLMSection(lctx)
	b.appendRAGSection(lctx)
	b.appendChannelSection(lctx)
	b.appendSensorSection(lctx)
	b.appendAgentSection(lctx)
	b.appendFileSection(lctx)
	return b.sections
}

func (b *summaryBuilder) appendProfileSection(lctx *LaunchContext) {
	b.start("실행 프로파일")

	b.row("설정 출처", configSourceLabel(lctx.ConfigSource), SourceRuntime)
	b.rowValue("config revision", lctx.ConfigRevision, SourceRuntime)
	if !lctx.ConfigCachedAt.IsZero() {
		b.row("캐시 동기화 시각", lctx.ConfigCachedAt.Local().Format("2006-01-02 15:04:05"), SourceRuntime)
	}
	if cfg := lctx.ActiveConfig; cfg.Name != "" {
		b.row("설정 프로필", fmt.Sprintf("%s (id=%d, active=%t)", cfg.Name, cfg.ID, cfg.IsActive), SourceConfig)
	}
	b.row("로봇 / 환경", fmt.Sprintf("%s / %s", lctx.RobotName, lctx.EnvName), SourceRuntime)

	domainID := strings.TrimSpace(b.env["ROS_DOMAIN_ID"])
	if domainID == "" {
		domainID = strconv.Itoa(lctx.ActiveConfig.RosDomainId)
		b.row("ROS 도메인 ID", domainID, SourceConfig)
	} else {
		b.row("ROS 도메인 ID", domainID, SourceEnv)
	}

	if agentID := firstNonEmpty(b.env["RC_AGENT_ID"], lctx.ActiveConfig.AgentId); agentID != "" {
		src := SourceConfig
		if strings.TrimSpace(b.env["RC_AGENT_ID"]) != "" {
			src = SourceEnv
		}
		b.row("에이전트 ID", agentID, src)
	}

	mode := "로컬 (ros2 launch)"
	if lctx.Docker {
		mode = "Docker 컨테이너"
	}
	if lctx.Sim {
		mode += " · 시뮬레이션"
	}
	b.row("실행 모드", mode, SourceRuntime)
	b.row("launch 파일", lctx.LaunchTarget, SourceRuntime)
	b.row("설정 캐시 경로", lctx.CacheDir, SourceRuntime)
}

func (b *summaryBuilder) appendLLMSection(lctx *LaunchContext) {
	b.start("LLM")

	cfg := lctx.ActiveConfig
	b.rowNote("LLM 공급자", cfg.LlmProvider, SourceConfig, ignoredEnvNote("RC_LLM_PROVIDER", b.env, cfg.LlmProvider))
	b.rowNote("LLM 모델", cfg.LlmModel, SourceConfig, ignoredEnvNote("RC_LLM_MODEL", b.env, cfg.LlmModel))

	endpoint, endpointSource := cfg.OllamaBaseUrl, SourceConfig
	if v := strings.TrimSpace(b.env["RC_OLLAMA_BASE_URL"]); v != "" {
		endpoint, endpointSource = v, SourceEnv
	} else if strings.TrimSpace(endpoint) == "" {
		endpoint = cfg.AzureOpenaiEndpoint
	}
	b.rowValue("LLM 엔드포인트", endpoint, endpointSource)

	rawOptions := strings.TrimSpace(cfg.OllamaOptionsJson)
	if v := strings.TrimSpace(b.env["RC_OLLAMA_OPTIONS_JSON"]); v != "" {
		rawOptions = v
	}
	options, parseErr := parseLLMOptions(rawOptions)
	if parseErr != nil {
		b.row("Ollama 옵션", fmt.Sprintf("(JSON 파싱 실패: %v)", parseErr), SourceRuntime)
	}
	optionSource := SourceConfig
	if strings.TrimSpace(b.env["RC_OLLAMA_OPTIONS_JSON"]) != "" {
		optionSource = SourceEnv
	}
	for _, key := range orderLLMOptionKeys(options) {
		b.row("Ollama "+key, options[key], optionSource)
	}

	b.secretRow("Azure OpenAI API 키", b.env["AZURE_OPENAI_API_KEY"], SourceEnv)
	b.secretRow("OpenAI API 키", b.env["OPENAI_API_KEY"], SourceEnv)
	b.secretRow("Anthropic API 키", b.env["ANTHROPIC_API_KEY"], SourceEnv)

	if lctx.ActiveConfig.LangsmithTracing {
		b.row("LangSmith 트레이싱", "true", SourceConfig)
		b.rowValue("LangSmith 프로젝트", cfg.LangsmithProject, SourceConfig)
		b.rowValue("LangSmith 엔드포인트", cfg.LangsmithEndpoint, SourceConfig)
		b.secretRow("LangSmith API 키", cfg.LangsmithApiKey, SourceConfig)
	}
}

func (b *summaryBuilder) appendRAGSection(lctx *LaunchContext) {
	b.start("임베딩 · RAG")
	cfg := lctx.ActiveConfig

	b.envOrCfgBoolRow("RAG 사용", "RC_ENABLE_RAG", cfg.EnableRag)
	b.envOrCfgRow("임베딩 공급자", "RC_LLM_EMBEDDING_PROVIDER", cfg.LlmEmbeddingProvider)
	b.envOrCfgRow("임베딩 모델", "RC_LLM_EMBEDDING_MODEL", cfg.LlmEmbeddingModel)
	b.envOrCfgRow("임베딩 엔드포인트", "RC_LLM_EMBEDDING_BASE_URL", cfg.LlmEmbeddingBaseUrl)
	b.secretRow("임베딩 API 키", firstNonEmpty(b.env["RC_LLM_EMBEDDING_API_KEY"], cfg.LlmEmbeddingApiKey), embeddingKeySource(b.env, cfg))
	b.envOrCfgRow("벡터 백엔드", "RC_RAG_VECTOR_BACKEND", cfg.RagVectorBackend)
	b.envOrCfgRow("Qdrant URL", "QDRANT_URL", cfg.QdrantUrl)
	b.envOrCfgRow("Qdrant 컬렉션", "RC_QDRANT_COLLECTION", cfg.QdrantCollection)
	b.secretRow("Qdrant API 키", firstNonEmpty(b.env["QDRANT_API_KEY"], cfg.QdrantApiKey), qdrantKeySource(b.env, cfg))
	b.envOrCfgRow("Qdrant 타임아웃(초)", "RC_QDRANT_TIMEOUT_SEC", formatFloat(cfg.QdrantTimeoutSec))
	b.envOrCfgRow("RAG Top-K", "RC_RAG_TOP_K", strconv.Itoa(cfg.RagTopK))
	b.envOrCfgRow("RAG 유사도 임계값", "RC_RAG_SCORE_THRESHOLD", formatFloat(cfg.RagScoreThreshold))
	b.envOrCfgBoolRow("RAG 로컬 미러", "RC_RAG_LOCAL_MIRROR", cfg.RagLocalMirror)
}

func embeddingKeySource(env map[string]string, cfg RoboClawConfig) ConfigSummarySource {
	if strings.TrimSpace(env["RC_LLM_EMBEDDING_API_KEY"]) != "" {
		return SourceEnv
	}
	return SourceConfig
}

func qdrantKeySource(env map[string]string, cfg RoboClawConfig) ConfigSummarySource {
	if strings.TrimSpace(env["QDRANT_API_KEY"]) != "" {
		return SourceEnv
	}
	return SourceConfig
}

func (b *summaryBuilder) appendChannelSection(lctx *LaunchContext) {
	b.start("채널 · HTTP · gRPC")
	cfg := lctx.ActiveConfig

	b.row("Discord", strconv.FormatBool(cfg.EnableDiscord), SourceConfig)
	b.secretRow("Discord 봇 토큰", b.env["DISCORD_BOT_TOKEN"], SourceEnv)
	b.row("Telegram", strconv.FormatBool(cfg.EnableTelegram), SourceConfig)
	b.secretRow("Telegram 봇 토큰", b.env["TELEGRAM_BOT_TOKEN"], SourceEnv)
	b.row("Slack", strconv.FormatBool(cfg.EnableSlack), SourceConfig)
	b.secretRow("Slack App 토큰", b.env["SLACK_APP_TOKEN"], SourceEnv)
	b.secretRow("Slack Bot 토큰", b.env["SLACK_BOT_TOKEN"], SourceEnv)

	useGRPC := cfg.UseGrpc || lctx.ForceGrpc
	b.row("gRPC 서버", strconv.FormatBool(cfg.EnableGrpc), SourceConfig)
	b.row("gRPC 클라이언트", strconv.FormatBool(cfg.EnableGrpcClient), SourceConfig)
	b.row("gRPC 사용", strconv.FormatBool(useGRPC), SourceRuntime)
	b.rowValue("gRPC 대상 호스트", cfg.GrpcTargetHost, SourceConfig)
	if cfg.GrpcTargetPort > 0 {
		b.row("gRPC 대상 포트", strconv.Itoa(cfg.GrpcTargetPort), SourceConfig)
	}
	if peers := countJSONArray(cfg.GrpcTargetPeersJson); peers >= 0 {
		b.row("gRPC 피어 수", strconv.Itoa(peers), SourceConfig)
	}
	b.secretRow("gRPC 피어 토큰", b.env["GRPC_PEER_TOKEN"], SourceEnv)

	b.rowValue("HTTP 호스트", b.env["RC_HTTP_HOST"], SourceEnv)
	b.row("HTTP 포트", lctx.ActualPort, SourceRuntime)
	b.envRow("HTTP 속도 제한(분당)", "RC_HTTP_RATE_LIMIT_PER_MINUTE")
	if cidrs := countJSONArray(b.env["RC_HTTP_ALLOWED_CIDRS_JSON"]); cidrs >= 0 {
		b.row("HTTP 허용 CIDR 수", strconv.Itoa(cidrs), SourceEnv)
	}
	if allowed := countJSONArray(b.env["RC_HTTP_ALLOWED_SKILLS_JSON"]); allowed >= 0 {
		b.row("HTTP 허용 스킬 수", strconv.Itoa(allowed), SourceEnv)
	}
	if blocked := countJSONArray(b.env["RC_HTTP_BLOCKED_SKILLS_JSON"]); blocked >= 0 {
		b.row("HTTP 차단 스킬 수", strconv.Itoa(blocked), SourceEnv)
	}
	b.secretRow("HTTP 읽기 전용 토큰", b.env["RC_HTTP_READONLY_TOKEN"], SourceEnv)
	b.secretRow("HTTP 제어 토큰", b.env["RC_HTTP_CONTROL_TOKEN"], SourceEnv)

	if field, ok := contract.RuntimeFields["messenger.server.port"]; ok && field.Env != "" {
		b.envRow("메신저 서버 포트", field.Env)
	}

	b.envOrCfgBoolRow("MCP 사용", "RC_ENABLE_MCP", cfg.EnableMcp)
	if servers := countJSONArray(firstNonEmpty(b.env["RC_MCP_SERVERS_JSON"], cfg.McpServersJson)); servers >= 0 {
		b.row("MCP 서버 수", strconv.Itoa(servers), SourceRuntime)
	}
}

func (b *summaryBuilder) appendSensorSection(lctx *LaunchContext) {
	b.start("센서 · 비전")
	cfg := lctx.ActiveConfig

	b.envOrCfgRow("카메라 토픽", "RC_CAMERA_TOPIC", cfg.CameraTopic)
	b.envOrCfgBoolRow("비전 사용", "RC_USE_VISION", cfg.UseVision)
	modelPath := resolveVisionModelPath(lctx, firstNonEmpty(b.env["RC_VISION_MODEL_PATH"], cfg.VisionModelPath))
	b.rowValue("비전 모델 경로", modelPath, SourceRuntime)
	b.envOrCfgRow("그리퍼 카메라 토픽", "RC_GRIPPER_CAMERA_TOPIC", cfg.GripperCameraTopic)
	b.envOrCfgRow("그리퍼 깊이 토픽", "RC_GRIPPER_DEPTH_TOPIC", cfg.GripperDepthTopic)
	b.envOrCfgRow("그리퍼 camera_info 토픽", "RC_GRIPPER_CAMERA_INFO_TOPIC", cfg.GripperCameraInfoTopic)
	b.envOrCfgRow("그리퍼 포인트클라우드 토픽", "RC_GRIPPER_POINTCLOUD_TOPIC", cfg.GripperPointcloudTopic)
	b.envOrCfgBoolRow("그리퍼 비전 사용", "RC_USE_GRIPPER_VISION", cfg.UseGripperVision)
	b.envOrCfgRow("그리퍼 비전 최대 추론 Hz", "RC_GRIPPER_VISION_MAX_INFERENCE_HZ", formatFloat(cfg.GripperVisionMaxInferenceHz))
	b.envOrCfgRow("LiDAR 토픽", "RC_LIDAR_TOPIC", cfg.LidarTopic)
	b.envOrCfgRow("IMU 토픽", "RC_IMU_TOPIC", cfg.ImuTopic)
	b.envOrCfgRow("로봇 description 파일", "RC_ROBOT_DESCRIPTION_FILE", cfg.RobotDescriptionFile)
}

func (b *summaryBuilder) appendAgentSection(lctx *LaunchContext) {
	b.start("에이전트 · 태스크")
	cfg := lctx.ActiveConfig

	b.envOrCfgRow("태스크 큐 최대 크기", "RC_TASK_QUEUE_MAX_SIZE", strconv.Itoa(cfg.TaskQueueMaxSize))
	b.envOrCfgBoolRow("LLM fail fast", "RC_LLM_FAIL_FAST", cfg.LlmFailFast)
	b.envOrCfgBoolRow("strict config", "RC_STRICT_CONFIG", cfg.StrictConfig)
	b.envOrCfgBoolRow("태스크 분해 사용", "RC_ENABLE_TASK_DECOMPOSITION", cfg.EnableTaskDecomposition)
	b.envOrCfgRow("분해 최대 스텝", "RC_TASK_DECOMPOSITION_MAX_STEPS", strconv.Itoa(cfg.TaskDecompositionMaxSteps))
	b.envOrCfgRow("스텝 최대 재시도", "RC_TASK_STEP_MAX_RETRIES", strconv.Itoa(cfg.TaskStepMaxRetries))
	b.envOrCfgRow("분해 대기 마진 상한(초)", "RC_TASK_DECOMPOSITION_WAIT_MARGIN_CAP_SEC", formatFloat(cfg.TaskDecompositionWaitMarginCapSec))
	b.envOrCfgBoolRow("스킬 자가학습", "RC_ENABLE_SKILL_LEARNING", cfg.EnableSkillLearning)
	b.envOrCfgRow("자가학습 성공 샘플 비율", "RC_SKILL_LEARNING_SUCCESS_SAMPLE_RATE", formatFloat(cfg.SkillLearningSuccessSampleRate))
	b.envOrCfgRow("자가학습 회고 주기(초)", "RC_SKILL_LEARNING_REFLECT_INTERVAL_SEC", strconv.Itoa(cfg.SkillLearningReflectIntervalSec))

	for _, canonical := range []string{
		"agent.cloid_cleanup_demo_enabled",
		"agent.cloid_cleanup_demo_motion_ids_json",
		"agent.cloid_expression_enabled",
		"agent.cloid_navigation_pose_enabled",
	} {
		field, ok := contract.RuntimeFields[canonical]
		if !ok || field.Env == "" {
			continue
		}
		b.envRow("CLOiD "+strings.TrimPrefix(canonical, "agent.cloid_"), field.Env)
	}

	b.rowValue("메모리 디렉토리", firstNonEmpty(b.env["RC_MEMORY_DIR"], cfg.MemoryDir), memoryDirSource(b.env, cfg))
	b.rowValue("에이전트 작업 공간", firstNonEmpty(b.env["RC_AGENT_WORKSPACE_DIR"], cfg.AgentWorkspaceDir), workspaceDirSource(b.env, cfg))
	b.rowValue("Butler 스크립트 경로", ResolveButlerScriptsDir(b.env, lctx.ProjectRoot), SourceRuntime)
	b.rowValue("Butler 소스 경로", ResolveButlerSourceDir(b.env), SourceRuntime)
}

func memoryDirSource(env map[string]string, cfg RoboClawConfig) ConfigSummarySource {
	if strings.TrimSpace(env["RC_MEMORY_DIR"]) != "" {
		return SourceEnv
	}
	return SourceConfig
}

func workspaceDirSource(env map[string]string, cfg RoboClawConfig) ConfigSummarySource {
	if strings.TrimSpace(env["RC_AGENT_WORKSPACE_DIR"]) != "" {
		return SourceEnv
	}
	return SourceConfig
}

func (b *summaryBuilder) appendFileSection(lctx *LaunchContext) {
	b.start("설정 파일")

	for _, name := range []string{"ROBOT.md", "SKILLS.md", "TROUBLESHOOTING.md", "ROBOT_LIMITS.json"} {
		path := filepath.Join(lctx.CacheDir, name)
		info, err := os.Stat(path)
		if err != nil {
			b.row(name, "없음", SourceRuntime)
			continue
		}
		b.row(name, fmt.Sprintf("마운트됨 (%s)", humanBytes(info.Size())), SourceRuntime)
	}
	if promptFile := strings.TrimSpace(b.env["RC_SYSTEM_PROMPT_FILE"]); promptFile != "" {
		b.rowValue("시스템 프롬프트 파일", promptFile, SourceEnv)
	}
	if scenario := filepath.Join(lctx.CacheDir, "scenario.json"); PathExists(scenario) {
		b.row("시나리오", "동기화됨", SourceRuntime)
	}
}

// configSourceLabel 설정 출처를 사람이 읽는 문구로 바꿉니다.
func configSourceLabel(source string) string {
	if source == ConfigSourceRemote {
		return "원격 설정 서버 동기화"
	}
	return "로컬 캐시"
}

// PrintConfigSummary 적용 설정 요약을 콘솔에 출력합니다.
// w가 터미널이면 섹션 제목을 강조하고, 아니면 마크업 없이 출력한다.
func PrintConfigSummary(w io.Writer, sections []ConfigSummarySection) {
	rich.Fprintln(w, "[cyan]■ RoboClaw 적용 설정 요약[/cyan]")
	rich.Fprintln(w, "  설정 서버에서 받아 실제 구동에 적용한 값입니다. 시크릿은 마스킹됩니다.")
	for _, section := range sections {
		rich.Fprintln(w, "[cyan]▌ %s[/cyan]", section.Title)
		for _, line := range formatSectionRows(section) {
			fmt.Fprintln(w, line)
		}
		fmt.Fprintln(w)
	}
}

// renderConfigSummaryPlain 요약을 마크업 없는 텍스트로 만듭니다(로그 파일용).
func renderConfigSummaryPlain(sections []ConfigSummarySection) string {
	var sb strings.Builder
	for _, section := range sections {
		sb.WriteString("▌ " + section.Title + "\n")
		for _, line := range formatSectionRows(section) {
			sb.WriteString(line + "\n")
		}
		sb.WriteString("\n")
	}
	return sb.String()
}

// formatSectionRows 섹션 항목을 정렬된 출력 라인으로 만듭니다.
func formatSectionRows(section ConfigSummarySection) []string {
	if len(section.Entries) == 0 {
		return nil
	}
	labelWidth := 0
	valueWidth := 0
	for _, entry := range section.Entries {
		if n := rich.DisplayWidth(entry.Label); n > labelWidth {
			labelWidth = n
		}
		value := displayValue(entry)
		if n := rich.DisplayWidth(value); n > valueWidth {
			valueWidth = n
		}
	}
	if valueWidth > 46 {
		valueWidth = 46
	}

	lines := make([]string, 0, len(section.Entries))
	for _, entry := range section.Entries {
		value := displayValue(entry)
		pad := labelWidth - rich.DisplayWidth(entry.Label)
		valuePad := valueWidth - rich.DisplayWidth(value)
		if valuePad < 0 {
			valuePad = 0
		}
		line := fmt.Sprintf("  %s%s : %s%s  [%s]", entry.Label, strings.Repeat(" ", pad), value, strings.Repeat(" ", valuePad), entry.Source)
		lines = append(lines, line)
	}
	return lines
}

func displayValue(entry ConfigSummaryEntry) string {
	value := entry.Value
	if strings.TrimSpace(value) == "" {
		value = "(미설정)"
	}
	if entry.Note != "" {
		value = value + " " + entry.Note
	}
	return value
}

// ConfigSummaryLogDir 적용 설정 로그를 보관하는 디렉터리입니다.
func ConfigSummaryLogDir(lctx *LaunchContext) string {
	return filepath.Join(lctx.CacheDir, "logs")
}

// WriteConfigSummaryLog 적용 설정 요약을 타임스탬프 파일로 저장하고 경로를 반환합니다.
func WriteConfigSummaryLog(lctx *LaunchContext, sections []ConfigSummarySection, now time.Time) (string, error) {
	dir := ConfigSummaryLogDir(lctx)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return "", fmt.Errorf("적용 설정 로그 디렉터리 생성 실패 (%s): %w", dir, err)
	}
	path := filepath.Join(dir, fmt.Sprintf("applied_config_%s.log", now.Format("20060102_150405")))

	var sb strings.Builder
	fmt.Fprintf(&sb, "# RoboClaw 적용 설정 요약\n")
	fmt.Fprintf(&sb, "# 기록 시각      : %s\n", now.Format("2006-01-02 15:04:05 MST"))
	fmt.Fprintf(&sb, "# 로봇 / 환경    : %s / %s\n", lctx.RobotName, lctx.EnvName)
	fmt.Fprintf(&sb, "# 설정 출처      : %s\n", configSourceLabel(lctx.ConfigSource))
	fmt.Fprintf(&sb, "# config revision: %s\n\n", firstNonEmpty(lctx.ConfigRevision, "(없음)"))
	sb.WriteString(renderConfigSummaryPlain(sections))
	sb.WriteString("# 시크릿 값은 마스킹되어 기록되지 않습니다.\n")

	if err := os.WriteFile(path, []byte(sb.String()), 0o600); err != nil {
		return "", fmt.Errorf("적용 설정 로그 저장 실패 (%s): %w", path, err)
	}
	pruneConfigSummaryLogs(dir)
	return path, nil
}

// pruneConfigSummaryLogs 최근 maxConfigSummaryLogs 개만 남기고 오래된 로그를 정리합니다.
func pruneConfigSummaryLogs(dir string) {
	matches, err := filepath.Glob(filepath.Join(dir, "applied_config_*.log"))
	if err != nil || len(matches) <= maxConfigSummaryLogs {
		return
	}
	sort.Sort(sort.Reverse(sort.StringSlice(matches)))
	for _, stale := range matches[maxConfigSummaryLogs:] {
		_ = os.Remove(stale)
	}
}

// parseLLMOptions Ollama 옵션 JSON을 키/값 문자열 맵으로 펼칩니다.
func parseLLMOptions(raw string) (map[string]string, error) {
	options := make(map[string]string)
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" || trimmed == "{}" {
		return options, nil
	}
	decoder := json.NewDecoder(strings.NewReader(trimmed))
	decoder.UseNumber()
	var parsed map[string]interface{}
	if err := decoder.Decode(&parsed); err != nil {
		return nil, err
	}
	for key, value := range parsed {
		if value == nil {
			options[key] = "(미설정)"
			continue
		}
		options[key] = fmt.Sprint(value)
	}
	return options, nil
}

// orderLLMOptionKeys 알려진 옵션을 우선순위대로, 나머지는 이름순으로 정렬합니다.
func orderLLMOptionKeys(options map[string]string) []string {
	keys := make([]string, 0, len(options))
	seen := make(map[string]bool, len(options))
	for _, key := range llmOptionOrder {
		if _, ok := options[key]; ok {
			keys = append(keys, key)
			seen[key] = true
		}
	}
	rest := make([]string, 0, len(options))
	for key := range options {
		if !seen[key] {
			rest = append(rest, key)
		}
	}
	sort.Strings(rest)
	return append(keys, rest...)
}

// countJSONArray JSON 배열 문자열의 원소 개수를 셉니다. 파싱할 수 없으면 -1.
func countJSONArray(raw string) int {
	trimmed := strings.TrimSpace(raw)
	if trimmed == "" {
		return -1
	}
	var items []json.RawMessage
	if err := json.Unmarshal([]byte(trimmed), &items); err != nil {
		return -1
	}
	return len(items)
}

// ignoredEnvNote launch 경로가 사용하지 않는 .env 값이 다를 때 안내 문구를 만듭니다.
func ignoredEnvNote(envKey string, env map[string]string, applied string) string {
	envValue := strings.TrimSpace(env[envKey])
	if envValue == "" || envValue == strings.TrimSpace(applied) {
		return ""
	}
	return fmt.Sprintf("(.env %s=%s 값은 launch에 적용되지 않음)", envKey, envValue)
}

// formatFloat 불필요한 소수 자릿수를 제거해 표시합니다.
func formatFloat(value float64) string {
	return strconv.FormatFloat(value, 'f', -1, 64)
}

// humanBytes 파일 크기를 사람이 읽기 쉬운 단위로 바꿉니다.
func humanBytes(size int64) string {
	const unit = 1024
	if size < unit {
		return fmt.Sprintf("%d B", size)
	}
	value := float64(size)
	for _, suffix := range []string{"KiB", "MiB", "GiB"} {
		value /= unit
		if value < unit {
			return fmt.Sprintf("%.1f %s", value, suffix)
		}
	}
	return fmt.Sprintf("%.1f TiB", value/unit)
}
