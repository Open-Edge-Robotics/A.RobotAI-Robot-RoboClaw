package cmd

import (
	"testing"
)

func TestKillCommandCreation(t *testing.T) {
	cmd := KillCmd()
	if cmd == nil {
		t.Fatal("KillCmd() returned nil")
	}
	if cmd.Use != "kill" {
		t.Errorf("expected Use to be 'kill', got '%s'", cmd.Use)
	}
}

func TestTargetProcessPatterns(t *testing.T) {
	patterns := DefaultKillProcessPatterns()
	if len(patterns) == 0 {
		t.Fatal("expected default process patterns to be non-empty")
	}
	expected := map[string]bool{
		"robo_claw_agent_node":   false,
		"robo_claw_channel_node": false,
		"robo_claw.launch.py":    false,
	}
	for _, p := range patterns {
		if _, ok := expected[p]; ok {
			expected[p] = true
		}
	}
	for name, found := range expected {
		if !found {
			t.Errorf("expected pattern '%s' in default kill patterns", name)
		}
	}
}
