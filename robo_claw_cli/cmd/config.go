package cmd

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"

	"robo_claw_cli/config"

	"github.com/wkqco33/wcli"
)

// ConfigCmd 로컬 rclaw 설정(플랫폼별 기본 저장 폴더의 config.yaml)을 확인·관리하는 커맨드.
// 하위 커맨드: init, show, path, raw, set, edit.
func ConfigCmd() *wcli.Command {
	cmd := &wcli.Command{
		Use:   "config",
		Short: "로컬 rclaw 설정(config.yaml)을 확인하고 관리합니다.",
		Long:  "플랫폼별 기본 저장 폴더(기본은 $XDG_CONFIG_HOME/robo_claw_cli, macOS Library/Application Support, Windows %AppData%)의 config.yaml을 확인하고 관리합니다. 하위 커맨드: init, show, path, raw, set, edit.",
		Run: func(ctx *wcli.Context) error {
			// 서브커맨드 없이 실행되면 show처럼 동작한다.
			return runConfigShow()
		},
	}
	cmd.AddCommand(
		configInitCmd(),
		configPathCmd(),
		configShowCmd(),
		configRawCmd(),
		configSetCmd(),
		configEditCmd(),
	)
	return cmd
}

func configInitCmd() *wcli.Command {
	var path string
	var force bool
	cmd := &wcli.Command{
		Use:   "init",
		Short: "플랫폼별 기본 폴더에 기본 config.yaml을 생성합니다.",
		Run: func(ctx *wcli.Context) error {
			target := configFilePath
			if path != "" {
				target = path
			} else if target == "" {
				dp, err := config.DefaultPath()
				if err != nil {
					return fmt.Errorf("기본 설정 경로를 결정할 수 없습니다: %w", err)
				}
				target = dp
			}

			if _, err := os.Stat(target); err == nil && !force {
				return fmt.Errorf("설정 파일이 이미 존재합니다: %s (덮어쓰려면 --force 사용)", target)
			}

			if err := os.MkdirAll(filepath.Dir(target), 0o755); err != nil {
				return fmt.Errorf("설정 디렉토리 생성 실패: %w", err)
			}

			if err := writeConfigYAML(target, cfg); err != nil {
				return fmt.Errorf("설정 파일 생성 실패: %w", err)
			}
			initLogOK(target)
			return nil
		},
	}
	cmd.Flags().StringVar(&path, "path", "p", "", "설정 파일 경로를 명시적으로 지정합니다")
	cmd.Flags().BoolVar(&force, "force", "f", false, "기존 파일이 있어도 덮어씁니다")
	return cmd
}

func configPathCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "path",
		Short: "현재 설정 파일 경로(플랫폼별 기본 저장 위치)를 출력합니다.",
		Run: func(ctx *wcli.Context) error {
			if configFilePath == "" {
				dp, err := config.DefaultPath()
				if err != nil {
					return fmt.Errorf("기본 설정 경로를 결정할 수 없습니다: %w", err)
				}
				configFilePath = dp
			}
			exists := "존재하지 않음"
			if _, err := os.Stat(configFilePath); err == nil {
				exists = "존재"
			}
			fmt.Printf("config_path:   %s\n", configFilePath)
			fmt.Printf("platform:       %s\n", config.PlatformName())
			fmt.Printf("file_status:    %s (초기화하려면: rclaw config init)\n", exists)
			return nil
		},
	}
}

func configShowCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "show",
		Short: "현재 적용 중인(유효) 설정값을 출력합니다. (환경변수 반영)",
		Run: func(ctx *wcli.Context) error {
			return runConfigShow()
		},
	}
}

func runConfigShow() error {
	srcPath := configFilePath
	if srcPath == "" {
		dp, err := config.DefaultPath()
		if err != nil {
			return fmt.Errorf("기본 설정 경로를 결정할 수 없습니다: %w", err)
		}
		srcPath = dp
	}
	fmt.Printf("=== rclaw 현재 설정 (유효값) ===\n")
	fmt.Printf("name: %q\n", cfg.AppName)
	fmt.Printf("device_token: %s\n", maskToken(cfg.DeviceToken))
	fmt.Printf("server.host: %q\n", cfg.Server.Host)
	fmt.Printf("server.port: %d\n", cfg.Server.Port)
	fmt.Printf("server.allow_insecure_http: %t\n", cfg.Server.AllowInsecureHTTP)
	fmt.Printf("log.level: %q\n", cfg.Log.Level)
	fmt.Printf("contract.project: %q\n", cfg.Contract.Project)
	fmt.Printf("contract.base_url: %q\n", cfg.Contract.BaseURL)
	fmt.Printf("설정 파일: %s\n", srcPath)
	fmt.Printf("(설정 파일 수정: rclaw config set <key> <value> 또는 rclaw config edit)\n")
	return nil
}

