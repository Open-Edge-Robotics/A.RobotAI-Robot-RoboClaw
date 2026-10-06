package launcher

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func baseCtx(env map[string]string) *LaunchContext {
	return &LaunchContext{
		ActualPort: "8080",
		ActiveConfig: RoboClawConfig{
			LlmProvider: "ollama",
			LlmModel:    "llama3.2",
		},
		EnvMap: env,
	}
}

// 원격/로컬 .env의 자가학습 변수가 ROS launch 인수로 전달되는지 검증한다.
func TestBuildCommonLaunchArgsSkillLearning(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"RC_ENABLE_RAG":                          "true",
		"RC_ENABLE_SKILL_LEARNING":               "true",
		"RC_SKILL_LEARNING_SUCCESS_SAMPLE_RATE":  "0.2",
		"RC_SKILL_LEARNING_REFLECT_INTERVAL_SEC": "1800",
	})

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	for _, want := range []string{
		"enable_skill_learning:=true",
		"skill_learning_success_sample_rate:=0.2",
		"skill_learning_reflect_interval_sec:=1800",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("기대한 launch 인수 누락: %s\n실제: %s", want, joined)
		}
	}
}

// 자가학습 변수가 없으면 관련 인수도 생성되지 않아야 한다.
func TestBuildCommonLaunchArgsSkillLearningOmitted(t *testing.T) {
	joined := strings.Join(BuildCommonLaunchArgs(baseCtx(map[string]string{})), " ")
	if strings.Contains(joined, "skill_learning") {
		t.Errorf("미설정 시 자가학습 인수가 없어야 함: %s", joined)
	}
}

// RC_RAG_LOCAL_MIRROR가 rag_local_mirror launch 인수로 전달되는지 검증한다.
func TestBuildCommonLaunchArgsRagLocalMirror(t *testing.T) {
	lctx := baseCtx(map[string]string{"RC_RAG_LOCAL_MIRROR": "false"})
	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	if !strings.Contains(joined, "rag_local_mirror:=false") {
		t.Errorf("기대한 launch 인수 누락: rag_local_mirror:=false\n실제: %s", joined)
	}

	joined = strings.Join(BuildCommonLaunchArgs(baseCtx(map[string]string{})), " ")
	if strings.Contains(joined, "rag_local_mirror") {
		t.Errorf("미설정 시 rag_local_mirror 인수가 없어야 함: %s", joined)
	}
}

// 임베딩 전용 설정이 ROS launch 인수로 전달되는지 검증한다.
func TestBuildCommonLaunchArgsEmbeddingProvider(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"RC_LLM_EMBEDDING_PROVIDER": "openai",
		"RC_LLM_EMBEDDING_MODEL":    "text-embedding-3-large",
		"RC_LLM_EMBEDDING_BASE_URL": "https://api.openai.com/v1",
		"RC_LLM_EMBEDDING_API_KEY":  "test-key",
	})

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	for _, want := range []string{
		"llm_embedding_provider:=openai",
		"llm_embedding_model:=text-embedding-3-large",
		"llm_embedding_base_url:=https://api.openai.com/v1",
		"llm_embedding_api_key:=test-key",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("기대한 launch 인수 누락: %s\n실제: %s", want, joined)
		}
	}
}

// MCP 서버 설정이 ROS launch 인수로 전달되는지 검증한다.
func TestBuildCommonLaunchArgsMcp(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"RC_ENABLE_MCP":       "true",
		"RC_MCP_SERVERS_JSON": `[{"name":"fs","transport":"stdio","command":"npx"}]`,
	})

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	for _, want := range []string{
		"mcp_enabled:=true",
		`mcp_servers_json:=[{"name":"fs","transport":"stdio","command":"npx"}]`,
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("기대한 launch 인수 누락: %s\n실제: %s", want, joined)
		}
	}
}

