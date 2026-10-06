package cmd

import (
	"os"
	"path/filepath"
	"robo_claw_cli/config"

	wcliconfig "github.com/wkqco33/wcli/config"
)

var cfg config.Config

// configFilePath 로드 시 사용하거나 기본 저장 대상이 되는 설정 파일 경로.
var configFilePath string

// Cfg 현재 설정값을 반환한다
func Cfg() *config.Config {
	return &cfg
}

// ConfigFilePath 현재 설정 파일 경로(플랫폼별 기본 저장 위치)를 반환한다.
func ConfigFilePath() string {
	return configFilePath
}

// InitConfig wcli로 설정을 로드한다. root의 PersistentPreRun에서 호출.
func InitConfig() error {
	files := []string{"config.yaml"}

	// 실행 파일과 동일한 위치 및 서브디렉토리의 config.yaml 경로도 자동 추가하여 실행 위치에 무관하게 로드 보장
	execPath, err := os.Executable()
	if err == nil {
		execDir := filepath.Dir(execPath)
		files = append(files,
			filepath.Join(execDir, "config.yaml"),
			filepath.Join(execDir, "robo_claw_cli", "config.yaml"),
		)
	}

	// 플랫폼별 기본 저장 폴더(기본 ~/.config/robo_claw_cli/config.yaml)의 설정을 마지막에 로드하여
	// 프로젝트/실행 디렉토리의 기본 config.yaml을 덮어쓰게 한다. (환경변수는 항상 최우선)
	if defaultPath, perr := config.DefaultPath(); perr == nil {
		files = append(files, defaultPath)
		configFilePath = defaultPath
	}

	return wcliconfig.Load(&cfg,
		wcliconfig.WithFiles(files...),
		wcliconfig.WithEnv(),
		wcliconfig.WithPrefix("ROBO_CLAW_CLI"),
	)
}
