package launcher

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func summaryTestContext(t *testing.T) *LaunchContext {
	t.Helper()
	cacheDir := t.TempDir()
	write := func(name, content string) {
		t.Helper()
		if err := os.WriteFile(filepath.Join(cacheDir, name), []byte(content), 0o600); err != nil {
			t.Fatalf("%s 생성 실패: %v", name, err)
		}
	}
	write("ROBOT.md", strings.Repeat("a", 2048))
	write("SKILLS.md", "skills")

	return &LaunchContext{
		LaunchFlags:    LaunchFlags{Docker: true},
		RobotName:      "former",
		EnvName:        "0047_w2_2f",
		CacheDir:       cacheDir,
		EnvFilePath:    filepath.Join(cacheDir, ".env"),
		ProjectRoot:    "/workspace",
		LaunchTarget:   "robo_claw.launch.py",
		ActualPort:     "8080",
		ConfigSource:   ConfigSourceRemote,
		ConfigRevision: "rev-42",
		ConfigCachedAt: time.Date(2026, 5, 1, 9, 30, 0, 0, time.UTC),
		ActiveConfig: RoboClawConfig{
			ID:                              7,
			Name:                            "Former_Factory_0047",
			RobotName:                       "former",
			Environment:                     "0047_w2_2f",
			IsActive:                        true,
			LlmProvider:                     "ollama",
			LlmModel:                        "gemma4:31b",
			OllamaBaseUrl:                   "http://10.0.0.1:11434",
			OllamaOptionsJson:               `{"num_ctx":32768,"temperature":0.2,"seed":null}`,
			EnableRag:                       true,
			LlmEmbeddingModel:               "qwen3-embedding:8b",
			QdrantUrl:                       "http://10.0.0.1:6333",
			QdrantCollection:                "robo_claw_former",
			RagTopK:                         5,
			RagScoreThreshold:               0.55,
			EnableTelegram:                  true,
			TelegramBotToken:                "telegram-secret-value",
			OpenaiApiKey:                    "sk-openai-secret",
			HttpControlToken:                "http-control-secret",
			GrpcTargetPeersJson:             `[{"host":"10.0.0.2","port":50051,"token":"peer-secret"}]`,
			HttpRateLimitPerMinute:          60,
			TaskQueueMaxSize:                8,
			StrictConfig:                    true,
			EnableTaskDecomposition:         true,
			TaskDecompositionMaxSteps:       4,
			TaskStepMaxRetries:              2,
			RagVectorBackend:                "qdrant",
			QdrantTimeoutSec:                5,
			LlmEmbeddingProvider:            "ollama",
			CameraTopic:                     "/camera/color/image_raw",
			UseVision:                       true,
			VisionModelPath:                 "/models/yolov8n.onnx",
			LidarTopic:                      "/scan",
			ImuTopic:                        "/imu",
			RosDomainId:                     5,
			AgentId:                         "agent-01",
			MemoryDir:                       "/workspace/memory",
			AgentWorkspaceDir:               "/workspace/agent_workspace",
			SkillLearningReflectIntervalSec: 1800,
		},
		EnvMap: map[string]string{
			"RC_ENABLE_RAG":   "false",
			"QDRANT_URL":      "http://10.0.0.9:6333",
			"GRPC_PEER_TOKEN": "peer-env-secret",
			"RC_LLM_MODEL":    "llama3.2",
		},
	}
}

func sectionEntries(sections []ConfigSummarySection, title string) (ConfigSummarySection, bool) {
	for _, section := range sections {
		if section.Title == title {
			return section, true
		}
	}
	return ConfigSummarySection{}, false
}

func findEntry(t *testing.T, sections []ConfigSummarySection, label string) ConfigSummaryEntry {
	t.Helper()
	for _, section := range sections {
		for _, entry := range section.Entries {
			if entry.Label == label {
				return entry
			}
		}
	}
	t.Fatalf("요약 항목을 찾지 못했습니다: %s", label)
	return ConfigSummaryEntry{}
}

func renderSummary(t *testing.T, lctx *LaunchContext) string {
	t.Helper()
	var buf bytes.Buffer
	PrintConfigSummary(&buf, BuildConfigSummary(lctx))
	return buf.String()
}

