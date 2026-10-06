package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
)

// FetchCmd 원격 설정을 로컬 캐시 디렉토리에 동기화하는 커맨드
func FetchCmd() *wcli.Command {
	cmd := &wcli.Command{
		Use:   "fetch [robot_name] [environment]",
		Short: "원격 서버에서 활성화된 설정을 가져와 로컬에 캐싱합니다.",
		Run: func(ctx *wcli.Context) error {
			args := ctx.Args
			if len(args) < 2 {
				return fmt.Errorf("로봇 이름과 환경명을 입력해야 합니다. 예: robo_claw_cli fetch butler office")
			}
			robotName := args[0]
			envName := args[1]

			if cfg.DeviceToken == "" {
				logging.Warn("config에서 DeviceToken을 읽지 못했습니다. Authorization 헤더가 전송되지 않습니다.")
			} else {
				masked := cfg.DeviceToken
				if len(masked) > 4 {
					masked = masked[:4] + "..."
				}
				logging.Debug("config에서 DeviceToken 로드됨: %s (길이: %d)", masked, len(cfg.DeviceToken))
			}

			logging.Info("로봇 '%s' (%s 환경)의 활성 설정 조회를 시작합니다.", robotName, envName)

			spinner := rich.NewSpinner(os.Stderr)
			spinner.Start(fmt.Sprintf("로봇 '%s' (%s 환경)의 활성 설정 가져오는 중", robotName, envName))

			err := fetchConfig(ctx, robotName, envName)
			if err != nil {
				spinner.Stop("")
				logging.Error("설정 조회 및 캐싱 실패: %v", err)
				return err
			}

			spinner.Stop("[green]✔[/green] 원격 설정 캐싱 완료")
			logging.Info("로봇 '%s' (%s 환경)의 활성 설정 캐싱이 완료되었습니다.", robotName, envName)
			return nil
		},
	}
	return cmd
}

