package cmd

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// procCmdline 워크스페이스에서 실행 중인 프로세스 하나의 명령줄 스냅샷입니다.
type procCmdline struct {
	PID  int
	Argv []string
}

// neverKillExecutables 은 명령줄에 RoboClaw 노드 이름이 등장하더라도 절대
// 종료하면 안 되는 인터프리터/래퍼 목록입니다.
//
// 이전 구현은 `pkill -9 -f <노드명>` 으로 전체 명령줄 문자열을 매칭했기 때문에,
// 해당 문자열을 인자로 가진 셸(코딩 에이전트의 도구 셸, 편집기, 래퍼 스크립트)까지
// 함께 종료했습니다. 지금은 실행 파일 이름과 인자 basename 만 비교하고 이 목록을
// 먼저 제외합니다.
var neverKillExecutables = map[string]bool{
	"bash": true, "sh": true, "dash": true, "zsh": true, "fish": true, "ksh": true, "tcsh": true,
	"sudo": true, "su": true, "doas": true, "env": true, "timeout": true, "nice": true,
	"pi": true, "rclaw": true, "ollama": true, "zellij": true, "tmux": true, "screen": true,
}

// DefaultKillNodeExecutables 은 RoboClaw 런타임 노드 실행 파일 이름 목록입니다.
func DefaultKillNodeExecutables() []string {
	return []string{
		"robo_claw_agent_node",
		"robo_claw_channel_node",
		"robo_claw_core_node",
		"robo_claw_discovery_node",
		"robo_claw_grpc_node",
		"robo_claw_manipulator_node",
		"robo_claw_messenger_client_node",
		"object_detector_node",
		"sim_node",
	}
}

// DefaultKillLaunchFiles 은 정리 대상이 되는 RoboClaw launch 파일 목록입니다.
func DefaultKillLaunchFiles() []string {
	return []string{
		"robo_claw.launch.py",
		"robo_claw_sim.launch.py",
	}
}

// DefaultKillProcessPatterns 은 하위 호환을 위한 노드/런치 파일 이름 합집합입니다.
func DefaultKillProcessPatterns() []string {
	patterns := DefaultKillNodeExecutables()
	return append(patterns, DefaultKillLaunchFiles()...)
}

// parseProcCmdline 은 NUL 로 구분된 /proc/<pid>/cmdline 내용을 파싱합니다.
func parseProcCmdline(pid int, raw []byte) procCmdline {
	fields := strings.Split(string(raw), "\x00")
	argv := make([]string, 0, len(fields))
	for _, field := range fields {
		if field == "" {
			continue
		}
		argv = append(argv, field)
	}
	return procCmdline{PID: pid, Argv: argv}
}

// runsScriptInterpreter 는 스크립트를 실행하는 인터프리터인지 판별합니다.
func runsScriptInterpreter(execName string) bool {
	return execName == "ros2" || strings.HasPrefix(execName, "python")
}

// procMatchesKillTarget 은 프로세스가 RoboClaw 정리 대상인지 판별합니다.
//
// 매칭 규칙:
//  1. 실행 파일 이름이 노드 실행 파일과 정확히 같으면 대상입니다(네이티브 노드).
//  2. python*/ros2 인터프리터가 실행 중이고 인자 basename 이 노드 실행 파일 또는
//     RoboClaw launch 파일과 일치하면 대상입니다(entry point, ros2 launch).
//
// 셸/래퍼는 1단계에서 제외되므로 `bash -c "... robo_claw_sim.launch.py ..."` 처럼
// 문자열만 언급한 프로세스는 종료되지 않습니다.
func procMatchesKillTarget(p procCmdline, nodeNames, launchFiles []string) (bool, string) {
	if len(p.Argv) == 0 {
		return false, ""
	}
	execName := filepath.Base(p.Argv[0])
	if execName == "" || execName == "." || execName == "/" {
		return false, ""
	}
	if neverKillExecutables[execName] {
		return false, ""
	}
	if containsName(nodeNames, execName) {
		return true, "노드 실행 파일"
	}
	if !runsScriptInterpreter(execName) {
		return false, ""
	}
	hasNode := false
	hasLaunch := false
	for _, arg := range p.Argv[1:] {
		base := filepath.Base(arg)
		if containsName(nodeNames, base) {
			hasNode = true
		}
		if containsName(launchFiles, base) {
			hasLaunch = true
		}
	}
	switch {
	case hasNode:
		return true, "노드 entry point"
	case hasLaunch:
		return true, "launch 프로세스"
	default:
		return false, ""
	}
}

