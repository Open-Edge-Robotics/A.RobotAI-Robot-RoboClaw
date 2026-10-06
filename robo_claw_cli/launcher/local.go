package launcher

import (
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
)

// RunLocalMode 네이티브 ROS 2 Launch로 RoboClaw 시스템을 기동합니다.
func RunLocalMode(ctx *wcli.Context, lctx *LaunchContext) error {
	mergedEnv := buildMergedEnv(ctx, lctx)
	launchArgs := buildLocalLaunchArgs(lctx)

	argsToExec := append([]string{"launch", "robo_claw_bringup", lctx.LaunchTarget}, launchArgs...)
	logging.Info("실행 명령어: ros2 %s", strings.Join(argsToExec, " "))
	rich.Println("[green]▶[/green] 실행 명령어: [bold]ros2 %s[/bold]", strings.Join(argsToExec, " "))

	cmdLaunch := exec.Command("ros2", argsToExec...)
	cmdLaunch.Env = mergedEnv
	cmdLaunch.Stdout = os.Stdout
	cmdLaunch.Stderr = os.Stderr
	cmdLaunch.Stdin = os.Stdin

	logging.Info("RoboClaw 시스템 기동 시작...")
	rich.Println("[cyan]🚀 RoboClaw 시스템 기동 중...[/cyan]")

	// 시그널 채널 생성 및 등록
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	if err := cmdLaunch.Start(); err != nil {
		logging.Error("RoboClaw 프로세스 시작 실패: %v", err)
		return fmt.Errorf("RoboClaw 런치 시작 에러: %w", err)
	}

	logging.Info("RoboClaw 프로세스 기동 성공 (PID: %d)", cmdLaunch.Process.Pid)

	// 프로세스 종료 대기 채널
	done := make(chan error, 1)
	go func() {
		done <- cmdLaunch.Wait()
	}()

	select {
	case sig := <-sigChan:
		logging.Warn("종료 시그널 감지 (%v). RoboClaw 시스템 종료 프로세스를 시작합니다.", sig)
		rich.Println("\n[yellow]🛑 종료 시그널 감지 (%v). RoboClaw 시스템 종료 중...[/yellow]", sig)
		if cmdLaunch.Process != nil {
			_ = cmdLaunch.Process.Signal(sig)
		}
		// 차일드 프로세스가 종료될 때까지 대기
		err := <-done
		logging.Info("RoboClaw 프로세스가 정상적으로 종료되었습니다.")
		return err
	case err := <-done:
		if err != nil {
			logging.Error("RoboClaw 프로세스 실행 중 에러 종료: %v", err)
			return fmt.Errorf("RoboClaw 런치 실행 에러: %w", err)
		}
	}
	logging.Info("RoboClaw 프로세스가 오류 없이 안전하게 종료되었습니다.")
	return nil
}

// BuildLocalLaunchCommand returns the command that RunLocalMode would execute.
// It is used by dry-run tooling and intentionally does not start ROS.
func BuildLocalLaunchCommand(lctx *LaunchContext) []string {
	return append([]string{"ros2", "launch", "robo_claw_bringup", lctx.LaunchTarget}, buildLocalLaunchArgs(lctx)...)
}

// buildLocalLaunchArgs 로컬 모드에서 사용할 ROS 2 런치 아규먼트를 빌드합니다.
func buildLocalLaunchArgs(lctx *LaunchContext) []string {
	args := append([]string{}, lctx.CommonLaunchArgs...)

	// 캐시된 마크다운/JSON 파일 절대 경로 매핑
	for filename, paramName := range map[string]string{
		"ROBOT.md":           "robot_soul_file",
		"SKILLS.md":          "skills_guide_file",
		"TROUBLESHOOTING.md": "troubleshooting_guide_file",
		"ROBOT_LIMITS.json":  "robot_limits_file",
	} {
		if fp := filepath.Join(lctx.CacheDir, filename); PathExists(fp) {
			args = append(args, fmt.Sprintf("%s:=%s", paramName, fp))
		}
	}

	// 커스텀 시스템 프롬프트
	if f := ResolveEnvWithFallback(lctx.EnvMap, "RC_SYSTEM_PROMPT_FILE"); f != "" && PathExists(f) {
		args = append(args, fmt.Sprintf("system_prompt_file:=%s", f))
	}

	// 에이전트 작업 공간 디렉토리
	agentWorkspaceDir := ResolveEnvWithFallback(lctx.EnvMap, "RC_AGENT_WORKSPACE_DIR")
	if agentWorkspaceDir == "" {
		agentWorkspaceDir = filepath.Join(lctx.ProjectRoot, "agent_workspace")
	}
	_ = os.MkdirAll(agentWorkspaceDir, 0755)
	args = append(args, fmt.Sprintf("agent_workspace_dir:=%s", agentWorkspaceDir))

	// 메모리/로컬 벡터 저장 경로 — 설정 시에만 오버라이드 (미설정 시 ~/.robo_claw 기본)
	if memoryDir := ResolveEnvWithFallback(lctx.EnvMap, "RC_MEMORY_DIR"); memoryDir != "" {
		_ = os.MkdirAll(memoryDir, 0755)
		args = append(args, fmt.Sprintf("memory_path:=%s", filepath.Join(memoryDir, "memory.json")))
	}

	// Butler Scripts 디렉토리 — butler 로봇은 이 인자가 반드시 필요하다.
	if dir := ResolveButlerScriptsDir(lctx.EnvMap, lctx.ProjectRoot); dir != "" && PathExists(dir) {
		args = append(args, fmt.Sprintf("butler_script_dir:=%s", dir))
	}

	if lctx.RobotName != "" {
		args = append(args, fmt.Sprintf("robot_config:=%s", lctx.RobotName))
	}
	if lctx.Sim {
		args = append(args, BuildSimArgs(lctx)...)
	}

	return args
}

