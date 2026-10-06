package cmd

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strconv"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
)

// DoctorCmd validates the server-provided runtime envelope without launching ROS.
func DoctorCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "config-doctor [robot_name] [environment]",
		Short: "원격 runtime 설정의 schema와 핵심 값을 점검합니다.",
		Run: func(ctx *wcli.Context) error {
			if len(ctx.Args) < 2 {
				return fmt.Errorf("로봇 이름과 환경명을 입력해야 합니다. 예: robo_claw_cli config-doctor butler office")
			}
			baseURL, err := serverBaseURL()
			if err != nil {
				return err
			}
			query := url.Values{"robot_name": {ctx.Args[0]}, "environment": {ctx.Args[1]}}
			requestURL := fmt.Sprintf("%s/api/v2/device/runtime-config?%s", baseURL, query.Encode())
			resp, err := doGetRequest(ctx, &http.Client{}, requestURL)
			if err != nil {
				return err
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				return fmt.Errorf("runtime 설정 조회 실패: HTTP %d", resp.StatusCode)
			}

			var envelope struct {
				SchemaVersion string                 `json:"schema_version"`
				Revision      string                 `json:"config_revision"`
				Config        map[string]interface{} `json:"config"`
			}
			if err := json.NewDecoder(resp.Body).Decode(&envelope); err != nil {
				return fmt.Errorf("runtime 설정 JSON 파싱 실패: %w", err)
			}
			if err := validateRuntimeManifest(envelope.SchemaVersion, envelope.Revision, envelope.Config); err != nil {
				return err
			}
			logging.Info("runtime 설정 정상: schema=%s revision=%s provider=%v model=%v ros_domain_id=%v", envelope.SchemaVersion, envelope.Revision, envelope.Config["llm_provider"], envelope.Config["llm_model"], envelope.Config["ros_domain_id"])
			return nil
		},
	}
}

func validateRuntimeManifest(schemaVersion, revision string, config map[string]interface{}) error {
	if schemaVersion != "2.0" {
		return fmt.Errorf("지원하지 않는 runtime schema: %s", schemaVersion)
	}
	if revision == "" || config == nil {
		return fmt.Errorf("runtime manifest의 revision 또는 config가 비어 있습니다")
	}
	provider, ok := config["llm_provider"].(string)
	if !ok || provider == "" {
		return fmt.Errorf("llm_provider가 없습니다")
	}
	if model, ok := config["llm_model"].(string); !ok || model == "" {
		return fmt.Errorf("llm_model이 없습니다")
	}
	if domain, ok := config["ros_domain_id"]; ok {
		if number, ok := domain.(float64); ok && (number < 0 || number > 232 || number != float64(int(number))) {
			return fmt.Errorf("ros_domain_id가 올바르지 않습니다: %s", strconv.FormatFloat(number, 'f', -1, 64))
		}
	}
	return nil
}
