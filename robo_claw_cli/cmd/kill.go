package cmd

import (
	"fmt"
	"os/exec"
	"strings"
	"time"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
)

// DefaultKillProcessPatterns 반환: 종료 대상 프로세스 이름 목록
func DefaultKillProcessPatterns() []string {
	return []string{
		"robo_claw_agent_node",
		"robo_claw_channel_node",
		"robo_claw_core_node",
		"robo_claw_discovery_node",
		"robo_claw_grpc_node",
		"robo_claw_manipulator_node",
		"robo_claw_messenger_client_node",
		"object_detector_node",
		"robo_claw.launch.py",
		"robo_claw_sim.launch.py",
	}
}

// KillRoboClawProcs 프로세스 패턴과 점유 포트를 정리합니다.
func KillRoboClawProcs(ports []int) error {
	patterns := DefaultKillProcessPatterns()
	for _, pattern := range patterns {
		cmd := exec.Command("pkill", "-9", "-f", pattern)
		_ = cmd.Run()
	}

	for _, port := range ports {
		out, err := exec.Command("lsof", "-t", fmt.Sprintf("-i:%d", port)).Output()
		if err == nil && len(out) > 0 {
			pids := strings.Fields(string(out))
			for _, pid := range pids {
				logging.Warn("포트 %d 점유 중인 프로세스(PID: %s) 종료", port, pid)
				rich.Println("[yellow]⚠ 포트 %d 점유 중인 프로세스(PID: %s) 종료[/yellow]", port, pid)
				_ = exec.Command("kill", "-9", pid).Run()
			}
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
			logging.Info("정리 완료")
			rich.Println("[green]✔ 정리 완료[/green]")
			return nil
		},
	}
}
