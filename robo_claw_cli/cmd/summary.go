package cmd

import (
	"fmt"
	"os"
	"time"

	"github.com/wkqco33/wcli"
	"robo_claw_cli/launcher"
)

// ConfigSummaryCmd 캐시된 설정으로 구동 시 적용되는 값을 요약해 출력하는 커맨드.
// 원격 동기화를 수행하지 않으므로 구동 후 어떤 설정이 적용되었는지 다시 확인할 때 사용한다.
func ConfigSummaryCmd() *wcli.Command {
	var writeLog bool
	var docker bool
	var sim bool

	cmd := &wcli.Command{
		Use:   "config-summary [robot_name] [environment]",
		Short: "구동 시 적용되는 설정을 섹션별로 상세히 출력합니다.",
		Run: func(ctx *wcli.Context) error {
			args := ctx.Args
			if len(args) < 2 {
				return fmt.Errorf("로봇 이름과 환경명을 입력해야 합니다. 예: robo_claw_cli config-summary butler office")
			}
			lctx, err := launcher.NewLaunchContextFromCache(
				args[0], args[1],
				launcher.LaunchFlags{Docker: docker, Sim: sim},
				launcher.DefaultCacheDir(args[0], args[1]),
				launcher.ConfigSourceCache,
			)
			if err != nil {
				return err
			}

			sections := launcher.BuildConfigSummary(lctx)
			launcher.PrintConfigSummary(os.Stdout, sections)

			if writeLog {
				path, err := launcher.WriteConfigSummaryLog(lctx, sections, time.Now())
				if err != nil {
					return err
				}
				fmt.Fprintf(os.Stdout, "적용 설정 로그 저장: %s\n", path)
			}
			return nil
		},
	}

	cmd.Flags().BoolVar(&writeLog, "write-log", "", false, "적용 설정 로그 파일로도 저장")
	cmd.Flags().BoolVar(&docker, "docker", "", false, "(표시용) Docker 컨테이너 실행 모드로 가정")
	cmd.Flags().BoolVar(&sim, "sim", "s", false, "(표시용) 시뮬레이션 launch 파일로 가정")

	return cmd
}
