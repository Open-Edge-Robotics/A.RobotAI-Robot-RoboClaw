package cmd

import (
	"encoding/json"
	"testing"
)

func TestRoboClawConfigPreservesLangsmithWorkspaceID(t *testing.T) {
	var config RoboClawConfig
	if err := json.Unmarshal([]byte(`{"langsmith_workspace_id":"workspace-123"}`), &config); err != nil {
		t.Fatalf("unmarshal config: %v", err)
	}
	if config.LangsmithWorkspaceId != "workspace-123" {
		t.Fatalf("expected workspace ID from server config, got %q", config.LangsmithWorkspaceId)
	}

	serialized, err := json.Marshal(config)
	if err != nil {
		t.Fatalf("marshal cached config: %v", err)
	}
	var cached map[string]any
	if err := json.Unmarshal(serialized, &cached); err != nil {
		t.Fatalf("unmarshal cached config: %v", err)
	}
	if cached["langsmith_workspace_id"] != "workspace-123" {
		t.Fatalf("expected workspace ID retained in config cache, got %v", cached["langsmith_workspace_id"])
	}
}

func TestRoboClawConfigPreservesLangfuseFields(t *testing.T) {
	var config RoboClawConfig
	if err := json.Unmarshal([]byte(`{
		"langfuse_tracing": true,
		"langfuse_public_key": "pk-lf-public",
		"langfuse_secret_key": "sk-lf-secret",
		"langfuse_host": "http://192.168.50.47:3000"
	}`), &config); err != nil {
		t.Fatalf("unmarshal config: %v", err)
	}
	if !config.LangfuseTracing {
		t.Fatalf("expected langfuse_tracing from server config")
	}
	if config.LangfusePublicKey != "pk-lf-public" {
		t.Fatalf("expected public key from server config, got %q", config.LangfusePublicKey)
	}
	if config.LangfuseSecretKey != "sk-lf-secret" {
		t.Fatalf("expected secret key from server config, got %q", config.LangfuseSecretKey)
	}
	if config.LangfuseHost != "http://192.168.50.47:3000" {
		t.Fatalf("expected host from server config, got %q", config.LangfuseHost)
	}

	serialized, err := json.Marshal(config)
	if err != nil {
		t.Fatalf("marshal cached config: %v", err)
	}
	var cached map[string]any
	if err := json.Unmarshal(serialized, &cached); err != nil {
		t.Fatalf("unmarshal cached config: %v", err)
	}
	want := map[string]any{
		"langfuse_tracing":    true,
		"langfuse_public_key": "pk-lf-public",
		"langfuse_secret_key": "sk-lf-secret",
		"langfuse_host":       "http://192.168.50.47:3000",
	}
	for key, expected := range want {
		if cached[key] != expected {
			t.Fatalf("expected %s retained in config cache, got %v", key, cached[key])
		}
	}
}