// buildMergedEnv 시스템 환경변수, .env 값, 설정값을 병합하여 최종 환경변수 슬라이스를 반환합니다.
func buildMergedEnv(ctx *wcli.Context, lctx *LaunchContext) []string {
	env := append(os.Environ(), lctx.EnvSlice...)

	// RC_DEBUG 환경변수 또는 --debug 플래그가 활성화된 경우 ROS 2 디버그 로깅 활성화
	if lctx.Debug || isEnvTrue(lctx.EnvMap, "RC_DEBUG") {
		env = append(env, "RCUTILS_LOGGING_MIN_SEVERITY_LEVEL=DEBUG")
		logging.Debug("ROS 2 디버그 로깅 활성화 (RCUTILS_LOGGING_MIN_SEVERITY_LEVEL=DEBUG)")
	}

	// ROS_DOMAIN_ID 주입 (우선순위: EnvMap -> 호스트 환경변수 -> activeConfig)
	hasRosDomainID := false
	for _, e := range env {
		if strings.HasPrefix(strings.ToUpper(e), "ROS_DOMAIN_ID=") {
			hasRosDomainID = true
			break
		}
	}
	if !hasRosDomainID && lctx.EnvMap["ROS_DOMAIN_ID"] == "" {
		env = append(env, fmt.Sprintf("ROS_DOMAIN_ID=%d", lctx.ActiveConfig.RosDomainId))
		logging.Debug("ROS_DOMAIN_ID 주입: %d", lctx.ActiveConfig.RosDomainId)
	}

	env = applyVenv(ctx, lctx.ProjectRoot, env)

	// 캐시된 config.json이 구버전 DTO로 생성되어 LangSmith 값을 잃었을 수 있다.
	// 명시적으로 생성된 .env가 있으면 그 값을 보존하고, JSON 값은 값이 있을 때만
	// 보완한다. 이렇게 해야 서버 설정 true가 오래된 false zero value로 덮이지 않는다.
	if _, configured := lctx.EnvMap["LANGSMITH_TRACING"]; !configured {
		if lctx.ActiveConfig.LangsmithTracing {
			env = append(env, "LANGSMITH_TRACING=true")
		}
	}
	// The .env artifact carries the unredacted secret. The config JSON endpoint may
	// return a redaction sentinel ("********"), so never let it override a usable
	// artifact value or become the effective key itself.
	artifactAPIKey := strings.TrimSpace(lctx.EnvMap["LANGSMITH_API_KEY"])
	configAPIKey := strings.TrimSpace(lctx.ActiveConfig.LangsmithApiKey)
	if (artifactAPIKey == "" || isRedactedSecret(artifactAPIKey)) &&
		configAPIKey != "" && !isRedactedSecret(configAPIKey) {
		env = append(env, fmt.Sprintf("LANGSMITH_API_KEY=%s", configAPIKey))
	}
	if lctx.ActiveConfig.LangsmithProject != "" {
		env = append(env, fmt.Sprintf("LANGSMITH_PROJECT=%s", lctx.ActiveConfig.LangsmithProject))
	}
	if lctx.ActiveConfig.LangsmithEndpoint != "" {
		env = append(env, fmt.Sprintf("LANGSMITH_ENDPOINT=%s", lctx.ActiveConfig.LangsmithEndpoint))
	}
	if lctx.ActiveConfig.LangsmithWorkspaceId != "" {
		env = append(env, fmt.Sprintf("LANGSMITH_WORKSPACE_ID=%s", lctx.ActiveConfig.LangsmithWorkspaceId))
	}
	logging.Debug("LangSmith 트레이싱 환경변수 주입 완료 (tracing=%t, project=%s)", lctx.ActiveConfig.LangsmithTracing, lctx.ActiveConfig.LangsmithProject)

	if lctx.Sim {
		env = applySimEnv(ctx, lctx, env)
	}

	return env
}

func isRedactedSecret(value string) bool {
	value = strings.TrimSpace(value)
	return value != "" && strings.Trim(value, "*") == ""
}

