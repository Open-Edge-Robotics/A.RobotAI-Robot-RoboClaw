package tester

// TestStepResult 개별 테스트 스텝의 수행 결과
type TestStepResult struct {
	Step      string `json:"step"`
	Name      string `json:"name"`
	Status    string `json:"status"`
	Detail    string `json:"detail"`
	ImagePath string `json:"image_path,omitempty"`
}

// RobotLimits ROBOT_LIMITS.json 파싱 구조체
type RobotLimits struct {
	Navigation struct {
		MaxLinearVelocityMps float64 `json:"max_linear_velocity_mps"`
	} `json:"navigation"`
	MaxLinearVelocity float64 `json:"max_linear_velocity"`
}

// TestCase 동적으로 파싱되고 매핑 실행될 개별 테스트 케이스 정의 구조체
type TestCase struct {
	ID        string         `json:"id"`
	Name      string         `json:"name"`
	Step      string         `json:"step"`
	Type      string         `json:"type"` // "ping", "robot_info", "battery", "camera", "map", "camera_analysis", "map_analysis", "navigation", "manipulation", "persona", "custom"
	TimeoutMs int            `json:"timeout_ms"`
	Enabled   bool           `json:"enabled"`
	Params    map[string]any `json:"params,omitempty"`
}

// Scenario 여러 테스트 케이스로 구성된 테스트 시나리오 구조체
type Scenario struct {
	Name        string     `json:"name"`
	Description string     `json:"description"`
	TestCases   []TestCase `json:"test_cases"`
}

// GetDefaultScenario 기본 내장 테스트 시나리오를 구성하여 반환합니다.
func GetDefaultScenario(runNavigation, runManipulation bool, testSpeed float64) Scenario {
	return Scenario{
		Name:        "RoboClaw 기본 테스트 시나리오",
		Description: "연결성 진단부터 AI 페르소나 매핑까지의 표준 자동화 테스트 세트",
		TestCases: []TestCase{
			// Phase 1
			{
				ID:        "tc_ping",
				Name:      "gRPC Ping 연결성",
				Step:      "Step 1",
				Type:      "ping",
				TimeoutMs: 3000,
				Enabled:   true,
			},
			{
				ID:        "tc_robot_info",
				Name:      "로봇 정보 조회 및 정합성",
				Step:      "Step 1",
				Type:      "robot_info",
				TimeoutMs: 3000,
				Enabled:   true,
			},
			{
				ID:        "tc_battery",
				Name:      "배터리 상태 점검",
				Step:      "Step 1",
				Type:      "battery",
				TimeoutMs: 10000,
				Enabled:   true,
			},
			// Phase 2
			{
				ID:        "tc_camera",
				Name:      "카메라 이미지 획득",
				Step:      "Step 2",
				Type:      "camera",
				TimeoutMs: 5000,
				Enabled:   true,
			},
			{
				ID:        "tc_slam_map",
				Name:      "SLAM 지도 맵 수신",
				Step:      "Step 2",
				Type:      "map",
				TimeoutMs: 5000,
				Enabled:   true,
			},
			{
				ID:        "tc_camera_analysis",
				Name:      "카메라 이미지 분석 및 전달 스킬",
				Step:      "Step 2",
				Type:      "camera_analysis",
				TimeoutMs: 25000,
				Enabled:   true,
			},
			{
				ID:        "tc_map_analysis",
				Name:      "맵 이미지 분석 및 전달 스킬",
				Step:      "Step 2",
				Type:      "map_analysis",
				TimeoutMs: 25000,
				Enabled:   true,
			},
			// Phase 3
			{
				ID:        "tc_guardrail",
				Name:      "물리 가드레일 속도 제약",
				Step:      "Step 3",
				Type:      "navigation",
				TimeoutMs: 15000,
				Enabled:   runNavigation,
				Params: map[string]any{
					"mode":  "guardrail",
					"speed": testSpeed,
				},
			},
			{
				ID:        "tc_motion_chat",
				Name:      "자연어 기반 주행 스킬 기동",
				Step:      "Step 3",
				Type:      "navigation",
				TimeoutMs: 15000,
				Enabled:   runNavigation,
				Params: map[string]any{
					"mode": "motion",
				},
			},
			// Phase 3-2
			{
				ID:        "tc_joint_control",
				Name:      "매니퓰레이션 관절 구동 검증",
				Step:      "Step 3",
				Type:      "manipulation",
				TimeoutMs: 15000,
				Enabled:   runManipulation,
			},
			// Phase 4
			{
				ID:        "tc_persona",
				Name:      "LLM 페르소나 및 응답 지능",
				Step:      "Step 4",
				Type:      "persona",
				TimeoutMs: 15000,
				Enabled:   true,
				Params: map[string]any{
					"prompt": "너의 정체성과 성격에 대해 한 문장으로 답변해줘.",
				},
			},
		},
	}
}
