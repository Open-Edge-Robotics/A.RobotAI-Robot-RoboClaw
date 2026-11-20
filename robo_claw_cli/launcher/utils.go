package launcher

import (
	"bufio"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
)

// IsPortAvailable 포트가 사용 가능한지 체크합니다.
func IsPortAvailable(port string) bool {
	ln, err := net.Listen("tcp", ":"+port)
	if err != nil {
		return false
	}
	_ = ln.Close()
	return true
}

// isEnvTrue 환경변수 값이 "true", "1", "yes", "on" (대소문자 무관)인지 확인합니다.
func isEnvTrue(envMap map[string]string, key string) bool {
	v := strings.ToLower(strings.TrimSpace(envMap[key]))
	return v == "true" || v == "1" || v == "yes" || v == "on"
}

// isHostEnvTrue 호스트 프로세스 환경변수가 참 값을 갖는지 확인합니다.
func isHostEnvTrue(key string) bool {
	return isEnvTrue(map[string]string{key: os.Getenv(key)}, key)
}

// FindAvailablePort 지정한 포트번호부터 증가하며 비어있는 첫 포트를 반환합니다.
func FindAvailablePort(startPort string) string {
	var portNum int
	n, err := fmt.Sscanf(startPort, "%d", &portNum)
	if err != nil || n != 1 {
		portNum = 8080 // 파싱 에러 시 기본값
	}

	for i := 0; i < 20; i++ {
		if p := fmt.Sprintf("%d", portNum+i); IsPortAvailable(p) {
			return p
		}
	}
	return startPort // 전체 실패 시 폴백
}

// readConfigRevision 캐시 디렉터리의 manifest.json에서 config revision을 읽습니다.
// v2 manifest를 지원하지 않는 서버에서는 캐시에 manifest.json이 없으므로 빈 문자열을 반환합니다.
func readConfigRevision(cacheDir string) string {
	manifestPath := filepath.Join(cacheDir, "manifest.json")
	data, err := os.ReadFile(manifestPath)
	if err != nil {
		return ""
	}
	var manifest struct {
		ConfigRevision string `json:"config_revision"`
	}
	if err := json.Unmarshal(data, &manifest); err != nil {
		return ""
	}
	return strings.TrimSpace(manifest.ConfigRevision)
}

// ResolveVenvSitePackages 가상환경 site-packages 경로를 찾아 반환합니다.
func ResolveVenvSitePackages(projectRoot string) string {
	pattern := filepath.Join(projectRoot, ".venv", "lib", "python3.*", "site-packages")
	matches, _ := filepath.Glob(pattern)
	if len(matches) > 0 {
		return matches[0]
	}
	return ""
}

// ParseEnvFile .env 환경설정 파일을 읽고 환경변수 리스트와 맵으로 반환합니다.
func ParseEnvFile(filePath string) ([]string, map[string]string, error) {
	file, err := os.Open(filePath)
	if err != nil {
		return nil, nil, err
	}
	defer file.Close()

	var envSlice []string
	envMap := make(map[string]string)
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])

		// 따옴표 제거
		if len(val) >= 2 {
			if (val[0] == '"' && val[len(val)-1] == '"') || (val[0] == '\'' && val[len(val)-1] == '\'') {
				val = val[1 : len(val)-1]
			}
		}

		envSlice = append(envSlice, fmt.Sprintf("%s=%s", key, val))
		envMap[key] = val
	}
	return envSlice, envMap, scanner.Err()
}

// GetProjectRoot 현재 워크스페이스의 루트 경로를 반환합니다.
func GetProjectRoot() string {
	wd, err := os.Getwd()
	if err != nil {
		return "."
	}

	for dir := wd; ; dir = filepath.Dir(dir) {
		if PathExists(filepath.Join(dir, "src", "robo_claw_bringup")) && PathExists(filepath.Join(dir, "robo_claw_cli")) {
			return dir
		}

		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
	}

	return wd
}

// ResolveEnvWithFallback envMap에서 key를 먼저 조회하고, 없으면 호스트 환경변수에서 가져옵니다.
func ResolveEnvWithFallback(envMap map[string]string, key string) string {
	if v := envMap[key]; v != "" {
		return v
	}
	return os.Getenv(key)
}

// PathExists 해당 경로의 파일 또는 디렉토리가 존재하는지 확인합니다.
func PathExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

