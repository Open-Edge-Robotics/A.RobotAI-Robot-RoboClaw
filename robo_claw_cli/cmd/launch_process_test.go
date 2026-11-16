package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// waitForGone polls until pid no longer exists.
func waitForGone(t *testing.T, pid int, timeout time.Duration) bool {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if err := syscall.Kill(pid, 0); err != nil {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return false
}

func waitForGroupGone(t *testing.T, pgid int, timeout time.Duration) bool {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		if !processGroupAlive(pgid) {
			return true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return false
}

func readPIDFile(t *testing.T, path string) int {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		raw, err := os.ReadFile(path)
		if err == nil {
			if pid, convErr := strconv.Atoi(strings.TrimSpace(string(raw))); convErr == nil && pid > 0 {
				return pid
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("pid file %s was not written", path)
	return 0
}

// readSessionID 는 /proc/<pid>/stat 의 session id 를 읽습니다.
func readSessionID(t *testing.T, pid int) int {
	t.Helper()
	st, ok := readProcStat("/proc", pid)
	if !ok {
		t.Fatalf("read stat for %d failed", pid)
	}
	return st.Session
}

// startLaunchScript runs script through runLaunchProcess in the background so the
// test can inspect and terminate the resulting process group.
func startLaunchScript(t *testing.T, script string, grace time.Duration) (*exec.Cmd, chan error) {
	t.Helper()
	cmd := exec.Command("sh", "-c", script)
	errCh := make(chan error, 1)
	go func() {
		errCh <- runLaunchProcessWithGrace(cmd, grace)
	}()
	return cmd, errCh
}

func waitError(t *testing.T, errCh chan error, timeout time.Duration) error {
	t.Helper()
	select {
	case err := <-errCh:
		return err
	case <-time.After(timeout):
		t.Fatal("runLaunchProcess did not return in time")
		return nil
	}
}

// A failed launch must not leave the nodes it already started alive.
func TestRunLaunchProcessCleansGroupAfterFailure(t *testing.T) {
	pidFile := filepath.Join(t.TempDir(), "child.pid")
	script := fmt.Sprintf("sh -c 'sleep 30' & echo $! > %s; exit 1", pidFile)

	cmd, errCh := startLaunchScript(t, script, 200*time.Millisecond)
	err := waitError(t, errCh, 10*time.Second)
	if err == nil {
		t.Fatal("expected the failing launch to return an error")
	}
	if !strings.Contains(err.Error(), "exit status 1") {
		t.Fatalf("error = %v, want it to keep the launch exit status", err)
	}

	pgid := cmd.Process.Pid
	if !waitForGroupGone(t, pgid, 2*time.Second) {
		t.Fatalf("process group %d still has members after cleanup", pgid)
	}
	childPID := readPIDFile(t, pidFile)
	if !waitForGone(t, childPID, 2*time.Second) {
		t.Fatalf("orphaned child %d survived the launch cleanup", childPID)
	}
}

// A normal exit also cleans up background nodes started by the launch.
func TestRunLaunchProcessCleansGroupAfterSuccess(t *testing.T) {
	pidFile := filepath.Join(t.TempDir(), "child.pid")
	script := fmt.Sprintf("sh -c 'sleep 30' & echo $! > %s; exit 0", pidFile)

	cmd, errCh := startLaunchScript(t, script, 200*time.Millisecond)
	if err := waitError(t, errCh, 10*time.Second); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	pgid := cmd.Process.Pid
	if !waitForGroupGone(t, pgid, 2*time.Second) {
		t.Fatalf("process group %d still has members after a successful run", pgid)
	}
	childPID := readPIDFile(t, pidFile)
	if !waitForGone(t, childPID, 2*time.Second) {
		t.Fatalf("background child %d survived the launch cleanup", childPID)
	}
}

// The launch runs in its own session so it does not depend on the caller's tty.
func TestRunLaunchProcessUsesOwnSession(t *testing.T) {
	pidFile := filepath.Join(t.TempDir(), "sid.pid")
	script := fmt.Sprintf("echo $$ > %s; sleep 30", pidFile)

	cmd, errCh := startLaunchScript(t, script, 200*time.Millisecond)
	shellPID := readPIDFile(t, pidFile)
	if sid := readSessionID(t, shellPID); sid != shellPID {
		t.Errorf("session id %d != child pid %d (Setsid not applied)", sid, shellPID)
	}
	// 끝맺기: 그룹을 정리하면 runLaunchProcess 가 반환됩니다.
	_ = terminateProcessGroup(cmd.Process.Pid, 200*time.Millisecond)
	waitError(t, errCh, 5*time.Second)
}

func TestTerminateProcessGroupRefusesOwnGroup(t *testing.T) {
	if err := terminateProcessGroup(syscall.Getpgrp(), 100*time.Millisecond); err == nil {
		t.Fatal("terminateProcessGroup must refuse the caller's own process group")
	}
}

func TestTerminateProcessGroupKillsStubbornGroup(t *testing.T) {
	pidFile := filepath.Join(t.TempDir(), "stubborn2.pid")
	script := fmt.Sprintf("trap '' TERM; echo $$ > %s; sleep 30", pidFile)

	cmd := exec.Command("sh", "-c", script)
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		t.Fatalf("start: %v", err)
	}
	pgid := cmd.Process.Pid
	childPID := readPIDFile(t, pidFile)

	start := time.Now()
	if err := terminateProcessGroup(pgid, 200*time.Millisecond); err != nil {
		t.Fatalf("terminateProcessGroup: %v", err)
	}
	if elapsed := time.Since(start); elapsed > 5*time.Second {
		t.Fatalf("terminateProcessGroup took %s, expected SIGKILL escalation", elapsed)
	}
	// 좀비는 회수 전까지 kill(pid, 0) 으로 존재하는 것처럼 보이므로 먼저 회수합니다.
	_ = cmd.Wait()
	if !waitForGone(t, childPID, 2*time.Second) {
		t.Fatalf("child %d ignoring SIGTERM survived SIGKILL escalation", childPID)
	}
}