func configRawCmd() *wcli.Command {
	var path string
	cmd := &wcli.Command{
		Use:   "raw",
		Short: "디스크에 저장된 현재 config.yaml의 원본 내용을 출력합니다.",
		Run: func(ctx *wcli.Context) error {
			target := configFilePath
			if path != "" {
				target = path
			} else if target == "" {
				dp, err := config.DefaultPath()
				if err != nil {
					return fmt.Errorf("기본 설정 경로를 결정할 수 없습니다: %w", err)
				}
				target = dp
			}
			content, err := os.ReadFile(target)
			if err != nil {
				if os.IsNotExist(err) {
					fmt.Printf("설정 파일이 아직 없습니다: %s (초기화하려면: rclaw config init)\n", target)
					return nil
				}
				return err
			}
			fmt.Print(string(content))
			return nil
		},
	}
	cmd.Flags().StringVar(&path, "path", "p", "", "설정 파일 경로를 명시적으로 지정합니다")
	return cmd
}

func configSetCmd() *wcli.Command {
	var path string
	cmd := &wcli.Command{
		Use:   "set <key> <value>",
		Short: "설정 값을 변경합니다. (예: rclaw config set server.host 10.0.0.1)",
		Run: func(ctx *wcli.Context) error {
			if len(ctx.Args) < 2 {
				return fmt.Errorf("사용법: rclaw config set <key> <value> (예: server.host 10.0.0.1)")
			}
			key := strings.ToLower(strings.TrimSpace(ctx.Args[0]))
			value := strings.TrimSpace(strings.Join(ctx.Args[1:], " "))
			if !validConfigKey(key) {
				return fmt.Errorf("지원하지 않는 설정 키입니다: %q (지원 키: name, device_token, server.host, server.port, server.allow_insecure_http, log.level, contract.project, contract.base_url)", key)
			}

			target := configFilePath
			if path != "" {
				target = path
			} else if target == "" {
				dp, err := config.DefaultPath()
				if err != nil {
					return fmt.Errorf("기본 설정 경로를 결정할 수 없습니다: %w", err)
				}
				target = dp
			}

			if _, err := os.Stat(target); err != nil {
				return fmt.Errorf("설정 파일이 없습니다: %s (먼저 rclaw config init 실행)", target)
			}

			replaced, oldVal, err := setConfigValue(target, key, value)
			if err != nil {
				return err
			}
			fmt.Printf("설정 변경 완료 (%s):\n", target)
			if replaced {
				fmt.Printf("  %s: %q -> %q\n", key, oldVal, value)
			} else {
				fmt.Printf("  %s = %q (신규 추가)\n", key, value)
			}
			return nil
		},
	}
	cmd.Flags().StringVar(&path, "path", "p", "", "설정 파일 경로를 명시적으로 지정합니다")
	return cmd
}

func configEditCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "edit",
		Short: "설정 파일을 기본 편집기($EDITOR)로 엽니다.",
		Run: func(ctx *wcli.Context) error {
			target := configFilePath
			if target == "" {
				dp, err := config.DefaultPath()
				if err != nil {
					return fmt.Errorf("기본 설정 경로를 결정할 수 없습니다: %w", err)
				}
				target = dp
			}
			if _, err := os.Stat(target); err != nil {
				return fmt.Errorf("설정 파일이 없습니다: %s (먼저 rclaw config init 실행)", target)
			}
			editor := os.Getenv("EDITOR")
			if editor == "" {
				editor = "vi"
			}
			cmd := exec.Command(editor, target)
			cmd.Stdin = os.Stdin
			cmd.Stdout = os.Stdout
			cmd.Stderr = os.Stderr
			if err := cmd.Run(); err != nil {
				return fmt.Errorf("편집기 실행 실패(%s): %w", editor, err)
			}
			return nil
		},
	}
}

func initLogOK(path string) {
	fmt.Printf("설정 파일 초기화 완료: %s\n", path)
	fmt.Printf("현재 유효값 확인: rclaw config show\n변경: rclaw config set <key> <value>\n")
}

// maskToken 출력 시 device_token 을 마스킹한다. (빈 값은 "(설정 안 됨)" 표기)
func maskToken(tok string) string {
	if tok == "" {
		return "(설정 안 됨)"
	}
	if len(tok) <= 4 {
		return "****"
	}
	return tok[:4] + "…(길이 " + strconv.Itoa(len(tok)) + ")"
}

// validConfigKey 설정 커맨드에서 변경 가능한 키인지 검증한다.
func validConfigKey(key string) bool {
	switch key {
	case "name", "device_token",
		"server.host", "server.port", "server.allow_insecure_http",
		"log.level",
		"contract.project", "contract.base_url":
		return true
	}
	return false
}
