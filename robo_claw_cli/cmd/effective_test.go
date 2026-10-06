package cmd

import "testing"

func TestDisplaySecretMasksConfiguredSecret(t *testing.T) {
	if got := displaySecret("OPENAI_API_KEY", "secret"); got != "********" {
		t.Fatalf("secret was not masked: %s", got)
	}
}

func TestDisplaySecretMasksSystem1APIKey(t *testing.T) {
	if got := displaySecret("SYSTEM1_API_KEY", "secret"); got != "********" {
		t.Fatalf("System 1 API key was not masked: %s", got)
	}
}

func TestDisplaySecretKeepsNonSecretValue(t *testing.T) {
	if got := displaySecret("RC_LLM_MODEL", "gpt-4.1"); got != "gpt-4.1" {
		t.Fatalf("non-secret value was changed: %s", got)
	}
}

func TestEffectiveCommandListsSystem1EnvironmentSettings(t *testing.T) {
	expected := map[string]string{
		"system1_router":               "SYSTEM1_ROUTER",
		"system1_shadow":               "SYSTEM1_SHADOW",
		"system1_shadow_log":           "SYSTEM1_SHADOW_LOG",
		"system1_scope":                "SYSTEM1_SCOPE",
		"system1_endpoint":             "SYSTEM1_ENDPOINT",
		"system1_provider":             "SYSTEM1_PROVIDER",
		"system1_timeout_ms":           "SYSTEM1_TIMEOUT_MS",
		"system1_conf_thresholds_json": "SYSTEM1_CONF_THRESHOLDS_JSON",
		"system1_skills":               "SYSTEM1_SKILLS",
		"system1_max_options":          "SYSTEM1_MAX_OPTIONS",
		"system1_api_key":              "SYSTEM1_API_KEY",
	}
	for configKey, envKey := range expected {
		if effectiveKeys[configKey] != envKey {
			t.Errorf("effectiveKeys[%q] = %q, want %q", configKey, effectiveKeys[configKey], envKey)
		}
	}
	if got := displaySecret("SYSTEM1_API_KEY", "token"); got != "********" {
		t.Errorf("System 1 API key must be masked, got %q", got)
	}
}
