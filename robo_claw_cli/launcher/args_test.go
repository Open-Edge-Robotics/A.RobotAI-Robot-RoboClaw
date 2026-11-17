package launcher

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"robo_claw_cli/contract"
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

// 스킬 선택 방식(네이티브 도구 호출)이 ROS launch 인수로 전달되는지 검증한다.
func TestBuildCommonLaunchArgsLlmToolCalling(t *testing.T) {
	joined := strings.Join(
		BuildCommonLaunchArgs(baseCtx(map[string]string{"RC_LLM_TOOL_CALLING": "false"})),
		" ",
	)
	if !strings.Contains(joined, "llm_tool_calling:=false") {
		t.Errorf("기대한 launch 인수 누락: llm_tool_calling:=false\n실제: %s", joined)
	}
}

// 미설정이면 launch 인수를 만들지 않아 노드 기본값(true)이 유지된다.
func TestBuildCommonLaunchArgsLlmToolCallingOmitted(t *testing.T) {
	joined := strings.Join(BuildCommonLaunchArgs(baseCtx(map[string]string{})), " ")
	if strings.Contains(joined, "llm_tool_calling:=") {
		t.Errorf("미설정 시 인수가 없어야 함: llm_tool_calling:=\n실제: %s", joined)
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

// rclaw launch 경로도 rclaw run 경로와 동일하게 CLOiD 개체 설정을 ROS launch 인수로
// 전달해야 한다. 누락되면 launch 파일 기본값(비활성/빈 allowlist)이 적용되어 원격
// 설정이 조용히 무시된다.
func TestBuildCommonLaunchArgsCloidProfileSettings(t *testing.T) {
	lctx := baseCtx(map[string]string{
		"RC_CLOID_EXPRESSION_ENABLED":           "true",
		"RC_CLOID_NAVIGATION_POSE_ENABLED":      "true",
		"RC_CLOID_CLEANUP_DEMO_ENABLED":         "true",
		"RC_CLOID_CLEANUP_DEMO_MOTION_IDS_JSON": "[22]",
	})

	joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
	for _, want := range []string{
		"cloid_expression_enabled:=true",
		"cloid_navigation_pose_enabled:=true",
		"cloid_cleanup_demo_enabled:=true",
		"cloid_cleanup_demo_motion_ids_json:=[22]",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("기대한 CLOiD launch 인수 누락: %s\n실제: %s", want, joined)
		}
	}
}

// CLOiD 설정이 없으면 관련 인수도 생성되지 않아야 한다(launch 기본값 유지).
func TestBuildCommonLaunchArgsCloidSettingsOmitted(t *testing.T) {
	joined := strings.Join(BuildCommonLaunchArgs(baseCtx(map[string]string{})), " ")
	for _, notWant := range []string{
		"cloid_expression_enabled:=",
		"cloid_navigation_pose_enabled:=",
		"cloid_cleanup_demo_enabled:=",
		"cloid_cleanup_demo_motion_ids_json:=",
	} {
		if strings.Contains(joined, notWant) {
			t.Errorf("미설정 시 CLOiD 인수가 없어야 함: %s\n실제: %s", notWant, joined)
		}
	}
}

// 계약에 launch_argument가 정의된 dashboard.* 필드는 launch 경로에서도 전달되어야 한다.
// AI Config Server 프로필의 dashboard.enabled(RC_USE_DASHBOARD)가 누락되면 대시보드 노드가
// 기동하지 않으므로 포트만 설정한 프로필이 조용히 무시된다.
func TestBuildCommonLaunchArgsForwardsDashboardContractFields(t *testing.T) {
	cases := map[string]string{
		"dashboard.enabled": "true",
		"dashboard.host":    "0.0.0.0",
		"dashboard.port":    "9190",
	}
	for canonical, value := range cases {
		field, ok := contract.RuntimeFields[canonical]
		if !ok {
			t.Fatalf("계약에 %s 필드가 없음", canonical)
		}
		if field.LaunchArgument == "" {
			t.Fatalf("%s launch 인자 미정의", canonical)
		}
		lctx := baseCtx(map[string]string{field.Env: value})
		joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
		want := fmt.Sprintf("%s:=%s", field.LaunchArgument, value)
		if !strings.Contains(joined, want) {
			t.Errorf("%s(%s)가 launch 인수로 전달되지 않음: %s", canonical, field.Env, joined)
		}
	}

	// 미설정 시 launch 파일 기본값(false / 127.0.0.1 / 9090)을 덮어쓰지 않도록 인수를 만들지 않는다.
	joined := strings.Join(BuildCommonLaunchArgs(baseCtx(map[string]string{})), " ")
	for _, notWant := range []string{"use_dashboard:=", "dashboard_host:=", "dashboard_port:="} {
		if strings.Contains(joined, notWant) {
			t.Errorf("미설정 시 대시보드 인수가 없어야 함: %s\n실제: %s", notWant, joined)
		}
	}
}

// 계약에 launch_argument가 정의된 CLOiD 개체 필드는 모두 전달되어야 한다.
// 새 CLOiD 필드가 계약에 추가될 때 launch 경로 누락을 이 테스트가 잡는다.
func TestBuildCommonLaunchArgsForwardsEveryCloidContractField(t *testing.T) {
	for canonical, field := range contract.RuntimeFields {
		if !strings.HasPrefix(canonical, "agent.cloid") || field.LaunchArgument == "" {
			continue
		}
		lctx := baseCtx(map[string]string{field.Env: "test-value"})
		joined := strings.Join(BuildCommonLaunchArgs(lctx), " ")
		want := fmt.Sprintf("%s:=test-value", field.LaunchArgument)
		if !strings.Contains(joined, want) {
			t.Errorf(
				"CLOiD 계약 필드 %s(%s)가 launch 인수로 전달되지 않음: %s",
				canonical, field.Env, joined,
			)
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

// 시뮬레이션 엔진은 Jazzy(Gazebo Harmonic) 또는 Gazebo 불필요한 stub 만 지원한다.
// Humble(Gazebo Classic) 지원은 제거되어 인자 생성 단계에서 distro 분기가 없다.
func TestBuildSimArgsSimEngineIgnoresDistro(t *testing.T) {
	for _, distro := range []string{"jazzy", "humble", ""} {
		lctx := baseCtx(map[string]string{})
		lctx.RosDistro = distro
		joined := strings.Join(BuildSimArgs(lctx), " ")
		if !strings.Contains(joined, "sim_engine:=gz") {
			t.Fatalf("distro=%q 에서도 gz 여야 합니다. 실제: %s", distro, joined)
		}
	}
}

// RosDistro 가 비어 있으면 환경변수/설치 경로로 배포판을 감지해 엔진을 정한다.
func TestBuildSimArgsFallsBackToDetectedDistro(t *testing.T) {
	t.Setenv("ROS_DISTRO", "jazzy")
	lctx := baseCtx(map[string]string{})
	lctx.RosDistro = ""

	joined := strings.Join(BuildSimArgs(lctx), " ")
	if !strings.Contains(joined, "sim_engine:=gz") {
		t.Fatalf("ROS_DISTRO=jazzy 인데 gz 엔진이 선택되지 않았습니다. 실제: %s", joined)
	}
}

func TestDetectRosDistroPrefersEnvironment(t *testing.T) {
	t.Setenv("ROS_DISTRO", "humble")
	if got := DetectRosDistro(); got != "humble" {
		t.Fatalf("환경변수 우선 실패: %s", got)
	}
}

// 시뮬레이션은 Jazzy 단일 지원이다. humble 배포판에서는 조용히 gz 를 띄우지 않고
// 시작 전에 명확한 오류로 중단해야 한다(ros_gz_sim 부재로 늦게 실패하는 것을 막는다).
func TestValidateSimDistro(t *testing.T) {
	if err := ValidateSimDistro("jazzy"); err != nil {
		t.Fatalf("jazzy 는 지원해야 합니다: %v", err)
	}

	err := ValidateSimDistro("humble")
	if err == nil {
		t.Fatal("humble 은 지원하지 않아야 합니다")
	}
	if !strings.Contains(err.Error(), "jazzy") {
		t.Fatalf("오류 메시지에 지원 배포판이 드러나야 합니다: %v", err)
	}
}

// --engine 으로 전용 시뮬레이터(stub)를 선택하면 Gazebo 인자 없이 실행되어야 한다.
func TestBuildSimArgsExplicitStubEngine(t *testing.T) {
	lctx := baseCtx(map[string]string{})
	lctx.RosDistro = "jazzy"
	lctx.SimEngine = SimEngineStub
	lctx.ScenarioFile = "src/robo_claw_sim/scenarios/navigate_basic.yaml"

	joined := strings.Join(BuildSimArgs(lctx), " ")

	if !strings.Contains(joined, "sim_engine:=stub") {
		t.Fatalf("stub 엔진이 선택되지 않았습니다: %s", joined)
	}
	if !strings.Contains(joined, "scenario_file:=src/robo_claw_sim/scenarios/navigate_basic.yaml") {
		t.Fatalf("시나리오 인자가 전달되지 않았습니다: %s", joined)
	}
}

// --engine 을 주지 않으면 배포판 기본 엔진(Gazebo)을 유지한다(기존 동작 보존).
func TestBuildSimArgsAutoEngineKeepsDistroDefault(t *testing.T) {
	lctx := baseCtx(map[string]string{})
	lctx.RosDistro = "jazzy"
	lctx.SimEngine = SimEngineAuto

	joined := strings.Join(BuildSimArgs(lctx), " ")
	if !strings.Contains(joined, "sim_engine:=gz") {
		t.Fatalf("auto 는 배포판 기본값(gz)이어야 합니다: %s", joined)
	}
	if strings.Contains(joined, "scenario_file:=") {
		t.Fatalf("시나리오 미지정 시 인자를 만들면 안 됩니다: %s", joined)
	}
}

func TestNormalizeSimEngine(t *testing.T) {
	cases := map[string]string{
		"":         SimEngineAuto,
		"auto":     SimEngineAuto,
		"stub":     SimEngineStub,
		"STUB":     SimEngineStub,
		" gz ":     SimEngineGz,
		"harmonic": SimEngineGz,
	}
	for input, want := range cases {
		got, err := NormalizeSimEngine(input)
		if err != nil {
			t.Fatalf("%q 정규화 실패: %v", input, err)
		}
		if got != want {
			t.Fatalf("%q -> %q 기대, 실제 %q", input, want, got)
		}
	}

	for _, removed := range []string{"classic", "gazebo", "mujoco"} {
		if _, err := NormalizeSimEngine(removed); err == nil {
			t.Fatalf("%q 는 거부해야 합니다", removed)
		}
	}
}

func TestResolveSimEngine(t *testing.T) {
	if got := ResolveSimEngine("stub"); got != SimEngineStub {
		t.Fatalf("명시값 우선 실패: %s", got)
	}
	if got := ResolveSimEngine(""); got != SimEngineGz {
		t.Fatalf("auto 기본값은 gz 이어야 합니다: %s", got)
	}
	if got := ResolveSimEngine("알수없음"); got != SimEngineGz {
		t.Fatalf("잘못된 값은 기본값으로 되돌아야 합니다: %s", got)
	}
	if got := ResolveSimEngine("classic"); got != SimEngineGz {
		t.Fatalf("제거된 엔진은 기본값으로 되돌아야 합니다: %s", got)
	}
}

// 제거된 classic 엔진을 명시하면 안내 메시지와 함께 거부해야 한다.
func TestNormalizeSimEngineExplainsRemovedClassic(t *testing.T) {
	_, err := NormalizeSimEngine("classic")
	if err == nil {
		t.Fatal("classic 은 거부해야 합니다")
	}
	if !strings.Contains(err.Error(), "Jazzy") {
		t.Fatalf("대체 엔진(gz/Jazzy) 안내가 필요합니다: %v", err)
	}
}

// Gazebo 시뮬레이션은 노드를 50개 이상 띄운다. CycloneDDS 참여자 인덱스 상한을
// 넣지 않으면 "Failed to find a free participant index" 로 노드가 abort 하므로,
// 생성되는 URI 에 상한이 반드시 포함되어야 한다.
func TestCycloneDdsUriIncludesParticipantLimit(t *testing.T) {
	uri := CycloneDdsUri("131072B", "2097152B")

	for _, want := range []string{
		"<MaxMessageSize>131072B</MaxMessageSize>",
		"<ParticipantIndex>auto</ParticipantIndex>",
		fmt.Sprintf("<MaxAutoParticipantIndex>%d</MaxAutoParticipantIndex>", CycloneDdsMaxAutoParticipantIndex),
		"<WhcHigh>2097152B</WhcHigh>",
	} {
		if !strings.Contains(uri, want) {
			t.Fatalf("URI 에 %q 가 없습니다: %s", want, uri)
		}
	}
	if CycloneDdsMaxAutoParticipantIndex < 64 {
		t.Fatalf("참여자 상한이 너무 작습니다: %d", CycloneDdsMaxAutoParticipantIndex)
	}
}

// Watermarks 를 주지 않으면 Internal 항목을 넣지 않는다(기존 동작 보존).
func TestCycloneDdsUriOmitsWatermarksWhenNotRequested(t *testing.T) {
	uri := CycloneDdsUri("65535B", "")

	if strings.Contains(uri, "WhcHigh") {
		t.Fatalf("watermark 미지정인데 Internal 항목이 들어갔습니다: %s", uri)
	}
	if !strings.Contains(uri, "<MaxAutoParticipantIndex>") {
		t.Fatalf("참여자 상한이 빠졌습니다: %s", uri)
	}
}

// 시뮬레이션 도메인은 실기기 도메인(기본 0)과 분리되어야 한다.
func TestResolveSimDomainID(t *testing.T) {
	t.Setenv("ROS_DOMAIN_ID", "")
	t.Setenv("RC_SIM_DOMAIN_ID", "")
	if got := ResolveSimDomainID(nil); got != DefaultSimDomainID {
		t.Fatalf("기본 시뮬레이션 도메인은 %s 여야 합니다: %s", DefaultSimDomainID, got)
	}
	if DefaultSimDomainID == "0" {
		t.Fatal("시뮬레이션 기본 도메인이 0(실기기 공유)이면 안 됩니다")
	}

	t.Setenv("RC_SIM_DOMAIN_ID", "77")
	if got := ResolveSimDomainID(map[string]string{}); got != "77" {
		t.Fatalf("RC_SIM_DOMAIN_ID 우선순위 실패: %s", got)
	}

	t.Setenv("ROS_DOMAIN_ID", "5")
	if got := ResolveSimDomainID(map[string]string{"RC_SIM_DOMAIN_ID": "77"}); got != "5" {
		t.Fatalf("명시한 ROS_DOMAIN_ID 를 존중해야 합니다: %s", got)
	}

	t.Setenv("ROS_DOMAIN_ID", "")
	if got := ResolveSimDomainID(map[string]string{"ROS_DOMAIN_ID": "4"}); got != "4" {
		t.Fatalf(".env 의 ROS_DOMAIN_ID 를 존중해야 합니다: %s", got)
	}
}

// 시뮬레이션 env 주입은 전용 도메인과 DDS 공통 설정을 넣어야 한다.
func TestApplySimDdsEnv(t *testing.T) {
	t.Setenv("CYCLONEDDS_URI", "")
	t.Setenv("ROS_DOMAIN_ID", "")
	t.Setenv("RC_SIM_DDS_LAN", "")

	env := applySimDdsEnv(&LaunchContext{EnvMap: map[string]string{}}, "65535B", "", nil)
	joined := strings.Join(env, "\n")

	if !strings.Contains(joined, "ROS_DOMAIN_ID="+DefaultSimDomainID) {
		t.Fatalf("시뮬레이션 도메인이 주입되지 않았습니다: %s", joined)
	}
	if !strings.Contains(joined, "RMW_IMPLEMENTATION=rmw_cyclonedds_cpp") {
		t.Fatalf("RMW 설정이 빠졌습니다: %s", joined)
	}
}
