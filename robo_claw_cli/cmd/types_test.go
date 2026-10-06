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