// fetchConfig는 원격 서버로부터 설정을 가져와 로컬 캐시 폴더에 캐싱합니다.
func fetchConfig(ctx *wcli.Context, robotName, envName string) error {
	// API 주소 구성
	baseURL, err := serverBaseURL()
	if err != nil {
		return err
	}
	params := url.Values{"robot_name": {robotName}, "environment": {envName}}
	query := params.Encode()
	manifestConfig, revision, err := fetchRuntimeManifest(ctx, baseURL, query)
	if err != nil {
		return err
	}
	activeConfigURL := fmt.Sprintf("%s/api/v1/configs/active?%s", baseURL, query)
	envFileURL := fmt.Sprintf("%s/api/v1/configs/active/files/.env?%s", baseURL, query)

	logging.Debug("활성 설정 요청 URL: %s", activeConfigURL)

	// 1. 활성 설정 JSON 데이터 조회
	client := &http.Client{
		Timeout: 10 * time.Second,
	}
	var activeConfig RoboClawConfig
	if manifestConfig != nil {
		activeConfig = *manifestConfig
		logging.Debug("v2 runtime manifest를 설정 원본으로 사용합니다 (revision=%s)", revision)
	} else {
		resp, err := doGetRequest(ctx, client, activeConfigURL)
		if err != nil {
			return &NetworkError{URL: activeConfigURL, Err: err}
		}
		defer resp.Body.Close()
		if resp.StatusCode == http.StatusNotFound {
			serverMsg := readServerError(resp)
			return &NetworkError{URL: activeConfigURL, StatusCode: resp.StatusCode, Err: fmt.Errorf("해당 로봇(%s)/환경(%s)에서 활성화된 설정을 찾을 수 없습니다. 대시보드에서 먼저 활성화해주세요.", robotName, envName), ServerMsg: serverMsg}
		}
		if resp.StatusCode != http.StatusOK {
			serverMsg := readServerError(resp)
			return &NetworkError{URL: activeConfigURL, StatusCode: resp.StatusCode, Err: fmt.Errorf("설정 조회 실패 (상태 코드: %d)", resp.StatusCode), ServerMsg: serverMsg}
		}
		if err := json.NewDecoder(resp.Body).Decode(&activeConfig); err != nil {
			return fmt.Errorf("JSON 응답 파싱 실패: %w", err)
		}
	}

	// 캐시 저장 경로 설정
	homeDir, err := os.UserHomeDir()
	if err != nil {
		return fmt.Errorf("홈 디렉토리를 가져올 수 없습니다: %w", err)
	}
	cacheDir := filepath.Join(homeDir, ".robo_claw", "config_cache", fmt.Sprintf("%s_%s", robotName, envName))
	logging.Debug("캐시 저장 디렉토리 생성 시도: %s", cacheDir)
	if err := os.MkdirAll(cacheDir, 0700); err != nil {
		return &CacheError{Path: cacheDir, Op: "mkdir", Err: err}
	}
	if err := os.Chmod(cacheDir, 0700); err != nil {
		return &CacheError{Path: cacheDir, Op: "chmod", Err: err}
	}

	// JSON 파일 덤프
	configJSONPath := filepath.Join(cacheDir, "config.json")
	configJSONBytes, err := json.MarshalIndent(activeConfig, "", "  ")
	if err != nil {
		return fmt.Errorf("설정 캐시 직렬화 실패: %w", err)
	}
	logging.Debug("설정 JSON 파일 저장 시도: %s", configJSONPath)
	if err := os.WriteFile(configJSONPath, configJSONBytes, 0600); err != nil {
		return &CacheError{Path: configJSONPath, Op: "write", Err: err}
	}
	if revision != "" {
		metadata := fmt.Sprintf("{\n  \"schema_version\": \"2.0\",\n  \"config_revision\": %q,\n  \"robot_name\": %q,\n  \"environment\": %q\n}\n", revision, robotName, envName)
		manifestPath := filepath.Join(cacheDir, "manifest.json")
		if err := os.WriteFile(manifestPath, []byte(metadata), 0600); err != nil {
			return &CacheError{Path: manifestPath, Op: "write", Err: err}
		}
	}

	// 마크다운 및 JSON 설정 내용 저장
	filesToWrite := map[string]string{
		"ROBOT.md":           activeConfig.SoulContent,
		"SKILLS.md":          activeConfig.SkillsContent,
		"TROUBLESHOOTING.md": activeConfig.TroubleshootingContent,
		"ROBOT_LIMITS.json":  activeConfig.LimitsContent,
	}

	for filename, content := range filesToWrite {
		filePath := filepath.Join(cacheDir, filename)
		logging.Debug("설정 파일 저장 시도: %s", filePath)
		if err := os.WriteFile(filePath, []byte(content), 0600); err != nil {
			return &CacheError{Path: filePath, Op: "write", Err: err}
		}
	}

	// 2. .env 파일 다운로드
	logging.Debug(".env 파일 요청 URL: %s", envFileURL)
	envResp, err := doGetRequest(ctx, client, envFileURL)
	if err != nil {
		return &NetworkError{URL: envFileURL, Err: err}
	}
	defer envResp.Body.Close()

	if envResp.StatusCode == http.StatusOK {
		envBytes, err := io.ReadAll(envResp.Body)
		if err != nil {
			return fmt.Errorf(".env 데이터 읽기 실패: %w", err)
		}
		envFilePath := filepath.Join(cacheDir, ".env")
		logging.Debug(".env 파일 저장 시도: %s", envFilePath)
		if err := os.WriteFile(envFilePath, envBytes, 0600); err != nil {
			return &CacheError{Path: envFilePath, Op: "write", Err: err}
		}
	} else {
		serverMsg := readServerError(envResp)
		return &NetworkError{
			URL:        envFileURL,
			StatusCode: envResp.StatusCode,
			Err:        fmt.Errorf(".env 서버 반환 오류 (상태 코드: %d)", envResp.StatusCode),
			ServerMsg:  serverMsg,
		}
	}

	// 3. 활성 시나리오 JSON 다운로드
	activeScenarioURL := fmt.Sprintf("%s/api/v1/scenarios/active?robot_name=%s&environment=%s", baseURL, robotName, envName)
	logging.Debug("활성 시나리오 요청 URL: %s", activeScenarioURL)
	scenResp, err := doGetRequest(ctx, client, activeScenarioURL)
	if err != nil {
		logging.Warn("원격 시나리오 서버 연결 실패: %v. 시나리오 동기화를 건너뜁니다.", err)
	} else {
		defer scenResp.Body.Close()
		if scenResp.StatusCode == http.StatusOK {
			scenBytes, err := io.ReadAll(scenResp.Body)
			if err == nil {
				scenFilePath := filepath.Join(cacheDir, "scenario.json")
				logging.Debug("시나리오 파일 저장 시도: %s", scenFilePath)
				_ = os.WriteFile(scenFilePath, scenBytes, 0644)
			}
		} else {
			serverMsg := readServerError(scenResp)
			if serverMsg != "" {
				logging.Warn("시나리오 서버 반환 오류 (상태 코드 %d): %s. 시나리오 동기화를 건너뜁니다.", scenResp.StatusCode, serverMsg)
			} else {
				logging.Warn("시나리오 서버 반환 오류 (상태 코드 %d). 시나리오 동기화를 건너뜁니다.", scenResp.StatusCode)
			}
		}
	}

	return nil
}

