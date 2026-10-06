package cmd

import (
	"strings"
	"testing"
)

func TestStatusCmdCreation(t *testing.T) {
	cmd := StatusCmd()
	if cmd == nil {
		t.Fatal("StatusCmd() returned nil")
	}
	if cmd.Use != "status" {
		t.Errorf("expected Use to be 'status', got '%s'", cmd.Use)
	}
}

func TestSkillsCmdCreation(t *testing.T) {
	cmd := SkillsCmd()
	if cmd == nil {
		t.Fatal("SkillsCmd() returned nil")
	}
	if cmd.Use != "skills" {
		t.Errorf("expected Use to be 'skills', got '%s'", cmd.Use)
	}
}

func TestTaskCmdCreation(t *testing.T) {
	cmd := TaskCmd()
	if cmd == nil {
		t.Fatal("TaskCmd() returned nil")
	}
	if !strings.HasPrefix(cmd.Use, "task") {
		t.Errorf("expected Use to start with 'task', got '%s'", cmd.Use)
	}
}

func TestBuildTaskActionGoalPayload(t *testing.T) {
	payload := BuildTaskActionGoalPayload("청소 시작", 45.0)
	expected := "{instruction: '청소 시작', timeout_sec: 45.000000}"
	if payload != expected {
		t.Errorf("expected %s, got %s", expected, payload)
	}
}