// JSON 계열 런치 인자는 launch 파일에서 ParameterValue(value_type=str)로 감싸므로
// CLI는 raw 문자열을 그대로 전달해야 한다. dict 항목을 포함하는 target_peers_json이
// 대표적인 사례이다.
func TestBuildCommonLaunchArgsMcpStreamableHTTP(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"RC_ENABLE_MCP":       "true",
		"RC_MCP_SERVERS_JSON": `[{"name":"remote","transport":"streamable_http","url":"https://example.com/mcp","headers":{"Authorization":"Bearer token"}}]`,
	})

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	want := `mcp_servers_json:=[{"name":"remote","transport":"streamable_http","url":"https://example.com/mcp","headers":{"Authorization":"Bearer token"}}]`
	if !strings.Contains(joined, want) {
		t.Errorf("streamable_http MCP 설정이 launch 인수로 보존되지 않음: %s", joined)
	}
}

func TestBuildCommonLaunchArgsJsonArgsArePassedRaw(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"RC_OLLAMA_OPTIONS_JSON":     `{"num_ctx":32768,"temperature":0}`,
		"RC_HTTP_ALLOWED_CIDRS_JSON": `["0.0.0.0/0"]`,
		"RC_MCP_SERVERS_JSON":        `[{"name":"fs","transport":"stdio"}]`,
	})
	lctx.ActiveConfig.GrpcTargetPeersJson = `[{"name":"Former","host":"10.0.0.1","port":50051}]`

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	for _, want := range []string{
		`target_peers_json:=[{"name":"Former","host":"10.0.0.1","port":50051}]`,
		`ollama_options_json:={"num_ctx":32768,"temperature":0}`,
		`http_allowed_cidrs_json:=["0.0.0.0/0"]`,
		`mcp_servers_json:=[{"name":"fs","transport":"stdio"}]`,
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("JSON 인수가 raw 문자열로 전달되지 않음: %s\n실제: %s", want, joined)
		}
	}
}

func TestBuildCommonLaunchArgsAgentIDFromConfig(t *testing.T) {
	lctx := baseCtx(map[string]string{})
	lctx.ActiveConfig.AgentId = "Former0047"

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	if !strings.Contains(joined, "agent_id:=Former0047") {
		t.Errorf("config의 agent_id가 launch 인자로 전달되지 않음: %s", joined)
	}
}

func TestBuildCommonLaunchArgsAgentIDEnvOverridesConfig(t *testing.T) {
	lctx := baseCtx(map[string]string{"RC_AGENT_ID": "Stretch3"})
	lctx.ActiveConfig.AgentId = "Former0047"

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	if !strings.Contains(joined, "agent_id:=Stretch3") {
		t.Errorf("RC_AGENT_ID가 config의 agent_id보다 우선하지 않음: %s", joined)
	}
	if strings.Contains(joined, "agent_id:=Former0047") {
		t.Errorf("오버라이드되지 않은 config agent_id가 남아 있음: %s", joined)
	}
}

// 값에 작은따옴표가 포함되어도 raw 그대로 전달되어야 한다(이스케이프/변형 금지).
func TestBuildCommonLaunchArgsJsonArgsPreserveSingleQuotes(t *testing.T) {
	lctx := baseCtx(map[string]string{})
	// description 필드에 아포스트로피가 포함된 피어 JSON
	lctx.ActiveConfig.GrpcTargetPeersJson = `[{"name":"Former","description":"robot's peer"}]`

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	want := `target_peers_json:=[{"name":"Former","description":"robot's peer"}]`
	if !strings.Contains(joined, want) {
		t.Errorf("JSON 값이 raw 그대로 전달되지 않음: %s\n실제: %s", want, joined)
	}
	if strings.Contains(joined, "robot''s peer") {
		t.Errorf("불필요한 작은따옴표 이스케이프가 발생함: %s", joined)
	}
}

