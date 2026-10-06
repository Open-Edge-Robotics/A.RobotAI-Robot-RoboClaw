package cmd

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
)

// ListCmd ai-config-server에 등록된 설정 목록을 조회하는 커맨드
func ListCmd() *wcli.Command {
	var robot string
	var env string

	cmd := &wcli.Command{
		Use:   "list",
		Short: "원격 서버의 설정 프로필 목록을 조회합니다.",
		Run: func(ctx *wcli.Context) error {
			if robot == "" || env == "" {
				return fmt.Errorf("로봇 이름과 환경명을 모두 입력해야 합니다. 예: robo_claw_cli list --robot former --env lab2")
			}

			baseURL, err := serverBaseURL()
			if err != nil {
				return err
			}
			apiURL := fmt.Sprintf("%s/api/v1/configs", baseURL)

			// 로드된 DeviceToken 상태 디버깅
			if cfg.DeviceToken == "" {
				logging.Warn("config에서 DeviceToken을 읽지 못했습니다. Authorization 헤더가 전송되지 않습니다.")
			} else {
				masked := cfg.DeviceToken
				if len(masked) > 4 {
					masked = masked[:4] + "..."
				}
				logging.Debug("config에서 DeviceToken 로드됨: %s (길이: %d)", masked, len(cfg.DeviceToken))
			}

			// 쿼리 매개변수 빌드
			params := url.Values{}
			if robot != "" {
				params.Add("robot_name", robot)
			}
			if env != "" {
				params.Add("environment", env)
			}
			if len(params) > 0 {
				apiURL = apiURL + "?" + params.Encode()
			}

			// 로딩 스피너 작동
			spinner := rich.NewSpinner(os.Stderr)
			spinner.Start("원격 설정 서버에서 데이터 조회 중")
			logging.Info("원격 설정 서버 데이터 조회 요청. (URL: %s)", apiURL)

			// HTTP 요청 수행
			client := &http.Client{
				Timeout: 10 * time.Second,
			}
			resp, err := doGetRequest(ctx, client, apiURL)
			if err != nil {
				spinner.Stop("")
				logging.Error("원격 서버 연결 실패: %v", err)
				return &NetworkError{URL: apiURL, Err: err}
			}
			defer resp.Body.Close()

			if resp.StatusCode == http.StatusUnauthorized {
				spinner.Stop("")
				serverMsg := readServerError(resp)
				if cfg.DeviceToken == "" {
					logging.Error("인증 실패 (HTTP 401): config.yaml의 device_token이 비어 있습니다.")
					return &NetworkError{
						URL:        apiURL,
						StatusCode: resp.StatusCode,
						Err:        fmt.Errorf("인증 실패 (HTTP 401): config.yaml에 device_token이 설정되지 않았습니다"),
						ServerMsg:  serverMsg,
					}
				}
				logging.Error("인증 실패 (HTTP 401): 서버가 토큰을 거부했습니다. %s", serverMsg)
				return &NetworkError{
					URL:        apiURL,
					StatusCode: resp.StatusCode,
					Err:        fmt.Errorf("인증 실패 (HTTP 401): DeviceToken을 확인하세요"),
					ServerMsg:  serverMsg,
				}
			}

			if resp.StatusCode != http.StatusOK {
				spinner.Stop("")
				serverMsg := readServerError(resp)
				logging.Error("서버 상태 코드 에러: %d (응답: %s)", resp.StatusCode, serverMsg)
				return &NetworkError{
					URL:        apiURL,
					StatusCode: resp.StatusCode,
					Err:        fmt.Errorf("서버 응답 오류"),
					ServerMsg:  serverMsg,
				}
			}

			var items []RoboClawConfig
			if err := json.NewDecoder(resp.Body).Decode(&items); err != nil {
				spinner.Stop("")
				logging.Error("응답 JSON 파싱 실패: %v", err)
				return fmt.Errorf("응답 데이터 파싱 실패: %w", err)
			}

			spinner.Stop("[green]✔[/green] 설정 목록 조회 완료")
			logging.Info("설정 목록 조회 완료 (총 %d개)", len(items))

			if len(items) == 0 {
				rich.Println("[yellow]등록된 설정 프로필이 없습니다.[/yellow]")
				return nil
			}

			// 결과 표 출력
			table := rich.NewTable("ID", "프로필 이름", "대상 로봇", "구동 환경", "활성화", "LLM 제공자", "LLM 모델", "RAG")
			for _, item := range items {
				activeStr := "No"
				if item.IsActive {
					activeStr = "[green]Yes[/green]"
				}

				ragStr := "Disabled"
				if item.EnableRag {
					ragStr = "[cyan]Enabled[/cyan]"
				}

				table.AddRow(
					fmt.Sprintf("%d", item.ID),
					item.Name,
					item.RobotName,
					item.Environment,
					activeStr,
					item.LlmProvider,
					item.LlmModel,
					ragStr,
				)
			}

			fmt.Println()
			table.Print()
			fmt.Println()

			return nil
		},
	}

	cmd.Flags().StringVar(&robot, "robot", "r", "", "특정 로봇명으로 필터링")
	cmd.Flags().StringVar(&env, "env", "e", "", "특정 구동 환경으로 필터링")

	return cmd
}
