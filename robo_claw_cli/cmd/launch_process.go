package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/wkqco33/wcli/logging"
)

// launchShutdownGrace 는 런치 프로세스 그룹을 SIGTERM 으로 정리할 때 기다리는 시간입니다.
const launchShutdownGrace = 3 * time.Second

// runLaunchProcess 는 런치 프로세스를 별도 세션(프로세스 그룹)에서 실행하고,
// 종료 시점에 그룹 전체를 정리합니다.
//
// ros2 launch 가 노드를 일부 띄운 뒤 예외로 종료하면 이미 시작된 노드가
// 고아 프로세스로 남습니다. 고아는 부모가 init 으로 바뀌므로 PPid 추적으로는
// 찾을 수 없기 때문에, 세션/그룹 단위로 묶어 두고 마지막에 그룹째 정리합니다.
func runLaunchProcess(cmd *exec.Cmd) error {
	return runLaunchProcessWithGrace(cmd, launchShutdownGrace)
}

func runLaunchProcessWithGrace(cmd *exec.Cmd, grace time.Duration) error {
	if cmd.SysProcAttr == nil {
		cmd.SysProcAttr = &syscall.SysProcAttr{}
	}
	// 별도 세션이면 Ctrl+C 가 자식 그룹으로 전달되지 않으므로 아래에서 직접 전달합니다.
	// 터미널 제어권과 분리되어 백그라운드 프로세스 그룹 시그널(SIGTTIN)도 받지 않습니다.
	cmd.SysProcAttr.Setsid = true

	if err := cmd.Start(); err != nil {
		return err
	}
	pgid := cmd.Process.Pid
	logging.Info("런치 프로세스 시작 (PID: %d, PGID: %d)", cmd.Process.Pid, pgid)

	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, os.Interrupt, syscall.SIGTERM)
	defer signal.Stop(sigCh)

	done := make(chan error, 1)
	go func() {
		done <- cmd.Wait()
	}()

	var runErr error
	select {
	case sig := <-sigCh:
		logging.Warn("종료 시그널 감지 (%v). 런치 프로세스 그룹을 종료합니다.", sig)
		_ = terminateProcessGroup(pgid, grace)
		runErr = <-done
	case runErr = <-done:
	}

	// 정상/비정상 종료 모두 그룹을 정리해 남은 노드를 회수합니다.
	if err := terminateProcessGroup(pgid, grace); err != nil {
		logging.Warn("런치 프로세스 그룹 정리 실패 (PGID: %d): %v", pgid, err)
	}
	return runErr
}

// terminateProcessGroup 은 그룹 전체에 SIGTERM 을 보내고, 유예 시간 안에 끝나지
// 않으면 SIGKILL 로 확실히 정리합니다.
func terminateProcessGroup(pgid int, grace time.Duration) error {
	if pgid <= 1 {
		return nil
	}
	if pgid == syscall.Getpgrp() {
		return fmt.Errorf("자신이 속한 프로세스 그룹(%d)은 종료할 수 없습니다", pgid)
	}
	if !processGroupAlive(pgid) {
		return nil
	}
	_ = syscall.Kill(-pgid, syscall.SIGTERM)
	if waitProcessGroupGone(pgid, grace) {
		return nil
	}
	logging.Warn("프로세스 그룹 %d 이(가) SIGTERM 에 응답하지 않아 SIGKILL 로 정리합니다.", pgid)
	_ = syscall.Kill(-pgid, syscall.SIGKILL)
	if waitProcessGroupGone(pgid, grace) {
		return nil
	}
	return fmt.Errorf("프로세스 그룹 %d 정리에 실패했습니다", pgid)
}

// processGroupAlive 는 프로세스 그룹에 살아 있는(좀비 제외) 프로세스가 있는지 확인합니다.
//
// kill(-pgid, 0) 은 아직 회수되지 않은 좀비도 존재로 세기 때문에, 상태가 Z 인
// 프로세스는 제외하고 판단합니다.
func processGroupAlive(pgid int) bool {
	if pgid <= 0 {
		return false
	}
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return false
	}
	for _, entry := range entries {
		pid, convErr := strconv.Atoi(entry.Name())
		if convErr != nil || pid <= 1 {
			continue
		}
		st, ok := readProcStat("/proc", pid)
		if !ok || st.State == "Z" {
			continue
		}
		if st.Pgrp == pgid {
			return true
		}
	}
	return false
}

func waitProcessGroupGone(pgid int, grace time.Duration) bool {
	deadline := time.Now().Add(grace)
	for {
		if !processGroupAlive(pgid) {
			return true
		}
		if !time.Now().Before(deadline) {
			return false
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// terminatePID 는 개별 프로세스를 SIGTERM → SIGKILL 순서로 정리합니다.
func terminatePID(pid int, grace time.Duration) error {
	if pid <= 1 || pid == os.Getpid() {
		return nil
	}
	if err := syscall.Kill(pid, 0); err != nil {
		return nil
	}
	_ = syscall.Kill(pid, syscall.SIGTERM)
	deadline := time.Now().Add(grace)
	for time.Now().Before(deadline) {
		if err := syscall.Kill(pid, 0); err != nil {
			return nil
		}
		time.Sleep(20 * time.Millisecond)
	}
	_ = syscall.Kill(pid, syscall.SIGKILL)
	return nil
}