// MCP 설정이 없으면 관련 인수도 생성되지 않아야 한다.
func TestBuildCommonLaunchArgsMcpOmittedWhenUnset(t *testing.T) {
	joined := strings.Join(BuildCommonLaunchArgs(baseCtx(map[string]string{})), " ")
	for _, notWant := range []string{"mcp_enabled:=", "mcp_servers_json:="} {
		if strings.Contains(joined, notWant) {
			t.Errorf("미설정 시 MCP 인수가 없어야 함: %s\n실제: %s", notWant, joined)
		}
	}
}

// 빈 gRPC 대상값 및 빈 토큰은 ROS2 launch에서 malformed argument가 되므로 전달하지 않는다.
func TestBuildCommonLaunchArgsOmitsEmptyGrpcTargets(t *testing.T) {
	joined := strings.Join(BuildCommonLaunchArgs(baseCtx(map[string]string{
		"GRPC_PEER_TOKEN":       "",
		"GRPC_PEER_TOKENS_JSON": "",
		"GRPC_MAX_FILE_BYTES":   "",
		"GRPC_DEDUP_PATH":       "",
	})), " ")
	for _, notWant := range []string{
		"target_host:=",
		"target_port:=0",
		"target_peers_json:=",
		"grpc_peer_token:=",
		"grpc_peer_tokens_json:=",
		"grpc_max_file_bytes:=",
		"grpc_dedup_path:=",
	} {
		if strings.Contains(joined, notWant) {
			t.Errorf("빈 gRPC launch 인수가 없어야 함: %s\n실제: %s", notWant, joined)
		}
	}
}

func TestBuildCommonLaunchArgsMessengerGrpcPort(t *testing.T) {
	lctx := baseCtx(map[string]string{"ROBO_CLAW_GRPC_PORT": "50152"})
	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	if !strings.Contains(joined, "grpc_port:=50152") {
		t.Fatalf("messenger gRPC port was not forwarded: %s", joined)
	}
}

func TestBuildCommonLaunchArgsGrpcPeerTokens(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"GRPC_PEER_TOKEN":       "secret123",
		"GRPC_PEER_TOKENS_JSON": `{"Former":"secret456"}`,
		"GRPC_MAX_FILE_BYTES":   "1048576",
		"GRPC_DEDUP_PATH":       "/tmp/test_dedup.sqlite3",
	})
	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	for _, want := range []string{
		"grpc_peer_token:=secret123",
		`grpc_peer_tokens_json:={"Former":"secret456"}`,
		"grpc_max_file_bytes:=1048576",
		"grpc_dedup_path:=/tmp/test_dedup.sqlite3",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("기대한 launch 인수 누락: %s\n실제: %s", want, joined)
		}
	}
}

func TestBuildCommonLaunchArgsDockerVisionModelFallback(t *testing.T) {
	root := t.TempDir()
	modelsDir := filepath.Join(root, "models")
	if err := os.MkdirAll(modelsDir, 0755); err != nil {
		t.Fatalf("models 디렉토리 생성 실패: %v", err)
	}
	if err := os.WriteFile(filepath.Join(modelsDir, "yolov8n.onnx"), []byte("test"), 0644); err != nil {
		t.Fatalf("테스트 모델 파일 생성 실패: %v", err)
	}

	lctx := baseCtx(map[string]string{
		"RC_VISION_MODEL_PATH": filepath.Join(root, "models", "yolov8.onnx"),
	})
	lctx.Docker = true
	lctx.ProjectRoot = root

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	if !strings.Contains(joined, "vision_model_path:=/ros2_ws/models/yolov8n.onnx") {
		t.Fatalf("Docker 모드 모델 경로 fallback 실패: %s", joined)
	}
}