// 시크릿 값은 요약 출력 어디에도 원문으로 나타나지 않아야 한다.
func TestConfigSummaryMasksSecrets(t *testing.T) {
	lctx := summaryTestContext(t)
	out := renderSummary(t, lctx)

	for _, secret := range []string{
		"telegram-secret-value",
		"sk-openai-secret",
		"http-control-secret",
		"peer-secret",
		"peer-env-secret",
	} {
		if strings.Contains(out, secret) {
			t.Errorf("시크릿 원문이 요약에 노출되었습니다: %s", secret)
		}
	}
	if !strings.Contains(out, "********") {
		t.Errorf("마스킹 표시가 없습니다:\n%s", out)
	}
}

// Ollama 옵션 JSON은 키별 항목으로 펼쳐서 보여야 한다.
func TestConfigSummaryExpandsOllamaOptions(t *testing.T) {
	lctx := summaryTestContext(t)
	sections := BuildConfigSummary(lctx)

	ctxEntry := findEntry(t, sections, "Ollama num_ctx")
	if ctxEntry.Value != "32768" {
		t.Errorf("num_ctx 표시가 잘못되었습니다: %s", ctxEntry.Value)
	}
	tempEntry := findEntry(t, sections, "Ollama temperature")
	if tempEntry.Value != "0.2" {
		t.Errorf("temperature 표시가 잘못되었습니다: %s", tempEntry.Value)
	}
	seedEntry := findEntry(t, sections, "Ollama seed")
	if seedEntry.Value != "(미설정)" {
		t.Errorf("seed 미설정 표시가 잘못되었습니다: %s", seedEntry.Value)
	}
}

// .env override는 값과 출처가 함께 표시되어야 한다.
func TestConfigSummaryReportsEnvOverrideSource(t *testing.T) {
	lctx := summaryTestContext(t)
	sections := BuildConfigSummary(lctx)

	ragEntry := findEntry(t, sections, "RAG 사용")
	if ragEntry.Value != "false" || ragEntry.Source != SourceEnv {
		t.Errorf("RAG 사용 항목이 .env override를 반영하지 않았습니다: %+v", ragEntry)
	}
	qdrantEntry := findEntry(t, sections, "Qdrant URL")
	if qdrantEntry.Value != "http://10.0.0.9:6333" || qdrantEntry.Source != SourceEnv {
		t.Errorf("Qdrant URL 항목이 .env override를 반영하지 않았습니다: %+v", qdrantEntry)
	}
}

// launch 경로가 무시하는 .env 값(llm_model)은 사용자가 알 수 있게 표시한다.
func TestConfigSummaryNotesIgnoredEnvLlmModel(t *testing.T) {
	lctx := summaryTestContext(t)
	modelEntry := findEntry(t, BuildConfigSummary(lctx), "LLM 모델")
	if modelEntry.Value != "gemma4:31b" {
		t.Errorf("launch는 config.json 모델을 사용해야 합니다: %+v", modelEntry)
	}
	if !strings.Contains(modelEntry.Note, "RC_LLM_MODEL") {
		t.Errorf(".env 무시 안내가 없습니다: %+v", modelEntry)
	}
}

// 프로파일 정보(revision/출처/캐시 시각)가 요약에 포함되어야 한다.
func TestConfigSummaryIncludesProfileMetadata(t *testing.T) {
	lctx := summaryTestContext(t)
	out := renderSummary(t, lctx)

	for _, want := range []string{"rev-42", "원격 설정 서버", "2026-05-01", "0047_w2_2f"} {
		if !strings.Contains(out, want) {
			t.Errorf("프로파일 정보 누락: %s\n%s", want, out)
		}
	}
}

// 캐시된 설정 파일 존재 여부와 크기를 보여줘야 한다.
func TestConfigSummaryListsConfigFiles(t *testing.T) {
	lctx := summaryTestContext(t)
	sections := BuildConfigSummary(lctx)

	section, ok := sectionEntries(sections, "설정 파일")
	if !ok {
		t.Fatalf("설정 파일 섹션이 없습니다")
	}
	robot := findEntry(t, sections, "ROBOT.md")
	if !strings.Contains(robot.Value, "2") || strings.Contains(robot.Value, "없음") {
		t.Errorf("ROBOT.md 크기 표시가 잘못되었습니다: %+v", robot)
	}
	limits := findEntry(t, sections, "ROBOT_LIMITS.json")
	if limits.Value != "없음" {
		t.Errorf("없는 설정 파일은 '없음'으로 표시해야 합니다: %+v", limits)
	}
	if len(section.Entries) == 0 {
		t.Errorf("설정 파일 항목이 비어 있습니다")
	}
}

