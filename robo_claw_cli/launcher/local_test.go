package launcher

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBuildMergedEnvPreservesSystem1SettingsFromEnvArtifact(t *testing.T) {
	values := map[string]string{
		"SYSTEM1_ROUTER":               "laya",
		"SYSTEM1_SHADOW":               "true",
		"SYSTEM1_SHADOW_LOG":           "/tmp/system1.jsonl",
		"SYSTEM1_SCOPE":                "navigation",
		"SYSTEM1_ENDPOINT":             "http://laya:8000",
		"SYSTEM1_PROVIDER":             "laya",
		"SYSTEM1_TIMEOUT_MS":           "450",
		"SYSTEM1_CONF_THRESHOLDS_JSON": `{"smalltalk":0.95}`,
		"SYSTEM1_SKILLS":               "get_status,identify_location",
		"SYSTEM1_MAX_OPTIONS":          "8",
		"SYSTEM1_API_KEY":              "system1-secret",
	}
	lines := make([]string, 0, len(values))
	for key, value := range values {
		lines = append(lines, key+"="+value)
	}
	envFile := filepath.Join(t.TempDir(), ".env")
	if err := os.WriteFile(envFile, []byte(strings.Join(lines, "\n")), 0600); err != nil {
		t.Fatal(err)
	}
	envSlice, envMap, err := ParseEnvFile(envFile)
	if err != nil {
		t.Fatalf("parse .env artifact: %v", err)
	}
	env := buildMergedEnv(nil, &LaunchContext{
		ProjectRoot: t.TempDir(),
		EnvSlice:    envSlice,
		EnvMap:      envMap,
	})

	for key, value := range values {
		if got := lastEnvValue(t, env, key); got != value {
			t.Errorf("expected env artifact value %s=%q to reach runtime, got %q", key, value, got)
		}
	}
}

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
