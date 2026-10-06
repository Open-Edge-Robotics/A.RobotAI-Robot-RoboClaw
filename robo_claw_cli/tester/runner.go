package tester

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/wkqco33/wcli/logging"
	"github.com/wkqco33/wcli/rich"
)

// RunScenario 로드된 Scenario를 대상으로 지정된 TestContext를 사용하여 순차 실행하고 결과를 반환합니다.
func RunScenario(tctx *TestContext, scenario *Scenario) []TestStepResult {
	var results []TestStepResult

	for _, tc := range scenario.TestCases {
		if !tc.Enabled {
			results = append(results, TestStepResult{
				Step:   tc.Step,
				Name:   tc.Name,
				Status: "SKIP",
				Detail: "비활성화 처리됨",
			})
			continue
		}

		handler, ok := HandlerRegistry[tc.Type]
		if !ok {
			results = append(results, TestStepResult{
				Step:   tc.Step,
				Name:   tc.Name,
				Status: "FAIL",
				Detail: fmt.Sprintf("지원되지 않는 테스트 타입: %s", tc.Type),
			})
			logging.Error("지원되지 않는 테스트 타입 [%s] (TestCase ID: %s)", tc.Type, tc.ID)
			rich.Println("[red]❌ %s (%s): FAIL[/red] (지원되지 않는 테스트 타입)", tc.Step, tc.Name)
			continue
		}

		// 개별 테스트 실행
		logging.Info("테스트 케이스 실행 시작: %s (ID: %s, Type: %s)", tc.Name, tc.ID, tc.Type)
		res := handler(tctx, &tc)
		logging.Info("테스트 케이스 실행 완료: %s (ID: %s, 결과: %s)", tc.Name, tc.ID, res.Status)

		results = append(results, res)
	}

	return results
}

// LoadScenarioFile 외부 시나리오 JSON 파일을 파싱하여 Scenario 구조체를 반환합니다.
func LoadScenarioFile(filePath string) (*Scenario, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("시나리오 파일 읽기 실패: %w", err)
	}

	var scenario Scenario
	if err := json.Unmarshal(data, &scenario); err != nil {
		return nil, fmt.Errorf("시나리오 JSON 파싱 실패: %w", err)
	}

	return &scenario, nil
}