// applyVenv .venv 가상환경의 PATH 및 PYTHONPATH를 환경변수에 주입합니다.
func applyVenv(ctx *wcli.Context, projectRoot string, env []string) []string {
	venvBin := filepath.Join(projectRoot, ".venv", "bin")
	if !PathExists(venvBin) {
		logging.Debug("가상환경 폴더가 존재하지 않음: %s", venvBin)
		return env
	}

	env = prependEnvValue(env, "PATH", venvBin)

	if sitePkg := ResolveVenvSitePackages(projectRoot); sitePkg != "" {
		env = prependEnvValue(env, "PYTHONPATH", sitePkg)
	}

	logging.Debug("Python 가상환경(.venv) 적용 완료")
	rich.Println("[green]✔[/green] Python 가상환경(.venv) 적용 완료")
	return env
}

// prependEnvValue 환경변수 슬라이스에서 key에 해당하는 항목의 값 앞에 value를 추가합니다.
// 해당 key가 없으면 새 항목으로 추가합니다. 원본 key 대소문자를 보존합니다.
func prependEnvValue(env []string, key, value string) []string {
	upperKey := strings.ToUpper(key) + "="
	for i, e := range env {
		if strings.HasPrefix(strings.ToUpper(e), upperKey) {
			parts := strings.SplitN(e, "=", 2)
			env[i] = fmt.Sprintf("%s=%s:%s", parts[0], value, parts[1])
			return env
		}
	}
	return append(env, fmt.Sprintf("%s=%s", key, value))
}

// applySimEnv 시뮬레이션 모드에 필요한 환경변수를 추가합니다.
func applySimEnv(ctx *wcli.Context, lctx *LaunchContext, env []string) []string {
	env = append(env,
		"RCUTILS_COLORIZED_OUTPUT=1",
		"PYTHONWARNINGS=ignore:setup.py install is deprecated",
	)

	if lctx.RosDistro == "jazzy" {
		env = applyJazzySimEnv(ctx, lctx.ProjectRoot, env)
	} else {
		env = applyHumbleSimEnv(ctx, env)
	}

	logging.Debug("시뮬레이션 최적화 환경변수 설정 완료 (Distro: %s)", lctx.RosDistro)
	rich.Println("[green]✔[/green] 시뮬레이션 최적화 환경변수 설정 완료 (Distro: %s)", lctx.RosDistro)
	return env
}

// applyJazzySimEnv Jazzy(Harmonic/Gazebo) 시뮬레이션 환경변수를 추가합니다.
func applyJazzySimEnv(ctx *wcli.Context, projectRoot string, env []string) []string {
	gzModelPath := filepath.Join(projectRoot, "install", "robo_claw_bringup", "share", "robo_claw_bringup", "models")
	if cur := os.Getenv("GZ_SIM_RESOURCE_PATH"); cur != "" {
		env = append(env, fmt.Sprintf("GZ_SIM_RESOURCE_PATH=%s:%s", cur, gzModelPath))
	} else {
		env = append(env, fmt.Sprintf("GZ_SIM_RESOURCE_PATH=%s", gzModelPath))
	}
	env = append(env, "QT_QPA_PLATFORM=xcb", "GZ_RENDERING_BACKEND=ogre2")

	// GUI 캐시 정리
	homeDir, _ := os.UserHomeDir()
	gzCacheDir := filepath.Join(homeDir, ".gz", "sim")
	_ = os.RemoveAll(gzCacheDir)
	logging.Debug("Gazebo GUI 캐시 정리 완료: %s", gzCacheDir)
	rich.Println("[cyan]ℹ[/cyan] Gazebo GUI 캐시 정리 완료 (%s)", gzCacheDir)

	return env
}

// applyHumbleSimEnv Humble(Gazebo Classic) 시뮬레이션 환경변수를 추가합니다.
func applyHumbleSimEnv(ctx *wcli.Context, env []string) []string {
	env = append(env,
		"RMW_IMPLEMENTATION=rmw_cyclonedds_cpp",
		"CYCLONEDDS_URI=<CycloneDDS><Domain><General><MaxMessageSize>65535B</MaxMessageSize><FragmentSize>1300B</FragmentSize></General></Domain></CycloneDDS>",
		"LIBGL_ALWAYS_SOFTWARE=1",
		"MESA_GL_VERSION_OVERRIDE=3.3",
		"MESA_GLSL_VERSION_OVERRIDE=330",
		"OGRE_RTT_MODE=Copy",
	)
	if os.Getenv("ROS_DOMAIN_ID") == "" {
		env = append(env, "ROS_DOMAIN_ID=30")
	}
	if os.Getenv("TURTLEBOT3_MODEL") == "" {
		env = append(env, "TURTLEBOT3_MODEL=waffle")
	}

	// Gazebo Classic 기본 경로 보정
	gazeboResourcePath := os.Getenv("GAZEBO_RESOURCE_PATH")
	if !strings.Contains(gazeboResourcePath, "/usr/share/gazebo-11") {
		if gazeboResourcePath != "" {
			env = append(env, fmt.Sprintf("GAZEBO_RESOURCE_PATH=/usr/share/gazebo-11:%s", gazeboResourcePath))
		} else {
			env = append(env, "GAZEBO_RESOURCE_PATH=/usr/share/gazebo-11")
		}
	}

	logging.Debug("Humble 시뮬레이션 환경 변수 적용 (RMW_IMPLEMENTATION=rmw_cyclonedds_cpp)")
	return env
}
