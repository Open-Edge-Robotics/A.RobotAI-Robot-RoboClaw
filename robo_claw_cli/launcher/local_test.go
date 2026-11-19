package launcher

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBuildMergedEnvAddsLangsmithWorkspaceIDFromActiveConfig(t *testing.T) {
	t.Setenv("LANGSMITH_WORKSPACE_ID", "")

	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvMap:      map[string]string{},
		ActiveConfig: RoboClawConfig{
			LangsmithWorkspaceId: "workspace-123",
		},
	})

	if !strings.Contains(strings.Join(env, "\n"), "LANGSMITH_WORKSPACE_ID=workspace-123") {
		t.Fatalf("expected workspace ID from active config in launch environment, got %q", env)
	}
}

func TestBuildMergedEnvPreservesLangsmithAPIKeyFromEnvArtifact(t *testing.T) {
	t.Setenv("LANGSMITH_API_KEY", "compose-placeholder")

	const artifactKey = "lsv2_artifact_key"
	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvSlice:    []string{"LANGSMITH_API_KEY=" + artifactKey},
		EnvMap:      map[string]string{"LANGSMITH_API_KEY": artifactKey},
		ActiveConfig: RoboClawConfig{
			LangsmithApiKey: "lsv2_json_key",
		},
	})

	if got := lastEnvValue(t, env, "LANGSMITH_API_KEY"); got != artifactKey {
		t.Fatalf("expected artifact API key to remain effective, got %q", got)
	}
}

func TestBuildMergedEnvDoesNotInjectMaskedLangsmithAPIKey(t *testing.T) {
	t.Setenv("LANGSMITH_API_KEY", "")

	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvMap:      map[string]string{},
		ActiveConfig: RoboClawConfig{
			LangsmithApiKey: "********",
		},
	})

	if strings.Contains(strings.Join(env, "\n"), "LANGSMITH_API_KEY=********") {
		t.Fatal("masked API key must not be injected")
	}
}

func TestBuildMergedEnvUsesActiveConfigLangsmithAPIKeyAsFallback(t *testing.T) {
	t.Setenv("LANGSMITH_API_KEY", "")

	const configKey = "lsv2_config_key"
	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot:  t.TempDir(),
		EnvMap:       map[string]string{},
		ActiveConfig: RoboClawConfig{LangsmithApiKey: configKey},
	})

	if got := lastEnvValue(t, env, "LANGSMITH_API_KEY"); got != configKey {
		t.Fatalf("expected active config API key fallback, got %q", got)
	}
}

func lastEnvValue(t *testing.T, env []string, key string) string {
	t.Helper()
	prefix := key + "="
	for i := len(env) - 1; i >= 0; i-- {
		if strings.HasPrefix(env[i], prefix) {
			return strings.TrimPrefix(env[i], prefix)
		}
	}
	t.Fatalf("environment key %q not found", key)
	return ""
}

// TestBuildMergedEnvKeepsSystem1StepRouting 은 서버가 배포한 .env의
// SYSTEM1_STEP_ROUTING이 ros2 launch 프로세스 환경으로 그대로 전달되는지 검증한다.
// SYSTEM1_* 는 launch 인자가 아니라 환경변수이므로 이 경로가 끊기면
// 웹에서 고른 step routing 모드가 조용히 무시된다.
func TestBuildMergedEnvKeepsSystem1StepRouting(t *testing.T) {
	t.Setenv("SYSTEM1_STEP_ROUTING", "")

	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvSlice:    []string{"SYSTEM1_STEP_ROUTING=on"},
		EnvMap:      map[string]string{"SYSTEM1_STEP_ROUTING": "on"},
	})

	if got := lastEnvValue(t, env, "SYSTEM1_STEP_ROUTING"); got != "on" {
		t.Fatalf("expected SYSTEM1_STEP_ROUTING from env artifact, got %q", got)
	}
}

