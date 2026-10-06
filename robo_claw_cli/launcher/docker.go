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

// RunDockerMode Docker 컨테이너 기반으로 RoboClaw 시스템을 기동합니다.
func RunDockerMode(ctx *wcli.Context, lctx *LaunchContext) error {
	logging.Info("Docker 모드 기동 준비 시작...")
	rich.Println("[cyan]🐳 Docker 컨테이너 기반으로 기동합니다.[/cyan]")

	// 이미지명 결정: 명시적 --image 값이 없으면 기본 이미지를 사용한다.
	const defaultImageName = "lgecloudroboticstask/robo-claw"
	imageName := strings.TrimSpace(lctx.ImageName)
	if imageName == "" {
		imageName = defaultImageName
	}
	tag := lctx.ImageTag
	if tag == "" {
		tag = "latest"
	}
	image := fmt.Sprintf("%s:%s", imageName, tag)

	// 실행 직전 이미지 pull (옛 로컬 이미지가 재사용되는 문제 방지, --pull 지정 시)
	if lctx.Pull {
		rich.Println("[cyan]⬇  이미지 pull: %s[/cyan]", image)
		pullCmd := exec.Command("docker", "pull", image)
		pullCmd.Stdout = os.Stdout
		pullCmd.Stderr = os.Stderr
		if err := pullCmd.Run(); err != nil {
			return fmt.Errorf("docker pull 실패 (%s): %w", image, err)
		}
	}

	// X11 포워딩 설정
	logging.Debug("X11 포워딩 권한 설정 (xhost +local:docker)")
	_ = exec.Command("xhost", "+local:docker").Run()
	defer func() {
		logging.Debug("X11 포워딩 권한 해제 (xhost -local:docker)")
		_ = exec.Command("xhost", "-local:docker").Run()
	}()

	// ROS_DOMAIN_ID 결정 (우선순위: EnvMap -> 호스트 환경변수 -> activeConfig)
	rosDomainID := ResolveEnvWithFallback(lctx.EnvMap, "ROS_DOMAIN_ID")
	if rosDomainID == "" {
		rosDomainID = fmt.Sprintf("%d", lctx.ActiveConfig.RosDomainId)
	}
	logging.Debug("컨테이너 내 주입할 ROS_DOMAIN_ID: %s", rosDomainID)

	dockerRunArgs := []string{
		"run", "-it", "--rm",
		"--name", "robo_claw_container",
		"--network", "host",
		"--privileged",
		"-e", fmt.Sprintf("DISPLAY=%s", os.Getenv("DISPLAY")),
		// 타임존: 호스트의 /etc/localtime을 마운트하고 TZ 환경변수를 주입해
		// 컨테이너 내 시간을 호스트와 일치시킨다. RC_TZ로 오버라이드 가능.
		"-e", fmt.Sprintf("TZ=%s", func() string {
			if tz := ResolveEnvWithFallback(lctx.EnvMap, "RC_TZ"); tz != "" {
				return tz
			}
			return "Asia/Seoul"
		}()),
		"-v", "/tmp/.X11-unix:/tmp/.X11-unix:rw",
		// /dev 전체 마운트 대신 로봇 제어에 필요한 장치만 선택적으로 마운트.
		// 시리얼 통신(ttyUSB*, ttyACM*), 입력 장치, 비전센서 컨텍스트 유지.
		"--device", "/dev/ttyUSB0",
		"--device", "/dev/ttyACM0",
		"-v", "/dev/input:/dev/input:ro",
		"-v", "/sys/class/power_supply:/sys/class/power_supply:ro",
		"-v", "/etc/localtime:/etc/localtime:ro",
		"-e", fmt.Sprintf("ROS_DOMAIN_ID=%s", rosDomainID),
	}
	dockerRunArgs = append(dockerRunArgs, buildDockerVolumes(lctx)...)
	dockerRunArgs = append(dockerRunArgs, "--env-file", lctx.EnvFilePath)
	dockerRunArgs = append(dockerRunArgs, image)
	dockerRunArgs = append(dockerRunArgs, buildContainerCmd(lctx)...)

	logging.Info("실행 명령어: docker %s", strings.Join(dockerRunArgs[1:], " "))
	rich.Println("[green]▶[/green] 실행 명령어: [bold]docker %s[/bold]", strings.Join(dockerRunArgs[1:], " "))

	cmdDocker := exec.Command("docker", dockerRunArgs...)
	cmdDocker.Stdout = os.Stdout
	cmdDocker.Stderr = os.Stderr
	cmdDocker.Stdin = os.Stdin

	logging.Info("Docker 컨테이너 기동 시작...")
	rich.Println("[cyan]🚀 RoboClaw Docker 컨테이너 기동 중...[/cyan]")

	// 시그널 채널 생성 및 등록
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, os.Interrupt, syscall.SIGTERM)

	if err := cmdDocker.Start(); err != nil {
		logging.Error("Docker 컨테이너 시작 실패: %v", err)
		return fmt.Errorf("Docker 컨테이너 시작 에러: %w", err)
	}

	logging.Info("Docker 컨테이너 기동 성공 (PID: %d)", cmdDocker.Process.Pid)

	// 프로세스 종료 대기 채널
	done := make(chan error, 1)
	go func() {
		done <- cmdDocker.Wait()
	}()

	select {
	case sig := <-sigChan:
		logging.Warn("종료 시그널 감지 (%v). Docker 컨테이너 종료 프로세스를 시작합니다.", sig)
		rich.Println("\n[yellow]🛑 종료 시그널 감지 (%v). Docker 컨테이너 종료 중...[/yellow]", sig)
		if cmdDocker.Process != nil {
			_ = cmdDocker.Process.Signal(sig)
		}
		// 차일드 프로세스가 종료될 때까지 대기
		err := <-done
		logging.Info("Docker 컨테이너가 정상적으로 종료되었습니다.")
		return err
	case err := <-done:
		if err != nil {
			logging.Error("Docker 컨테이너 실행 중 에러 종료: %v", err)
			return fmt.Errorf("Docker 컨테이너 실행 에러: %w", err)
		}
	}
	logging.Info("Docker 컨테이너가 오류 없이 안전하게 종료되었습니다.")
	return nil
}

