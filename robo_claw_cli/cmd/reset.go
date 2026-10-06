package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"

	"github.com/wkqco33/wcli"

	"robo_claw_cli/launcher"
)

// resetScriptName 반환: reset-robot 서브커맨드(use)에 대응하는 scripts/ 아래 bash 스크립트명.
// 스크립트가 진실 소스이므로 Go 쪽은 경로 해석 + 실행 위임만 담당한다(로직 이중화 금지).
func resetScriptName(use string) string {
	switch use {
	case "state":
		return "reset_robot_state.sh"
	case "memory":
		return "reset_memory.sh"
	case "full":
		return "reset_robot_state_server_db.sh"
	default:
		return ""
	}
}

// resetArgs 각 reset-robot 서브커맨드에서 공용으로 인식하는 플래그 모음.
// 등록된 값은 스크립트가 스스로 이해하는 장문 플래그로 재조립해 전달한다.
type resetArgs struct {
	yes         bool
	dryRun      bool
	restart     bool
	robot       string
	qdrantURL   string
	collection  string
	container   string
	memoryDir   string
	configCache string
	skipConfig  bool
	skipMemory  bool
	skipQdrant  bool
	extra       []string // 스크립트에 그대로 전달할 위치 인자
}

// register 부모 커맨드의 PersistentFlags 에 공용 플래그를 등록한다.
func (a *resetArgs) register(cmd *wcli.Command) {
	f := cmd.PersistentFlags()
	f.BoolVar(&a.yes, "yes", "y", false, "확인 프롬프트 생략")
	f.BoolVar(&a.dryRun, "dry-run", "", false, "실행 없이 실행될 명령/계획만 출력")
	f.BoolVar(&a.restart, "restart", "", false, "초기화 후 에이전트 컨테이너 재시작")
	f.StringVar(&a.robot, "robot", "", "", "SSH 대상 (user@host)")
	f.StringVar(&a.qdrantURL, "qdrant-url", "", "", "Qdrant 주소")
	f.StringVar(&a.collection, "collection", "", "", "Qdrant 컬렉션명")
	f.StringVar(&a.container, "container", "", "", "에이전트 컨테이너명")
	f.StringVar(&a.memoryDir, "memory-dir", "", "", "메모리 경로")
	f.StringVar(&a.configCache, "config-cache", "", "", "config 캐시 경로")
	f.BoolVar(&a.skipConfig, "skip-config", "", false, "config 캐시 단계 생략")
	f.BoolVar(&a.skipMemory, "skip-memory", "", false, "메모리 단계 생략")
	f.BoolVar(&a.skipQdrant, "skip-qdrant", "", false, "Qdrant 단계 생략")
}

// knownFlags 스크립트별로 실제로 이해하는 플래그(wcli 등록명) 집합. 스크립트가 모르는
// 플래그를 전달하지 않도록 서브커맨드에 맞는 것만 재구성한다.
func knownFlags(use string) map[string]bool {
	switch use {
	case "state":
		return map[string]bool{"yes": true, "dryRun": true, "restart": true, "robot": true}
	case "memory":
		// reset_memory.sh 는 -y/--yes 만 플래그로 받고 나머지는 환경변수로 제어한다.
		return map[string]bool{"yes": true, "container": true, "memoryDir": true, "qdrantURL": true, "collection": true}
	case "full":
		return map[string]bool{
			"yes": true, "dryRun": true, "restart": true, "robot": true,
			"qdrantURL": true, "collection": true, "container": true, "memoryDir": true,
			"configCache": true, "skipConfig": true, "skipMemory": true, "skipQdrant": true,
		}
	default:
		return nil
	}
}

// toScriptArgs 서브커맨드(use)가 아는 플래그만 스크립트 장문 플래그로 재조립한다.
func (a *resetArgs) toScriptArgs(use string) []string {
	known := knownFlags(use)
	out := []string{}
	addBool := func(wcliName, flag string, val bool) {
		if val && known[wcliName] {
			out = append(out, flag)
		}
	}
	addStr := func(wcliName, flag, val string) {
		if val != "" && known[wcliName] {
			out = append(out, flag, val)
		}
	}
	addBool("yes", "--yes", a.yes)
	addBool("dryRun", "--dry-run", a.dryRun)
	addBool("restart", "--restart", a.restart)
	addBool("skipConfig", "--skip-config", a.skipConfig)
	addBool("skipMemory", "--skip-memory", a.skipMemory)
	addBool("skipQdrant", "--skip-qdrant", a.skipQdrant)
	addStr("robot", "--robot", a.robot)
	addStr("qdrantURL", "--qdrant-url", a.qdrantURL)
	addStr("collection", "--collection", a.collection)
	addStr("container", "--container", a.container)
	addStr("memoryDir", "--memory-dir", a.memoryDir)
	addStr("configCache", "--config-cache", a.configCache)
	// 스크립트별 위치 인자(추가)는 그대로 뒤에 붙인다.
	out = append(out, a.extra...)
	return out
}

// execResetScript 지정 bash 스크립트를 스토어 클론 루트의 scripts/ 에서 찾아 실행한다.
func execResetScript(use string, args *resetArgs) error {
	name := resetScriptName(use)
	if name == "" {
		return fmt.Errorf("지원하지 않는 초기화 종류입니다: %s (state|memory|full)", use)
	}
	path := filepath.Join(launcher.GetProjectRoot(), "scripts", name)
	if !launcher.PathExists(path) {
		return fmt.Errorf("초기화 스크립트가 없습니다: %s — 스토어 클론 루트의 scripts/ 에 있어야 합니다", path)
	}

	cmdArgs := append([]string{path}, args.toScriptArgs(use)...)
	cmd := exec.Command("bash", cmdArgs...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

// ResetRobotCmd 로봇 상태/메모리/벡터 DB 초기화 bash 스크립트를 단일 CLI 입구로 노출한다.
func ResetRobotCmd() *wcli.Command {
	parent := &wcli.Command{
		Use:   "reset-robot",
		Short: "로봇 상태/메모리/벡터 DB 초기화 스크립트를 실행합니다.",
	}
	args := &resetArgs{}
	args.register(parent)

	resetSub := func(use, short string) *wcli.Command {
		return &wcli.Command{
			Use:   use,
			Short: short,
			Run: func(ctx *wcli.Context) error {
				args.extra = ctx.Args
				return execResetScript(use, args)
			},
		}
	}
	parent.AddCommand(
		resetSub("state", "호스트 SSH: config 캐시 + 로컬 메모리 삭제 (Qdrant 제외)"),
		resetSub("memory", "로봇 직접: 컨테이너 정지 + 메모리/시맨틱/콜드 + Qdrant 컬렉션 삭제"),
		resetSub("full", "호스트 SSH: config 캐시 + 메모리 + Qdrant 컬렉션 모두 초기화 (권장)"),
	)
	return parent
}
