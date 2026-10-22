package cmd

import (
	"fmt"
	"os"
	"time"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
	"robo_claw_cli/launcher"
)

// LaunchCmd 로컬 캐시를 토대로 ros2 launch(로컬 또는 Docker)를 기동하는 커맨드
func LaunchCmd() *wcli.Command {
	var debug bool
	var sim bool
	var nav2 bool
	var slam bool
	var docker bool
	var world string
	var robotModel string
	var mapFile string
	var imageName string
	var imageTag string
	var pull bool
	var useGrpc bool
	var dryRun bool
	var noConfigSummary bool

	cmd := &wcli.Command{
		Use:   "launch [robot_name] [environment]",
		Short: "원격 설정을 적용하여 로봇 시스템(ROS 2 Launch 또는 Docker 컨테이너)을 구동합니다.",
		Run: func(ctx *wcli.Context) error {
			args := ctx.Args
			if len(args) < 2 {
				return fmt.Errorf("로봇 이름과 환경명을 입력해야 합니다. 예: robo_claw_cli launch butler office")
			}

			logging.Info("robo_claw launch 커맨드를 기동합니다. (로봇: %s, 환경: %s)", args[0], args[1])

			lctx, err := launcher.LoadLaunchContext(ctx, args[0], args[1], launcher.LaunchFlags{
				Debug:      debug,
				Sim:        sim,
				Nav2:       nav2,
				Slam:       slam,
				Docker:     docker,
				World:      world,
				RobotModel: robotModel,
				MapFile:    mapFile,
				ImageName:  imageName,
				ImageTag:   imageTag,
				Pull:       pull,
				ForceGrpc:  useGrpc,
			}, fetchConfig)
			if err != nil {
				logging.Error("launch 환경 구성 실패: %v", err)
				return err
			}

			// 적용 설정 요약: 어떤 설정으로 구동되었는지 나중에 확인할 수 있도록
			// 콘솔에 출력하고 캐시 디렉터리에 로그로도 남긴다.
			if !noConfigSummary {
				printConfigSummary(lctx)
			}
			if dryRun {
				if docker {
					logging.Info("dry-run Docker launch: %s", launcher.MaskCommandSecrets(launcher.BuildDockerLaunchCommand(lctx)))
				} else {
					logging.Info("dry-run local launch: %s", launcher.MaskCommandSecrets(launcher.BuildLocalLaunchCommand(lctx)))
				}
				logging.Info("config source: cache=%s env=%s (적용 값 상세는 위 요약 또는 cache/logs/applied_config_*.log 참고)", lctx.CacheDir, lctx.EnvFilePath)
				return nil
			}

			if docker {
				logging.Info("Docker 컨테이너 기반으로 시스템을 기동합니다.")
				return launcher.RunDockerMode(ctx, lctx)
			}
			logging.Info("로컬 네이티브 프로세스로 시스템을 기동합니다.")
			return launcher.RunLocalMode(ctx, lctx)
		},
	}

	cmd.Flags().BoolVar(&debug, "debug", "d", false, "ROS 2 디버그 로깅 활성화")
	cmd.Flags().BoolVar(&sim, "sim", "s", false, "시뮬레이션 모드로 기동")
	cmd.Flags().BoolVar(&docker, "docker", "", false, "Docker 컨테이너 기반으로 시스템을 기동")
	cmd.Flags().BoolVar(&nav2, "nav2", "", false, "(Sim) Nav2 내비게이션 패키지 활성화")
	cmd.Flags().BoolVar(&slam, "slam", "", false, "(Sim) SLAM 및 Nav2 내비게이션 패키지 동시 활성화")
	cmd.Flags().StringVar(&world, "world", "", "", "(Sim) 로드할 시뮬레이션 월드 파일명")
	cmd.Flags().StringVar(&robotModel, "model", "", "", "(Sim) 사용할 로봇 모델 (예: waffle)")
	cmd.Flags().StringVar(&mapFile, "map", "", "", "(Sim) 사용할 맵(.yaml) 파일 경로")
	cmd.Flags().StringVar(&imageName, "image", "", "lgecloudroboticstask/robo-claw", "(Docker) 사용할 컨테이너 이미지명 (예: myrepo/robo-claw)")
	cmd.Flags().StringVar(&imageTag, "image-tag", "", "latest", "(Docker) 사용할 컨테이너 이미지 태그")
	cmd.Flags().BoolVar(&pull, "pull", "", false, "(Docker) 실행 직전 이미지를 레지스트리에서 pull")
	cmd.Flags().BoolVar(&useGrpc, "use-grpc", "", false, "gRPC 서버 노드 활성화 (robo_claw_cli test 연동 시 필요)")
	cmd.Flags().BoolVar(&dryRun, "dry-run", "", false, "실행하지 않고 최종 launch command만 출력")
	cmd.Flags().BoolVar(&noConfigSummary, "no-config-summary", "", false, "적용 설정 요약 출력과 요약 로그 저장을 생략")

	return cmd
}

// printConfigSummary 적용 설정 요약을 콘솔에 출력하고 로그 파일로 저장합니다.
func printConfigSummary(lctx *launcher.LaunchContext) {
	sections := launcher.BuildConfigSummary(lctx)
	launcher.PrintConfigSummary(os.Stdout, sections)

	path, err := launcher.WriteConfigSummaryLog(lctx, sections, time.Now())
	if err != nil {
		logging.Warn("적용 설정 로그 저장 실패: %v", err)
		return
	}
	logging.Info("적용 설정 로그 저장: %s", path)
	rich.Println("[dim]ℹ 적용 설정 로그: %s[/dim]", path)
}