// 요약 로그는 cacheDir/logs 아래에 0600 권한으로 남아야 한다.
func TestWriteConfigSummaryLogWritesFile(t *testing.T) {
	lctx := summaryTestContext(t)
	now := time.Date(2026, 5, 1, 10, 11, 12, 0, time.Local)

	path, err := WriteConfigSummaryLog(lctx, BuildConfigSummary(lctx), now)
	if err != nil {
		t.Fatalf("요약 로그 저장 실패: %v", err)
	}
	if !strings.HasPrefix(path, filepath.Join(lctx.CacheDir, "logs")) {
		t.Errorf("요약 로그 위치가 잘못되었습니다: %s", path)
	}
	if !strings.Contains(filepath.Base(path), "20260501_101112") {
		t.Errorf("요약 로그 파일명에 타임스탬프가 없습니다: %s", path)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatalf("요약 로그 파일 확인 실패: %v", err)
	}
	if info.Mode().Perm() != 0o600 {
		t.Errorf("요약 로그 권한이 0600이 아닙니다: %o", info.Mode().Perm())
	}
	content, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("요약 로그 읽기 실패: %v", err)
	}
	for _, want := range []string{"former", "0047_w2_2f", "rev-42", "Ollama num_ctx"} {
		if !strings.Contains(string(content), want) {
			t.Errorf("요약 로그 본문 누락: %s", want)
		}
	}
	if strings.Contains(string(content), "telegram-secret-value") {
		t.Errorf("요약 로그에 시크릿 원문이 기록되었습니다")
	}
}

// 오래된 요약 로그는 보관 개수를 넘으면 정리된다.
func TestWriteConfigSummaryLogPrunesOldFiles(t *testing.T) {
	lctx := summaryTestContext(t)
	dir := ConfigSummaryLogDir(lctx)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatalf("로그 디렉터리 생성 실패: %v", err)
	}
	for i := 0; i < maxConfigSummaryLogs+5; i++ {
		name := fmt.Sprintf("applied_config_20260101_0000%02d.log", i)
		if err := os.WriteFile(filepath.Join(dir, name), []byte("old"), 0o600); err != nil {
			t.Fatalf("이전 로그 생성 실패: %v", err)
		}
	}

	if _, err := WriteConfigSummaryLog(lctx, BuildConfigSummary(lctx), time.Now()); err != nil {
		t.Fatalf("요약 로그 저장 실패: %v", err)
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatalf("로그 디렉터리 읽기 실패: %v", err)
	}
	count := 0
	for _, entry := range entries {
		if !entry.IsDir() && strings.HasPrefix(entry.Name(), "applied_config_") {
			count++
		}
	}
	if count > maxConfigSummaryLogs {
		t.Errorf("보관 개수 초과: got=%d max=%d", count, maxConfigSummaryLogs)
	}
}

// 실행 명령 로그에서도 secret 인자는 마스킹되어야 한다.
func TestMaskCommandSecrets(t *testing.T) {
	args := []string{
		"ros2", "launch", "robo_claw_bringup", "robo_claw.launch.py",
		"llm_model:=gemma4:31b",
		"telegram_token:=12345:abcdef",
		"openai_api_key:=sk-xyz",
		"grpc_peer_tokens_json:={\"p\":\"secret\"}",
		"mcp_servers_json:=[]",
		"target_peers_json:=[{\"host\":\"10.0.0.2\",\"port\":50051,\"token\":\"peer-json-secret\"}]",
		"http_port:=8080",
	}
	got := MaskCommandSecrets(args)

	for _, secret := range []string{"12345:abcdef", "sk-xyz", "secret", "peer-json-secret"} {
		if strings.Contains(got, secret) {
			t.Errorf("명령 로그에 시크릿이 노출되었습니다: %s", got)
		}
	}
	for _, want := range []string{
		"llm_model:=gemma4:31b",
		"http_port:=8080",
		"telegram_token:=********",
		// JSON 컨테이너 인자는 구조 정보를 남기고 secret 키만 가린다.
		`target_peers_json:=[{"host":"10.0.0.2","port":50051,"token":"********"}]`,
	} {
		if !strings.Contains(got, want) {
			t.Errorf("명령 로그 형식이 잘못되었습니다: %s (want=%s)", got, want)
		}
	}
}