// rosDistroSetupFiles 는 ROS 배포판 감지에 사용하는 setup 파일 경로입니다.
// scripts/run_in_ros_env.sh 와 동일한 순서를 유지해, 같은 호스트에서 두 진입점이
// 서로 다른 배포판을 소싱하는 문제를 막습니다.
var rosDistroSetupFiles = []struct {
	Name string
	Path string
}{
	{Name: "humble", Path: "/opt/ros/humble/setup.bash"},
	{Name: "jazzy", Path: "/opt/ros/jazzy/setup.bash"},
}

// DetectRosDistro 실행 환경의 ROS 배포판을 결정합니다.
// 우선순위: ROS_DISTRO 환경변수 -> /opt/ros/<distro>/setup.bash 존재 여부 -> jazzy.
func DetectRosDistro() string {
	if distro := os.Getenv("ROS_DISTRO"); distro != "" {
		return distro
	}
	for _, candidate := range rosDistroSetupFiles {
		if PathExists(candidate.Path) {
			return candidate.Name
		}
	}
	return "jazzy"
}

// 시뮬레이션 엔진 선택값.
const (
	// SimEngineAuto 기본 엔진을 자동 선택합니다(= stub).
	SimEngineAuto = "auto"
	// SimEngineStub Gazebo 없이 도는 RoboClaw 전용 시뮬레이터(robo_claw_sim).
	SimEngineStub = "stub"
)

// simEngineAliases 사용자가 입력할 수 있는 별칭.
var simEngineAliases = map[string]string{
	"":         SimEngineAuto,
	"auto":     SimEngineAuto,
	"stub":     SimEngineStub,
	"lite":     SimEngineStub,
}

// removedSimEngines 제거된 엔진 별칭과 안내 메시지입니다.
var removedSimEngines = map[string]string{
	"gz":       "Gazebo 시뮬레이션 경로는 제거되었습니다. Gazebo 없이 도는 stub 엔진을 쓰세요",
	"harmonic": "Gazebo 시뮬레이션 경로는 제거되었습니다. Gazebo 없이 도는 stub 엔진을 쓰세요",
	"classic":  "Gazebo 시뮬레이션 경로는 제거되었습니다. Gazebo 없이 도는 stub 엔진을 쓰세요",
	"gazebo":   "Gazebo 시뮬레이션 경로는 제거되었습니다. Gazebo 없이 도는 stub 엔진을 쓰세요",
}

// SupportedSimEngines `--engine` 로 지정 가능한 값 목록(도움말·오류 메시지용).
var SupportedSimEngines = []string{SimEngineAuto, SimEngineStub}

// NormalizeSimEngine `--engine` 값을 정규화합니다. 지원하지 않는 값이면 오류를 반환합니다.
func NormalizeSimEngine(requested string) (string, error) {
	key := strings.ToLower(strings.TrimSpace(requested))
	if hint, removed := removedSimEngines[key]; removed {
		return "", fmt.Errorf("지원하지 않는 시뮬레이션 엔진입니다: %q (%s)", requested, hint)
	}
	normalized, ok := simEngineAliases[key]
	if !ok {
		return "", fmt.Errorf(
			"지원하지 않는 시뮬레이션 엔진입니다: %q (가능: %s)",
			requested, strings.Join(SupportedSimEngines, ", "))
	}
	return normalized, nil
}

// ResolveSimEngine 요청값으로 실제 launch 에 넣을 엔진을 결정합니다.
//
// Gazebo 경로 제거로 지원 엔진은 stub 뿐이므로, auto(또는 잘못된 값)는 stub 입니다.
func ResolveSimEngine(requested string) string {
	return SimEngineStub
}

// SupportedSimBackends 시뮬레이터 백엔드(robo_claw_sim) 선택값.
var SupportedSimBackends = []string{"kinematic", "mujoco"}

// NormalizeSimBackend `--sim-backend` 값을 검증/정규화합니다.
func NormalizeSimBackend(requested string) (string, error) {
	key := strings.ToLower(strings.TrimSpace(requested))
	if key == "" {
		return "", nil
	}
	for _, candidate := range SupportedSimBackends {
		if key == candidate {
			return key, nil
		}
	}
	return "", fmt.Errorf(
		"지원하지 않는 시뮬레이션 백엔드입니다: %q (가능: %s)",
		requested, strings.Join(SupportedSimBackends, ", "))
}

// EnvFlagEnabled 환경변수 문자열이 참 값을 갖는지 확인합니다(대소문자 무관).
func EnvFlagEnabled(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}