// TestBuildMergedEnvResolvesSkillsGuideForFleetConnector 는 local 모드(CLOiD 컨테이너 등)에서
// 서버 기본값 RC_SKILLS_GUIDE_FILE=config/SKILLS.md 가 작업 디렉터리에 따라 깨지지 않고
// 캐시된 SKILLS.md 절대 경로로 전달되는지 검증한다. robo_claw_grpc FleetControl connector는
// 이 환경변수로 capability manifest를 만든다.
func TestBuildMergedEnvResolvesSkillsGuideForFleetConnector(t *testing.T) {
	cacheDir := t.TempDir()
	cached := filepath.Join(cacheDir, "SKILLS.md")
	if err := os.WriteFile(cached, []byte("| x | `get_status` |\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	projectRoot := t.TempDir()
	projectSkills := filepath.Join(projectRoot, "docs", "SKILLS.md")
	if err := os.MkdirAll(filepath.Dir(projectSkills), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(projectSkills, []byte("| x | `stop` |\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	absolute := filepath.Join(t.TempDir(), "custom_SKILLS.md")
	if err := os.WriteFile(absolute, []byte("| x | `rotate` |\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	cases := []struct {
		name     string
		value    string
		cacheDir string
		want     string
	}{
		{"server default relative path uses cache", "config/SKILLS.md", cacheDir, cached},
		{"unset uses cache", "", cacheDir, cached},
		{"existing absolute path is kept", absolute, cacheDir, absolute},
		{"relative path under project root is made absolute", "docs/SKILLS.md", cacheDir, projectSkills},
		{"missing path without cache is left unchanged", "config/SKILLS.md", t.TempDir(), "config/SKILLS.md"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("RC_SKILLS_GUIDE_FILE", "")
			envMap := map[string]string{}
			var envSlice []string
			if tc.value != "" {
				envMap["RC_SKILLS_GUIDE_FILE"] = tc.value
				envSlice = []string{"RC_SKILLS_GUIDE_FILE=" + tc.value}
			}
			env := buildMergedEnv(nil, &LaunchContext{
				ProjectRoot: projectRoot,
				CacheDir:    tc.cacheDir,
				EnvSlice:    envSlice,
				EnvMap:      envMap,
			})
			if got := lastEnvValue(t, env, "RC_SKILLS_GUIDE_FILE"); got != tc.want {
				t.Fatalf("RC_SKILLS_GUIDE_FILE = %q, want %q", got, tc.want)
			}
		})
	}
}

// TestBuildMergedEnvKeepsLangfuseArtifactSecretKey 은 배포된 .env 의 secret key 가
// config JSON 값으로 덮이지 않는지 검증한다. config JSON 은 마스킹 값을 돌려줄 수 있으므로
// artifact 값이 항상 우선이어야 한다.
func TestBuildMergedEnvKeepsLangfuseArtifactSecretKey(t *testing.T) {
	t.Setenv("LANGFUSE_SECRET_KEY", "")

	const artifactKey = "sk-lf_artifact_key"
	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvSlice:    []string{"LANGFUSE_SECRET_KEY=" + artifactKey},
		EnvMap:      map[string]string{"LANGFUSE_SECRET_KEY": artifactKey},
		ActiveConfig: RoboClawConfig{
			LangfuseSecretKey: "sk-lf_json_key",
		},
	})

	if got := lastEnvValue(t, env, "LANGFUSE_SECRET_KEY"); got != artifactKey {
		t.Fatalf("expected artifact secret key to remain effective, got %q", got)
	}
}

// TestBuildMergedEnvDoesNotInjectMaskedLangfuseKeys 는 서버가 마스킹 값을 돌려줄 때
// 그 값이 실제 key 로 주입되지 않는지 검증한다.
func TestBuildMergedEnvDoesNotInjectMaskedLangfuseKeys(t *testing.T) {
	t.Setenv("LANGFUSE_PUBLIC_KEY", "")
	t.Setenv("LANGFUSE_SECRET_KEY", "")

	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvMap:      map[string]string{},
		ActiveConfig: RoboClawConfig{
			LangfusePublicKey: "********",
			LangfuseSecretKey: "********",
		},
	})

	joined := strings.Join(env, "\n")
	for _, key := range []string{"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"} {
		if strings.Contains(joined, key+"=********") {
			t.Fatalf("masked %s must not be injected", key)
		}
	}
}

// TestBuildMergedEnvUsesActiveConfigLangfuseKeysAsFallback 은 artifact 에 key 가 없을 때
// config JSON 값으로 보완하는지 검증한다.
func TestBuildMergedEnvUsesActiveConfigLangfuseKeysAsFallback(t *testing.T) {
	t.Setenv("LANGFUSE_PUBLIC_KEY", "")
	t.Setenv("LANGFUSE_SECRET_KEY", "")

	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvMap:      map[string]string{},
		ActiveConfig: RoboClawConfig{
			LangfusePublicKey: "pk-lf_config_key",
			LangfuseSecretKey: "sk-lf_config_key",
		},
	})

	if got := lastEnvValue(t, env, "LANGFUSE_PUBLIC_KEY"); got != "pk-lf_config_key" {
		t.Fatalf("expected active config public key fallback, got %q", got)
	}
	if got := lastEnvValue(t, env, "LANGFUSE_SECRET_KEY"); got != "sk-lf_config_key" {
		t.Fatalf("expected active config secret key fallback, got %q", got)
	}
}

