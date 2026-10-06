package config

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// AppDirName 로컬 기본 설정 디렉토리 하위에 사용하는 애플리케이션 디렉토리명.
const AppDirName = "robo_claw_cli"

// ConfigFileName 기본 설정 파일 이름.
const ConfigFileName = "config.yaml"

// EnvConfigDir 플랫폼 기본 설정 디렉토리를 덮어쓰기 위한 환경변수 (ROBO_CLAW_CLI_CONFIG_DIR).
const EnvConfigDir = "ROBO_CLAW_CLI_CONFIG_DIR"

// DefaultDir 플랫폼별 사용자 설정 기본 디렉토리를 반환한다.
//
//	linux/unix: $XDG_CONFIG_HOME/robo_claw_cli 또는 ~/.config/robo_claw_cli
//	macOS:      ~/Library/Application Support/robo_claw_cli
//	windows:    %AppData%\robo_claw_cli
//
// ROBO_CLAW_CLI_CONFIG_DIR 환경변수가 설정되어 있으면 그 값을 우선 사용한다.
func DefaultDir() (string, error) {
	if override := os.Getenv(EnvConfigDir); strings.TrimSpace(override) != "" {
		return filepath.Clean(override), nil
	}
	base, err := os.UserConfigDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(base, AppDirName), nil
}

// DefaultPath 플랫폼별 기본 config.yaml 경로(기본 저장 위치)를 반환한다.
func DefaultPath() (string, error) {
	dir, err := DefaultDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(dir, ConfigFileName), nil
}

// PlatformName 현재 실행 중인 OS 플랫폼 이름을 반환한다 (linux/darwin/windows/...).
func PlatformName() string {
	return runtime.GOOS
}