// buildDockerVolumes 마운트할 볼륨 인수 슬라이스를 구성합니다.
func buildDockerVolumes(lctx *LaunchContext) []string {
	var vols []string
	mountedTargets := make(map[string]bool)
	addVolume := func(source, target, mode string) {
		if mountedTargets[target] {
			return
		}
		mountedTargets[target] = true
		vols = append(vols, "-v", fmt.Sprintf("%s:%s:%s", source, target, mode))
	}

	pr := lctx.ProjectRoot
	cd := lctx.CacheDir

	// 1. Models 디렉토리
	if modelsDir := filepath.Join(pr, "models"); PathExists(modelsDir) {
		addVolume(modelsDir, "/ros2_ws/models", "ro")
		addVolume(modelsDir, modelsDir, "ro")
	}
	if modelPath := ResolveEnvWithFallback(lctx.EnvMap, "RC_VISION_MODEL_PATH"); filepath.IsAbs(modelPath) {
		if modelDir := filepath.Dir(modelPath); PathExists(modelDir) {
			addVolume(modelDir, modelDir, "ro")
		}
	}

	// 2. Config 디렉토리
	configDir := filepath.Join(pr, "src", "robo_claw_bringup", "config")
	if PathExists(configDir) {
		addVolume(configDir, "/ros2_ws/install/robo_claw_bringup/share/robo_claw_bringup/config", "ro")
	}

	// 2-1. Launch 디렉토리 — 개발 중 런치 파일 수정을 이미지 재빌드 없이
	// 컨테이너에 즉시 반영한다. 주의: 이미지 태그와 호스트 체크아웃 버전이
	// 다르면 호스트 launch가 이미지 launch를 덮어쓰므로 재현성이 필요한 검증에서는
	// 호스트와 이미지 태그를 동일한 커밋으로 맞춘다.
	launchDir := filepath.Join(pr, "src", "robo_claw_bringup", "launch")
	if PathExists(launchDir) {
		addVolume(launchDir, "/ros2_ws/install/robo_claw_bringup/share/robo_claw_bringup/launch", "ro")
	}

	// 2-2. 로컬 Python 패키지 소스 마운트 (컨테이너 내 코드 즉시 반영/디버깅용)
	for _, pyVer := range []string{"python3.10", "python3.12"} {
		for _, libPath := range []string{"local/lib/" + pyVer + "/dist-packages", "lib/" + pyVer + "/site-packages"} {
			channelSrcDir := filepath.Join(pr, "src", "robo_claw_channel", "robo_claw_channel")
			// 생성 proto가 없는 소스 마운트는 이미지에 설치된 정상 패키지를
			// 가려 ImportError를 유발하므로, 두 stub이 모두 있을 때만 덮어쓴다.
			if PathExists(channelSrcDir) &&
				PathExists(filepath.Join(channelSrcDir, "messenger_pb2.py")) &&
				PathExists(filepath.Join(channelSrcDir, "messenger_pb2_grpc.py")) {
				addVolume(channelSrcDir, fmt.Sprintf("/ros2_ws/install/robo_claw_channel/%s/robo_claw_channel", libPath), "ro")
			}
			agentSrcDir := filepath.Join(pr, "src", "robo_claw_agent", "robo_claw_agent")
			if PathExists(agentSrcDir) {
				addVolume(agentSrcDir, fmt.Sprintf("/ros2_ws/install/robo_claw_agent/%s/robo_claw_agent", libPath), "ro")
			}
		}
	}

	// 3. Butler Scripts 디렉토리 — butler 로봇은 이 경로가 마운트되어야 동작한다.
	if dir := ResolveButlerScriptsDir(lctx.EnvMap, pr); dir != "" {
		if PathExists(dir) {
			addVolume(dir, dir, "rw")
		} else {
			fmt.Fprintf(os.Stderr, "경고: Butler 스크립트 경로가 존재하지 않아 butler 동작이 불가능합니다: %s\n", dir)
		}
	} else {
		fmt.Fprintln(os.Stderr, "경고: Butler 스크립트 경로를 결정하지 못했습니다. butler 로봇은 동작하지 않습니다.")
	}

	// 4. Butler Source 디렉토리
	if dir := ResolveButlerSourceDir(lctx.EnvMap); dir != "" && PathExists(dir) {
		addVolume(dir, dir, "rw")
	}

	// 5. 에이전트 작업 공간 디렉토리
	agentWorkspaceDir := ResolveEnvWithFallback(lctx.EnvMap, "RC_AGENT_WORKSPACE_DIR")
	if agentWorkspaceDir == "" {
		agentWorkspaceDir = filepath.Join(pr, "agent_workspace")
	}
	_ = os.MkdirAll(agentWorkspaceDir, 0755)
	addVolume(agentWorkspaceDir, "/ros2_ws/agent_workspace", "rw")

	// 5-1. 메모리/로컬 벡터 저장(RAG 미러) 디렉토리 — 컨테이너 재시작 후에도 유지
	memoryDir := ResolveEnvWithFallback(lctx.EnvMap, "RC_MEMORY_DIR")
	if memoryDir == "" {
		memoryDir = filepath.Join(pr, "agent_workspace", "memory")
	}
	_ = os.MkdirAll(memoryDir, 0755)
	addVolume(memoryDir, "/ros2_ws/memory", "rw")

	// 6. 캐시된 마크다운 / JSON 설정 파일 개별 마운트
	for cacheFile, targetName := range map[string]string{
		"ROBOT.md":           "ROBOT.md",
		"SKILLS.md":          "SKILLS.md",
		"TROUBLESHOOTING.md": "TROUBLESHOOTING.md",
		"ROBOT_LIMITS.json":  "ROBOT_LIMITS.json",
	} {
		if fp := filepath.Join(cd, cacheFile); PathExists(fp) {
			addVolume(fp, fmt.Sprintf("/ros2_ws/config/%s", targetName), "ro")
		}
	}

	// 7. 커스텀 시스템 프롬프트 파일
	if f := ResolveEnvWithFallback(lctx.EnvMap, "RC_SYSTEM_PROMPT_FILE"); f != "" && PathExists(f) {
		addVolume(f, "/ros2_ws/config/system_prompt.md", "ro")
	}

	return vols
}