// TestBuildMergedEnvAddsLangfuseHostFromActiveConfig 은 자체 호스팅 서버 주소가
// ros2 launch 프로세스 환경으로 전달되는지 검증한다. LANGFUSE_HOST 는 launch 인자가
// 아니라 환경변수이므로 이 경로가 끊기면 자체 서버 대신 Langfuse Cloud 로 전송된다.
func TestBuildMergedEnvAddsLangfuseHostFromActiveConfig(t *testing.T) {
	t.Setenv("LANGFUSE_HOST", "")

	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvMap:      map[string]string{},
		ActiveConfig: RoboClawConfig{
			LangfuseHost: "http://192.168.50.47:3000",
		},
	})

	if got := lastEnvValue(t, env, "LANGFUSE_HOST"); got != "http://192.168.50.47:3000" {
		t.Fatalf("expected Langfuse host from active config, got %q", got)
	}
}

// TestBuildMergedEnvAddsLangfuseTracingFromActiveConfig 은 config 의 tracing 값이
// artifact 에 값이 없을 때만 주입되는지 검증한다.
func TestBuildMergedEnvAddsLangfuseTracingFromActiveConfig(t *testing.T) {
	t.Setenv("LANGFUSE_TRACING", "")

	enabled := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot:  t.TempDir(),
		EnvMap:       map[string]string{},
		ActiveConfig: RoboClawConfig{LangfuseTracing: true},
	})
	if got := lastEnvValue(t, enabled, "LANGFUSE_TRACING"); got != "true" {
		t.Fatalf("expected LANGFUSE_TRACING=true from active config, got %q", got)
	}

	disabled := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot:  t.TempDir(),
		EnvMap:       map[string]string{},
		ActiveConfig: RoboClawConfig{LangfuseTracing: false},
	})
	if strings.Contains(strings.Join(disabled, "\n"), "LANGFUSE_TRACING=true") {
		t.Fatal("LANGFUSE_TRACING must not be enabled when active config is false")
	}
}

// TestBuildMergedEnvKeepsLangfuseTracingFromEnvArtifact 은 배포된 .env 의 off 값이
// 캐시된 config 의 true 로 덮이지 않는지 검증한다.
func TestBuildMergedEnvKeepsLangfuseTracingFromEnvArtifact(t *testing.T) {
	t.Setenv("LANGFUSE_TRACING", "")

	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot:  t.TempDir(),
		EnvSlice:     []string{"LANGFUSE_TRACING=false"},
		EnvMap:       map[string]string{"LANGFUSE_TRACING": "false"},
		ActiveConfig: RoboClawConfig{LangfuseTracing: true},
	})

	if got := lastEnvValue(t, env, "LANGFUSE_TRACING"); got != "false" {
		t.Fatalf("expected artifact tracing value to remain effective, got %q", got)
	}
}
