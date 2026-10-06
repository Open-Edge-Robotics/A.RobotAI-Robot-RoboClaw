package cmd

import (
	"testing"
)

func TestRunCmdCreation(t *testing.T) {
	cmd := RunCmd()
	if cmd == nil {
		t.Fatal("RunCmd() returned nil")
	}
	if cmd.Use != "run [llm] [model] [channel]" {
		t.Errorf("expected Use to be 'run [llm] [model] [channel]', got '%s'", cmd.Use)
	}
}

func TestSimCmdCreation(t *testing.T) {
	cmd := SimCmd()
	if cmd == nil {
		t.Fatal("SimCmd() returned nil")
	}
	if cmd.Use != "sim [llm] [model] [channel]" {
		t.Errorf("expected Use to be 'sim [llm] [model] [channel]', got '%s'", cmd.Use)
	}
}

func TestBuildRunLaunchArgs(t *testing.T) {
	envMap := map[string]string{
		"RC_LLM_PROVIDER":   "ollama",
		"RC_LLM_MODEL":      "qwen2.5:7b",
		"RC_CAMERA_TOPIC":   "/my_cam",
		"RC_USE_DASHBOARD":  "true",
		"RC_DASHBOARD_HOST": "10.0.0.5",
		"RC_DASHBOARD_PORT": "9099",
	}
	args := BuildRunLaunchArgs(envMap, "azure", "gpt-4.1", "true", []string{"extra_arg:=123"})

	// positional overrides should take precedence if provided
	foundProvider := false
	foundModel := false
	foundCamera := false
	foundExtra := false
	foundUseDashboard := false
	foundDashHost := false
	foundDashPort := false

	for _, a := range args {
		if a == "llm_provider:=azure" {
			foundProvider = true
		}
		if a == "llm_model:=gpt-4.1" {
			foundModel = true
		}
		if a == "camera_topic:=/my_cam" {
			foundCamera = true
		}
		if a == "extra_arg:=123" {
			foundExtra = true
		}
		if a == "use_dashboard:=true" {
			foundUseDashboard = true
		}
		if a == "dashboard_host:=10.0.0.5" {
			foundDashHost = true
		}
		if a == "dashboard_port:=9099" {
			foundDashPort = true
		}
	}

	if !foundProvider {
		t.Errorf("expected llm_provider:=azure in args: %v", args)
	}
	if !foundModel {
		t.Errorf("expected llm_model:=gpt-4.1 in args: %v", args)
	}
	if !foundCamera {
		t.Errorf("expected camera_topic:=/my_cam in args: %v", args)
	}
	if !foundExtra {
		t.Errorf("expected extra_arg:=123 in args: %v", args)
	}
	if !foundUseDashboard {
		t.Errorf("expected use_dashboard:=true in args: %v", args)
	}
	if !foundDashHost {
		t.Errorf("expected dashboard_host:=10.0.0.5 in args: %v", args)
	}
	if !foundDashPort {
		t.Errorf("expected dashboard_port:=9099 in args: %v", args)
	}
}
