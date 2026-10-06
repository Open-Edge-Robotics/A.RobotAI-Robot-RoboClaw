package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"robo_claw_cli/launcher"
)

// BuildTaskActionGoalPayload ExecuteTask 액션 goal JSON 문자열을 생성합니다.
func BuildTaskActionGoalPayload(instruction string, timeoutSec float64) string {
	return fmt.Sprintf("{instruction: '%s', timeout_sec: %f}", instruction, timeoutSec)
}

// RunRosCommand ROS 2 명령어를 워크스페이스 환경에서 실행합니다.
func RunRosCommand(args ...string) error {
	projectRoot := launcher.GetProjectRoot()
	helperScript := filepath.Join(projectRoot, "scripts", "run_in_ros_env.sh")

	var cmd *exec.Cmd
	if launcher.PathExists(helperScript) {
		cmdArgs := append([]string{"--ws"}, args...)
		cmd = exec.Command(helperScript, cmdArgs...)
	} else {
		cmd = exec.Command(args[0], args[1:]...)
	}

	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

// StatusCmd 에이전트 노드 상태 토픽을 1회 조회합니다.
func StatusCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "status",
		Short: "에이전트 노드 상태 토픽을 1회 조회합니다.",
		Run: func(ctx *wcli.Context) error {
			logging.Info("에이전트 노드 상태 조회 중 (/robo_claw_agent_node/status)...")
			return RunRosCommand("ros2", "topic", "echo", "/robo_claw_agent_node/status", "--once")
		},
	}
}

// SkillsCmd 활성화된 스킬 목록을 조회합니다.
func SkillsCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "skills",
		Short: "현재 활성 스킬 목록을 조회합니다.",
		Run: func(ctx *wcli.Context) error {
			logging.Info("활성 스킬 목록 조회 중 (/robo_claw_agent_node/query_state)...")
			return RunRosCommand("ros2", "service", "call", "/robo_claw_agent_node/query_state", "robo_claw_msgs/srv/QueryState", "{}")
		},
	}
}

// TaskCmd 에이전트에게 즉시 명령을 전송합니다.
func TaskCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "task <instruction> [timeout_sec]",
		Short: "에이전트에게 즉시 명령(ExecuteTask)을 전송합니다.",
		Run: func(ctx *wcli.Context) error {
			if len(ctx.Args) < 1 {
				return fmt.Errorf("명령 내용(instruction)을 입력하세요. 예: rclaw task '방 청소해줘' 30.0")
			}
			instruction := ctx.Args[0]
			timeoutSec := 30.0
			if len(ctx.Args) > 1 {
				if t, err := strconv.ParseFloat(ctx.Args[1], 64); err == nil {
					timeoutSec = t
				}
			}

			payload := BuildTaskActionGoalPayload(instruction, timeoutSec)
			logging.Info("에이전트에 태스크 전송: %s (timeout: %.1fs)", instruction, timeoutSec)
			return RunRosCommand("ros2", "action", "send_goal", "/robo_claw_agent_node/execute_task", "robo_claw_msgs/action/ExecuteTask", payload)
		},
	}
}