func TestBuildCommonLaunchArgsDockerVisionModelKeepsExistingModelName(t *testing.T) {
	root := t.TempDir()
	modelsDir := filepath.Join(root, "models")
	if err := os.MkdirAll(modelsDir, 0755); err != nil {
		t.Fatalf("models 디렉토리 생성 실패: %v", err)
	}
	if err := os.WriteFile(filepath.Join(modelsDir, "yolov8s.onnx"), []byte("test"), 0644); err != nil {
		t.Fatalf("테스트 모델 파일 생성 실패: %v", err)
	}

	lctx := baseCtx(map[string]string{
		"RC_VISION_MODEL_PATH": filepath.Join(root, "models", "yolov8s.onnx"),
	})
	lctx.Docker = true
	lctx.ProjectRoot = root

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	if !strings.Contains(joined, "vision_model_path:=/ros2_ws/models/yolov8s.onnx") {
		t.Fatalf("Docker 모드 기존 모델명 보존 실패: %s", joined)
	}
}

// 태스크 큐 / 복합 명령 자동 분해 설정이 ROS launch 인수로 전달되는지 검증한다.
func TestBuildCommonLaunchArgsTaskDecomposition(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"RC_TASK_QUEUE_MAX_SIZE":                    "4",
		"RC_LLM_FAIL_FAST":                          "true",
		"RC_STRICT_CONFIG":                          "true",
		"RC_ENABLE_TASK_DECOMPOSITION":              "false",
		"RC_TASK_DECOMPOSITION_MAX_STEPS":           "8",
		"RC_TASK_STEP_MAX_RETRIES":                  "2",
		"RC_TASK_DECOMPOSITION_WAIT_MARGIN_CAP_SEC": "900",
	})

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	for _, want := range []string{
		"task_queue_max_size:=4",
		"llm_fail_fast:=true",
		"strict_config:=true",
		"enable_task_decomposition:=false",
		"task_decomposition_max_steps:=8",
		"task_step_max_retries:=2",
		"task_decomposition_wait_margin_cap_sec:=900",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("기대한 launch 인수 누락: %s\n실제: %s", want, joined)
		}
	}
}

// 태스크 큐 / 복합 명령 분해 변수가 없으면 관련 인수도 생성되지 않아야 한다.
func TestBuildCommonLaunchArgsTaskDecompositionOmitted(t *testing.T) {
	joined := strings.Join(BuildCommonLaunchArgs(baseCtx(map[string]string{})), " ")
	for _, notWant := range []string{
		"task_queue_max_size:=",
		"llm_fail_fast:=",
		"strict_config:=",
		"enable_task_decomposition:=",
		"task_decomposition_max_steps:=",
		"task_step_max_retries:=",
		"task_decomposition_wait_margin_cap_sec:=",
	} {
		if strings.Contains(joined, notWant) {
			t.Errorf("미설정 시 인수가 없어야 함: %s\n실제: %s", notWant, joined)
		}
	}
}

func TestBuildCommonLaunchArgsGripperCameraTopics(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"RC_GRIPPER_CAMERA_TOPIC":            "/gripper_camera/color/image_rect_raw",
		"RC_GRIPPER_DEPTH_TOPIC":             "/gripper_camera/aligned_depth_to_color/image_raw",
		"RC_GRIPPER_CAMERA_INFO_TOPIC":       "/gripper_camera/aligned_depth_to_color/camera_info",
		"RC_GRIPPER_POINTCLOUD_TOPIC":        "/gripper_camera/depth/color/points",
		"RC_USE_GRIPPER_VISION":              "true",
		"RC_GRIPPER_VISION_MAX_INFERENCE_HZ": "5.0",
	})

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	for _, want := range []string{
		"gripper_camera_topic:=/gripper_camera/color/image_rect_raw",
		"gripper_depth_topic:=/gripper_camera/aligned_depth_to_color/image_raw",
		"gripper_camera_info_topic:=/gripper_camera/aligned_depth_to_color/camera_info",
		"gripper_pointcloud_topic:=/gripper_camera/depth/color/points",
		"use_gripper_vision:=true",
		"gripper_vision_max_inference_hz:=5.0",
	} {
		if !strings.Contains(joined, want) {
			t.Fatalf("그리퍼 카메라 launch 인수 누락: %s\n실제: %s", want, joined)
		}
	}
}
