package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	"robo_claw_cli/launcher"
	"robo_claw_cli/tester"

	msg_pb "robo_claw_cli/proto/messenger_pb"
	pb "robo_claw_cli/proto/robo_pb"
)

// TestCmd 로봇 연동 및 AI 에이전트 검증 자동화 테스트를 수행하는 커맨드
func TestCmd() *wcli.Command {
	var robot string
	var env string
	var runNavigation bool
	var runManipulation bool
	var testSpeed float64
	var fetchLatest bool
	var outputReport string
	var host string
	var port int
	var container string
	var scenarioFile string // 신규: 커스텀 시나리오 파일 주입

	cmd := &wcli.Command{
		Use:   "test",
		Short: "원격 설정을 기반으로 로봇 연동 상태 및 AI 에이전트 수행 능력을 검증합니다.",
		Run: func(ctx *wcli.Context) error {
			if robot == "" || env == "" {
				return fmt.Errorf("대상 로봇 이름과 환경명을 지정해야 합니다. 예: robo_claw_cli test -r butler -e office")
			}

			homeDir, err := os.UserHomeDir()
			if err != nil {
				return fmt.Errorf("홈 디렉토리를 가져올 수 없습니다: %w", err)
			}
			cacheDir := filepath.Join(homeDir, ".robo_claw", "config_cache", fmt.Sprintf("%s_%s", robot, env))

			// 캐시 존재 여부 검사
			_, err = os.Stat(cacheDir)
			cacheExists := !os.IsNotExist(err)

			// fetch-latest가 켜졌거나 캐시가 없는 경우 fetchConfig 수행
			if fetchLatest || !cacheExists {
				spinner := rich.NewSpinner(os.Stderr)
				spinner.Start(fmt.Sprintf("로봇 '%s' (%s 환경)의 활성 설정을 원격에서 동기화 중...", robot, env))
				logging.Info("테스트용 설정을 원격에서 동기화 시도 중... (로봇: %s, 환경: %s)", robot, env)
				if err := fetchConfig(ctx, robot, env); err != nil {
					spinner.Stop("")
					logging.Error("테스트용 설정 동기화 실패: %v", err)
					return fmt.Errorf("설정 동기화 실패: %w", err)
				}
				spinner.Stop("[green]✔[/green] 원격 설정 동기화 및 캐싱 완료")
				logging.Info("테스트용 설정 동기화 완료")
			}

			// 캐시 파일 로드
			configJSONPath := filepath.Join(cacheDir, "config.json")
			configJSONBytes, err := os.ReadFile(configJSONPath)
			if err != nil {
				return fmt.Errorf("캐시된 설정을 읽을 수 없습니다: %w", err)
			}

			var activeConfig RoboClawConfig
			if err := json.Unmarshal(configJSONBytes, &activeConfig); err != nil {
				return fmt.Errorf("설정 JSON 파싱 실패: %w", err)
			}

			rich.Println("[cyan]정보:[/cyan] 설정 프로필 로드 완료 (LLM: %s/%s)", activeConfig.LlmProvider, activeConfig.LlmModel)

			// Limits 파싱
			var limits tester.RobotLimits
			limitsPath := filepath.Join(cacheDir, "ROBOT_LIMITS.json")
			if limitsBytes, err := os.ReadFile(limitsPath); err == nil {
				json.Unmarshal(limitsBytes, &limits)
			}

			// ROBOT.md(소울) 파일 로드
			var robotSoul string
			soulPath := filepath.Join(cacheDir, "ROBOT.md")
			if soulBytes, err := os.ReadFile(soulPath); err == nil {
				robotSoul = string(soulBytes)
			}

			// .env 파싱
			_, envMap, _ := launcher.ParseEnvFile(filepath.Join(cacheDir, ".env"))

			// 도커 컨테이너 IP 감지
			dockerIP := ""
			containerTarget := container
			if containerTarget == "" {
				containerTarget = envMap["RC_DOCKER_CONTAINER"]
			}
			if containerTarget != "" {
				dockerIP = detectDockerContainerIP(containerTarget)
			}

			// gRPC 호스트/포트 결정
			grpcHost := host
			if grpcHost == "" {
				grpcHost = dockerIP
			}
			if grpcHost == "" {
				grpcHost = envMap["RC_GRPC_HOST"]
			}
			if grpcHost == "" {
				grpcHost = "localhost"
			}

			grpcPort := port
			if grpcPort == 0 {
				if envPort, ok := envMap["RC_GRPC_PORT"]; ok {
					fmt.Sscanf(envPort, "%d", &grpcPort)
				}
			}
			if grpcPort == 0 {
				grpcPort = 50051
			}

			grpcTarget := fmt.Sprintf("%s:%d", grpcHost, grpcPort)

			// 보고서 경로 설정
			wd, _ := os.Getwd()
			if outputReport == "" {
				outputReport = filepath.Join(wd, fmt.Sprintf("test_report_%s_%s.md", robot, env))
			}

			// gRPC 연결 시작
			rich.Println("\n[green]✔[/green] [테스트 시작] 대상 주소: [white]%s[/white]", grpcTarget)
			fmt.Println(strings.Repeat("=", 60))

			spinner := rich.NewSpinner(os.Stderr)
			spinner.Start("[yellow]로봇 gRPC 서비스 및 AI 에이전트 검증 통신 진행 중...[/yellow]")

			dialCtx, cancelDial := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancelDial()
			conn, err := grpc.DialContext(dialCtx, grpcTarget,
				grpc.WithTransportCredentials(insecure.NewCredentials()),
				grpc.WithBlock(),
			)

			if err != nil {
				spinner.Stop("")
				rich.Println("[red]❌ Connection Error:[/red] gRPC 서버 '%s'에 연결할 수 없습니다.", grpcTarget)

				fmt.Println("\n" + strings.Repeat("!", 60))
				fmt.Println("[트러블슈팅 가이드: gRPC 연결 실패 (UNAVAILABLE)]")
				fmt.Println("1. 로봇 또는 도커 컨테이너가 정상 기동되었는지 확인하십시오.")
				if containerTarget != "" {
					fmt.Printf("   - 도커 컨테이너 '%s'의 기동 여부를 'docker ps'로 확인하세요.\n", containerTarget)
				} else {
					fmt.Println("   - 로컬에서 구동 중인 경우: './rclaw run' 또는 './rclaw sim'으로 시스템이 구동되었는지 확인하세요.")
				}
				fmt.Printf("2. 호스트/포트 지정 방법: --host [로봇IP] --port [포트] 옵션을 이용하십시오.\n")
				fmt.Println(strings.Repeat("!", 60) + "\n")

				steps := []tester.TestStepResult{
					{
						Step:   "Step 1",
						Name:   "gRPC 연결성",
						Status: "FAIL",
						Detail: fmt.Sprintf("gRPC 서버 다이얼 타임아웃/실패: %v", err),
					},
				}
				tester.WriteMarkdownReport(outputReport, robot, env, activeConfig.LlmProvider, activeConfig.LlmModel, grpcTarget, containerTarget, "gRPC 연결 실패", steps)
				return fmt.Errorf("gRPC 연결 실패: %w", err)
			}
			defer conn.Close()

			rosClient := pb.NewRosGrpcClient(conn)
			msgClient := msg_pb.NewRoboMessengerClient(conn)

			// 시나리오 결정
			var scenario *tester.Scenario
			if scenarioFile != "" {
				logging.Info("외부 테스트 시나리오 로드 시도: %s", scenarioFile)
				var scenarioErr error
				scenario, scenarioErr = tester.LoadScenarioFile(scenarioFile)
				if scenarioErr != nil {
					spinner.Stop("")
					logging.Error("외부 시나리오 파일 로딩 실패: %v", scenarioErr)
					return fmt.Errorf("시나리오 로드 실패: %w", scenarioErr)
				}
				logging.Info("외부 테스트 시나리오 로드 완료: %s", scenario.Name)
			} else {
				// 로컬 캐시 scenario.json 로드 시도
				scenarioJSONPath := filepath.Join(cacheDir, "scenario.json")
				var loadErr error
				scenario, loadErr = tester.LoadScenarioFile(scenarioJSONPath)
				if loadErr != nil {
					// 캐시가 없거나 오류가 난 경우 원격 설정 동기화(fetchConfig)를 실행하여 캐시 덤프 구축 시도
					logging.Info("로컬 시나리오 캐시 파일이 없거나 읽을 수 없습니다: %v. 원격 설정 동기화를 구동합니다...", loadErr)
					if fetchErr := fetchConfig(ctx, robot, env); fetchErr == nil {
						scenario, _ = tester.LoadScenarioFile(scenarioJSONPath)
					}
				}

				// 최후의 수단으로 폴백 (동기화 실패 또는 서버에 시나리오 부재 시)
				if scenario == nil {
					logging.Info("기본 내장 테스트 시나리오를 사용합니다.")
					defaultScenario := tester.GetDefaultScenario(runNavigation, runManipulation, testSpeed)
					scenario = &defaultScenario
				} else {
					logging.Info("원격 테스트 시나리오 로드 완료: %s", scenario.Name)
				}
			}

			useRosGrpc := true
			tctx := &tester.TestContext{
				Ctx:             context.Background(),
				WcliCtx:         ctx,
				RosClient:       rosClient,
				MsgClient:       msgClient,
				Limits:          limits,
				RobotSoul:       robotSoul,
				RobotName:       robot,
				EnvironmentName: env,
				UseRosGrpc:      &useRosGrpc,
			}

			// 시나리오 실행
			steps := tester.RunScenario(tctx, scenario)

			spinner.Stop("[green]✔[/green] 로봇 자동화 테스트 완료")
			fmt.Println(strings.Repeat("=", 60))

			// 결과 표 출력
			tester.PrintResultTable(steps)

			testMode := "RosGrpc + Messenger"
			if !useRosGrpc {
				testMode = "Messenger Only (RosGrpc 비활성)"
			}

			// 리포트 생성
			tester.WriteMarkdownReport(outputReport, robot, env, activeConfig.LlmProvider, activeConfig.LlmModel, grpcTarget, containerTarget, testMode, steps)
			rich.Println("\n[green]✔ 보고서 생성 완료:[/green] [white]%s[/white]\n", outputReport)

			return nil
		},
	}

	cmd.Flags().StringVar(&robot, "robot", "r", "", "대상 로봇명 (예: butler)")
	cmd.Flags().StringVar(&env, "env", "e", "", "구동 환경명 (예: office)")
	cmd.Flags().BoolVar(&runNavigation, "run-navigation", "n", false, "네비게이션 주행 및 가드레일 테스트(Step 3) 포함 여부")
	cmd.Flags().BoolVar(&runManipulation, "run-manipulation", "m", false, "매니퓰레이터 구동 테스트(Step 3) 포함 여부")
	cmd.Flags().Float64Var(&testSpeed, "speed", "s", 0.05, "구동 테스트 시의 속도 제한 (m/s)")
	cmd.Flags().BoolVar(&fetchLatest, "fetch-latest", "f", false, "실행 전 강제로 최신 설정을 원격에서 동기화")
	cmd.Flags().StringVar(&outputReport, "output", "o", "", "결과 Markdown 보고서 저장 경로")
	cmd.Flags().StringVar(&host, "host", "", "", "대상 gRPC 서버 호스트 IP")
	cmd.Flags().IntVar(&port, "port", "", 0, "대상 gRPC 서버 포트")
	cmd.Flags().StringVar(&container, "container", "c", "", "도커 컨테이너 이름 (도커 연동 시)")
	cmd.Flags().StringVar(&scenarioFile, "scenario-file", "", "", "사용자 정의 테스트 시나리오 파일 경로 (.json)")

	return cmd
}

// detectDockerContainerIP 도커 컨테이너 IP 주소 자동 검출 헬퍼
func detectDockerContainerIP(containerName string) string {
	isRunCmd := exec.Command("docker", "inspect", "-f", "{{.State.Running}}", containerName)
	out, err := isRunCmd.Output()
	if err != nil || strings.TrimSpace(string(out)) != "true" {
		return "localhost"
	}

	netModeCmd := exec.Command("docker", "inspect", "-f", "{{.HostConfig.NetworkMode}}", containerName)
	outNet, err := netModeCmd.Output()
	if err == nil && strings.TrimSpace(string(outNet)) == "host" {
		return "localhost"
	}

	ipCmd := exec.Command("docker", "inspect", "-f", "{{.NetworkSettings.IPAddress}}", containerName)
	outIp, err := ipCmd.Output()
	if err == nil && strings.TrimSpace(string(outIp)) != "" {
		return strings.TrimSpace(string(outIp))
	}

	ipNetCmd := exec.Command("docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", containerName)
	outIpNet, err := ipNetCmd.Output()
	if err == nil && strings.TrimSpace(string(outIpNet)) != "" {
		return strings.TrimSpace(string(outIpNet))
	}

	return "localhost"
}
