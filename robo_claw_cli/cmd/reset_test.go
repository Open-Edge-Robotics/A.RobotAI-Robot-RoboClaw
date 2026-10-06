package cmd

import (
	"reflect"
	"testing"
)

// resetScriptName 은 순수 함수이므로 스크립트 경로 없이 매핑 무결성을 검증할 수 있다.
func TestResetScriptName(t *testing.T) {
	cases := map[string]string{
		"state":  "reset_robot_state.sh",
		"memory": "reset_memory.sh",
		"full":   "reset_robot_state_server_db.sh",
	}
	for use, want := range cases {
		if got := resetScriptName(use); got != want {
			t.Errorf("resetScriptName(%q) = %q, want %q", use, got, want)
		}
	}
}

func TestResetScriptNameRejectsUnknown(t *testing.T) {
	if got := resetScriptName("nope"); got != "" {
		t.Errorf("resetScriptName(unknown) = %q, want empty", got)
	}
}

// toScriptArgs 는 각 서브커맨드가 이해하는 플래그만 스크립트 장문 인자로 재조립해야 한다.
func TestResetArgsToScriptArgsFiltersByUse(t *testing.T) {
	a := &resetArgs{yes: true, dryRun: true, robot: "former@10.0.0.1", skipQdrant: true, extra: []string{"pos"}}
	gotState := a.toScriptArgs("state")
	wantState := []string{"--yes", "--dry-run", "--robot", "former@10.0.0.1", "pos"}
	if !reflect.DeepEqual(gotState, wantState) {
		t.Errorf("state args = %v, want %v", gotState, wantState)
	}

	// reset_memory.sh 는 robot/dry-run/skip-qdrant 를 모른다 → yes 와 위치 인자만 전달.
	gotMemory := a.toScriptArgs("memory")
	wantMemory := []string{"--yes", "pos"}
	if !reflect.DeepEqual(gotMemory, wantMemory) {
		t.Errorf("memory args = %v, want %v", gotMemory, wantMemory)
	}

	// full 은 모든 공용 플래그를 받는다.
	gotFull := a.toScriptArgs("full")
	wantFull := []string{"--yes", "--dry-run", "--skip-qdrant", "--robot", "former@10.0.0.1", "pos"}
	if !reflect.DeepEqual(gotFull, wantFull) {
		t.Errorf("full args = %v, want %v", gotFull, wantFull)
	}
}

// ResetRobotCmd 는 지연 없이 구성 가능해야 하고 부모 이름이 reset-robot 이어야 한다.
func TestResetRobotCmdConfigures(t *testing.T) {
	cmd := ResetRobotCmd()
	if cmd.Use != "reset-robot" {
		t.Fatalf("parent Use = %q, want %q", cmd.Use, "reset-robot")
	}
	for _, use := range []string{"state", "memory", "full"} {
		if resetScriptName(use) == "" {
			t.Errorf("subcommand %q has no script mapping", use)
		}
	}
}
