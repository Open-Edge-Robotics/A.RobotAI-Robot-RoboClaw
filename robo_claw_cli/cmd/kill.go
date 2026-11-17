package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"time"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
)

// KillRoboClawProcs 는 RoboClaw 노드 프로세스와 점유 포트를 정리합니다.
//
// 프로세스 판별은 전체 명령줄 문자열 매칭이 아니라 실행 파일/인자 basename 비교로
// 수행하며, 셸·래퍼·자기 자신·조상 프로세스는 건드리지 않습니다
// (자세한 규칙은 kill_targets.go 참고).
func KillRoboClawProcs(ports []int) error {
	nodeNames := DefaultKillNodeExecutables()
	launchFiles := DefaultKillLaunchFiles()
	parentOf := func(pid int) (int, bool) { return readParentPID("/proc", pid) }
	protected := ancestorSet(os.Getpid(), parentOf)
	procs := collectProcessCmdlines("/proc")
	byPID := make(map[int]procCmdline, len(procs))
	for _, p := range procs {
		byPID[p.PID] = p
	}

	for _, target := range selectKillTargets(procs, os.Getpid(), parentOf, nodeNames, launchFiles) {
		logging.Info("RoboClaw 프로세스 종료 (PID: %d): %s", target.PID, strings.Join(target.Argv, " "))
		_ = terminatePID(target.PID, time.Second)
	}

	for _, port := range ports {
		out, err := exec.Command("lsof", "-t", fmt.Sprintf("-i:%d", port)).Output()
		if err != nil || len(out) == 0 {
			continue
		}
		var pids []int
		for _, pidText := range strings.Fields(string(out)) {
			if pid, convErr := strconv.Atoi(pidText); convErr == nil {
				pids = append(pids, pid)
			}
		}

		targets, skipped := selectPortKillTargets(pids, byPID, protected, nodeNames, launchFiles)
		for _, holder := range skipped {
			// 예: pi-bot-connect 는 RoboClaw gRPC 와 같은 50052 를 사용한다.
			// 이런 프로세스를 종료하면 도구 자체가 조용히 죽는다.
			logging.Warn("포트 %d 는 RoboClaw 프로세스가 아닌 %s(PID: %d)가 점유 중이라 건너뜁니다.",
				port, strings.Join(holder.Argv, " "), holder.PID)
			rich.Println("[yellow]⚠ 포트 %d 점유 프로세스(PID: %d, %s)는 RoboClaw 가 아니므로 그대로 둡니다[/yellow]",
				port, holder.PID, holder.Argv[0])
		}
		for _, target := range targets {
			logging.Warn("포트 %d 점유 중인 프로세스(PID: %d) 종료", port, target.PID)
			rich.Println("[yellow]⚠ 포트 %d 점유 중인 프로세스(PID: %d) 종료[/yellow]", port, target.PID)
			_ = terminatePID(target.PID, time.Second)
		}
	}
	time.Sleep(1 * time.Second)
	return nil
}

// KillCmd RoboClaw 관련 프로세스 및 포트를 정리하는 커맨드
func KillCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "kill",
		Short: "RoboClaw 관련 프로세스 및 점유 포트를 강제 정리합니다.",
		Run: func(ctx *wcli.Context) error {
			logging.Info("RoboClaw 관련 프로세스 및 포트 정리 중...")
			rich.Println("[cyan]🔄 RoboClaw 관련 프로세스 및 포트 정리 중...[/cyan]")
			if err := KillRoboClawProcs([]int{8080, 50051, 50052}); err != nil {
				return err
			}
			// gz sim 은 자체 프로세스 그룹으로 detach 되므로 별도로 회수한다.
			_ = KillSimRuntimeProcs()
			logging.Info("정리 완료")
			rich.Println("[green]✔ 정리 완료[/green]")
			return nil
		},
	}
}

// simKillTerminateTimeout gz GUI/server 종료 대기 시간.
// Gazebo 는 SIGTERM 처리에 수 초가 걸리므로 1초로는 SIGKILL 승격이 잦다.
const simKillTerminateTimeout = 3 * time.Second

// KillSimRuntimeProcs 이전 실행이 남긴 시뮬레이터 프로세스(gz sim server/gui 등)를 정리합니다.
//
// gz 는 server/gui 를 별도 프로세스 그룹으로 띄우므로 런치 그룹 정리로 회수되지 않는다.
// 남아 있으면 다음 실행의 `ros_gz_sim create` 가 월드 목록을 받지 못해 무한 대기한다.
func KillSimRuntimeProcs() int {
	parentOf := func(pid int) (int, bool) { return readParentPID("/proc", pid) }
	procs := collectProcessCmdlines("/proc")

	killed := 0
	for _, target := range selectSimRuntimeTargets(procs, os.Getpid(), parentOf) {
		logging.Warn("시뮬레이터 프로세스 종료 (PID: %d): %s", target.PID, strings.Join(target.Argv, " "))
		rich.Println("[yellow]⚠ 이전 실행의 시뮬레이터 프로세스 종료 (PID: %d, %s)[/yellow]",
			target.PID, target.Argv[0])
		_ = terminatePID(target.PID, simKillTerminateTimeout)
		killed++
	}
	if killed > 0 {
		time.Sleep(500 * time.Millisecond)
	}
	return killed
}