// fetchRuntimeManifest probes the versioned endpoint before using the v1
// artifact endpoints. A 404 is an expected compatibility fallback for older
// config servers.
func fetchRuntimeManifest(ctx *wcli.Context, baseURL, query string) (*RoboClawConfig, string, error) {
	manifestURL := fmt.Sprintf("%s/api/v2/device/runtime-config?%s", baseURL, query)
	client := &http.Client{Timeout: 10 * time.Second}
	resp, err := doGetRequest(ctx, client, manifestURL)
	if err != nil {
		return nil, "", &NetworkError{URL: manifestURL, Err: err}
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		logging.Debug("v2 runtime manifest 미지원 서버. v1 endpoint로 fallback합니다.")
		return nil, "", nil
	}
	if resp.StatusCode != http.StatusOK {
		return nil, "", &NetworkError{
			URL:        manifestURL,
			StatusCode: resp.StatusCode,
			Err:        fmt.Errorf("runtime manifest 조회 실패 (상태 코드: %d)", resp.StatusCode),
			ServerMsg:  readServerError(resp),
		}
	}
	var manifest struct {
		SchemaVersion string          `json:"schema_version"`
		Revision      string          `json:"config_revision"`
		Config        json.RawMessage `json:"config"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&manifest); err != nil {
		return nil, "", fmt.Errorf("runtime manifest 파싱 실패: %w", err)
	}
	if manifest.SchemaVersion != "2.0" || len(manifest.Config) == 0 || string(manifest.Config) == "null" {
		return nil, "", fmt.Errorf("지원하지 않는 runtime manifest 형식입니다")
	}
	var config map[string]interface{}
	if err := json.Unmarshal(manifest.Config, &config); err != nil {
		return nil, "", fmt.Errorf("runtime manifest config 파싱 실패: %w", err)
	}
	if err := validateManifestConfig(config); err != nil {
		return nil, "", err
	}
	var activeConfig RoboClawConfig
	if err := json.Unmarshal(manifest.Config, &activeConfig); err != nil {
		return nil, "", fmt.Errorf("runtime manifest config 역직렬화 실패: %w", err)
	}
	logging.Debug("v2 runtime manifest 확인 완료 (schema=%s revision=%s)", manifest.SchemaVersion, manifest.Revision)
	return &activeConfig, manifest.Revision, nil
}

func validateManifestConfig(config map[string]interface{}) error {
	for _, key := range []string{"llm_provider", "llm_model", "ros_domain_id"} {
		if _, ok := config[key]; !ok {
			return fmt.Errorf("runtime manifest 필수 필드가 누락되었습니다: %s", key)
		}
	}
	if provider, ok := config["llm_provider"].(string); !ok || provider == "" {
		return fmt.Errorf("runtime manifest의 llm_provider가 비어 있습니다")
	}
	if model, ok := config["llm_model"].(string); !ok || model == "" {
		return fmt.Errorf("runtime manifest의 llm_model이 비어 있습니다")
	}
	return nil
}

// doGetRequest는 Device Token이 설정된 경우 Authorization Bearer 헤더를 주입하여 Context 바인딩된 GET 요청을 수행합니다.
func doGetRequest(ctx context.Context, client *http.Client, url string) (*http.Response, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
	if err != nil {
		return nil, err
	}
	if cfg.DeviceToken != "" {
		// 디버깅용: 토큰 앞 4자리만 노출
		masked := cfg.DeviceToken
		if len(masked) > 4 {
			masked = masked[:4] + "..."
		}
		logging.Debug("Authorization 헤더 추가: Bearer %s", masked)
		req.Header.Set("Authorization", "Bearer "+cfg.DeviceToken)
	} else {
		logging.Warn("DeviceToken이 비어 있어 Authorization 헤더를 추가하지 않습니다.")
	}
	return client.Do(req)
}