// buildContainerCmd 컨테이너 내에서 실행할 ROS 2 Launch 명령어를 구성합니다.
func buildContainerCmd(lctx *LaunchContext) []string {
	baseCmd := []string{"ros2", "launch", "robo_claw_bringup", lctx.LaunchTarget}
	baseCmd = append(baseCmd, lctx.CommonLaunchArgs...)
	baseCmd = append(baseCmd,
		"robot_soul_file:=/ros2_ws/config/ROBOT.md",
		"skills_guide_file:=/ros2_ws/config/SKILLS.md",
		"robot_limits_file:=/ros2_ws/config/ROBOT_LIMITS.json",
		"troubleshooting_guide_file:=/ros2_ws/config/TROUBLESHOOTING.md",
		"agent_workspace_dir:=/ros2_ws/agent_workspace",
		"memory_path:=/ros2_ws/memory/memory.json",
	)

	if f := ResolveEnvWithFallback(lctx.EnvMap, "RC_SYSTEM_PROMPT_FILE"); f != "" && PathExists(f) {
		baseCmd = append(baseCmd, "system_prompt_file:=/ros2_ws/config/system_prompt.md")
	}
	// butler_script_dir launch 인자 — butler 로봇은 이 인자가 반드시 필요하다.
	if dir := ResolveButlerScriptsDir(lctx.EnvMap, lctx.ProjectRoot); dir != "" {
		baseCmd = append(baseCmd, fmt.Sprintf("butler_script_dir:=%s", dir))
	}

	if lctx.Sim {
		baseCmd = append(baseCmd, BuildSimArgs(lctx)...)
	}
	if lctx.RobotName != "" {
		baseCmd = append(baseCmd, fmt.Sprintf("robot_config:=%s", lctx.RobotName))
	}

	// butlerSourceDir가 마운트된 경우 소싱 래핑 적용
	butlerSourceDir := ResolveEnvWithFallback(lctx.EnvMap, "RC_BUTLER_SOURCE_DIR")
	if butlerSourceDir == "" {
		return baseCmd
	}
	wrapCmd := []string{
		"bash", "-c",
		fmt.Sprintf(
			"if [ -f /opt/ros/humble/setup.bash ]; then source /opt/ros/humble/setup.bash; fi && if [ -f /ros2_ws/install/setup.bash ]; then source /ros2_ws/install/setup.bash; fi && if [ -f %s/install/setup.bash ]; then source %s/install/setup.bash; fi && exec \"$0\" \"$@\"",
			butlerSourceDir, butlerSourceDir,
		),
	}
	return append(wrapCmd, baseCmd...)
}

// BuildDockerLaunchCommand returns the launch command executed inside Docker.
func BuildDockerLaunchCommand(lctx *LaunchContext) []string {
	return buildContainerCmd(lctx)
}