// ancestorSet 은 자기 자신과 조상 프로세스 PID 집합을 만듭니다.
func ancestorSet(selfPID int, parentOf func(int) (int, bool)) map[int]bool {
	excluded := map[int]bool{selfPID: true}
	pid := selfPID
	for depth := 0; depth < 64; depth++ {
		parent, ok := parentOf(pid)
		if !ok || parent <= 1 || excluded[parent] {
			break
		}
		excluded[parent] = true
		pid = parent
	}
	return excluded
}

// selectKillTargets 는 대상 프로세스 중 자기 자신과 조상 프로세스를 제외합니다.
func selectKillTargets(procs []procCmdline, selfPID int, parentOf func(int) (int, bool), nodeNames, launchFiles []string) []procCmdline {
	excluded := ancestorSet(selfPID, parentOf)

	var targets []procCmdline
	for _, p := range procs {
		if excluded[p.PID] {
			continue
		}
		if matched, _ := procMatchesKillTarget(p, nodeNames, launchFiles); matched {
			targets = append(targets, p)
		}
	}
	return targets
}

// collectProcessCmdlines 는 procRoot(예: /proc) 아래 프로세스들의 명령줄을 수집합니다.
func collectProcessCmdlines(procRoot string) []procCmdline {
	entries, err := os.ReadDir(procRoot)
	if err != nil {
		return nil
	}
	procs := make([]procCmdline, 0, len(entries))
	for _, entry := range entries {
		pid, convErr := strconv.Atoi(entry.Name())
		if convErr != nil || pid <= 1 {
			continue
		}
		raw, readErr := os.ReadFile(filepath.Join(procRoot, entry.Name(), "cmdline"))
		if readErr != nil || len(raw) == 0 {
			continue
		}
		procs = append(procs, parseProcCmdline(pid, raw))
	}
	return procs
}

// procStat 은 /proc/<pid>/stat 에서 필요한 필드만 추린 값입니다.
type procStat struct {
	State   string
	PPid    int
	Pgrp    int
	Session int
}

// readProcStat 은 /proc/<pid>/stat 을 파싱합니다.
func readProcStat(procRoot string, pid int) (procStat, bool) {
	raw, err := os.ReadFile(filepath.Join(procRoot, strconv.Itoa(pid), "stat"))
	if err != nil {
		return procStat{}, false
	}
	// comm 필드에 공백/괄호가 들어갈 수 있으므로 마지막 ')' 뒤부터 파싱합니다.
	text := string(raw)
	idx := strings.LastIndex(text, ")")
	if idx < 0 || idx+1 >= len(text) {
		return procStat{}, false
	}
	fields := strings.Fields(text[idx+1:])
	if len(fields) < 4 {
		return procStat{}, false
	}
	ppid, ppidErr := strconv.Atoi(fields[1])
	pgrp, pgrpErr := strconv.Atoi(fields[2])
	session, sessionErr := strconv.Atoi(fields[3])
	if ppidErr != nil || pgrpErr != nil || sessionErr != nil {
		return procStat{}, false
	}
	return procStat{State: fields[0], PPid: ppid, Pgrp: pgrp, Session: session}, true
}

// readParentPID 는 /proc/<pid>/stat 에서 PPid 를 읽습니다.
func readParentPID(procRoot string, pid int) (int, bool) {
	st, ok := readProcStat(procRoot, pid)
	if !ok {
		return 0, false
	}
	return st.PPid, true
}

func containsName(list []string, name string) bool {
	for _, item := range list {
		if item == name {
			return true
		}
	}
	return false
}

// selectPortKillTargets 는 포트를 점유한 PID 중 RoboClaw 프로세스만 정리 대상으로
// 고릅니다. 같은 포트를 다른 도구(예: pi-bot-connect 의 50052)가 점유한 경우에는
// 절대 종료하지 않고 skipped 로 돌려줍니다.
func selectPortKillTargets(pids []int, byPID map[int]procCmdline, protected map[int]bool, nodeNames, launchFiles []string) (targets, skipped []procCmdline) {
	for _, pid := range pids {
		if protected[pid] {
			if p, ok := byPID[pid]; ok {
				skipped = append(skipped, p)
			}
			continue
		}
		p, ok := byPID[pid]
		if !ok {
			continue
		}
		if matched, _ := procMatchesKillTarget(p, nodeNames, launchFiles); !matched {
			skipped = append(skipped, p)
			continue
		}
		targets = append(targets, p)
	}
	return targets, skipped
}
