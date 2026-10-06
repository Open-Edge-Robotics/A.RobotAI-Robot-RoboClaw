package launcher

import (
	"bufio"
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

// DefaultButlerScriptsDir butler 실행 스크립트의 고정 경로.
// butler 로봇은 이 경로가 존재해야 정상 동작한다.
const DefaultButlerScriptsDir = "/home/seoyc/Workspace/ros/butler/products/prd_butler_v01_magok_w02/script"

// DefaultButlerSourceDir butler ROS 2 워크스페이스(소싱용) 고정 경로.
const DefaultButlerSourceDir = "/home/udr/workspace/butler_v01_config/cloi2_ws"

// ResolveButlerScriptsDir butler 스크립트 경로를 결정한다.
// 우선순위: envMap/환경변수(RC_BUTLER_SCRIPTS_DIR) -> 프로젝트 루트/butler_scripts -> 고정 경로.
// 고정 경로마저 없으면 빈 문자열을 반환한다.
func ResolveButlerScriptsDir(envMap map[string]string, projectRoot string) string {
	if dir := ResolveEnvWithFallback(envMap, "RC_BUTLER_SCRIPTS_DIR"); dir != "" {
		return dir
	}
	localDir := filepath.Join(projectRoot, "butler_scripts")
	if PathExists(localDir) {
		return localDir
	}
	if PathExists(DefaultButlerScriptsDir) {
		return DefaultButlerScriptsDir
	}
	return ""
}

// ResolveButlerSourceDir butler 소스(소싱용) 경로를 결정한다.
// 우선순위: envMap/환경변수(RC_BUTLER_SOURCE_DIR) -> 고정 경로.
func ResolveButlerSourceDir(envMap map[string]string) string {
	if dir := ResolveEnvWithFallback(envMap, "RC_BUTLER_SOURCE_DIR"); dir != "" {
		return dir
	}
	if PathExists(DefaultButlerSourceDir) {
		return DefaultButlerSourceDir
	}
	return ""
}
