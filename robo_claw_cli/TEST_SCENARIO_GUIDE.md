# 📋 RoboClaw 테스트 시나리오 작성 가이드

이 문서는 `robo_claw_cli` 자동화 테스트 커맨드([test.go](file:///home/seoyc/Workspace/ros/robo_claw/robo_claw_cli/cmd/test.go)) 실행 시 사용할 수 있는 사용자 정의 테스트 시나리오 JSON 파일의 작성 및 구동 가이드입니다.

---

## 1. 시나리오 파일 구조

시나리오 파일은 JSON 형식으로 작성하며, 전체 시나리오를 설명하는 메타데이터와 개별 테스트 케이스의 배열로 구성됩니다. 기본 템플릿은 [default_scenario.json](file:///home/seoyc/Workspace/ros/robo_claw/robo_claw_cli/default_scenario.json) 파일을 참고하십시오.

```json
{
  "name": "시나리오 이름 (예: 모바일 로봇 검증 시나리오)",
  "description": "시나리오에 대한 설명",
  "test_cases": [
    {
      "id": "tc_01",
      "name": "테스트 케이스 이름",
      "phase": "대단계 분류 (예: Phase 1)",
      "type": "테스트 유형",
      "timeout_ms": 5000,
      "enabled": true,
      "params": {
        "key": "value"
      }
    }
  ]
}
```

### 1.1. 테스트 케이스 주요 필드 설명
자세한 Go 데이터 구조 정의는 [types.go](file:///home/seoyc/Workspace/ros/robo_claw/robo_claw_cli/tester/types.go#L20-L28) 파일을 참고하십시오.

| 필드명 | 타입 | 설명 |
| :--- | :--- | :--- |
| `id` | string | 테스트 케이스 고유 식별자 |
| `name` | string | 콘솔 및 보고서에 표시될 테스트 항목 이름 |
| `phase` | string | 테스트 대단계 구분 (예: `Phase 1`, `Phase 2` 등) |
| `type` | string | 실행할 테스트 핸들러 유형 (**지원되는 Type** 절 참고) |
| `timeout_ms`| int | 개별 gRPC 호출 타임아웃 (밀리초 단위) |
| `enabled` | bool | 해당 테스트 케이스의 실행 여부 (`false`인 경우 SKIP 처리됨) |
| `params` | object | 각 테스트 유형(`type`)별 비즈니스 검증에 필요한 입력 인자 맵 (선택) |

---

## 2. 지원되는 테스트 유형 (Type) 및 매개변수 (Params)

테스트 유형별 구체적인 실행 코드는 [handlers.go](file:///home/seoyc/Workspace/ros/robo_claw/robo_claw_cli/tester/handlers.go) 파일에서 처리하고 있습니다.

### 2.1. `ping`
* **설명**: 로봇 gRPC Ping 연결성 테스트 (실패 시 메신저 채널 SendCommand 대체 통신)
* **Params**: 없음

### 2.2. `robot_info`
* **설명**: 로봇 정보 조회 API 호출 및 프로필과 비교 정합성 테스트 (RosGrpc Only)
* **Params**: 없음

### 2.3. `battery`
* **설명**: 로봇 배터리 상태 점검 (10% 미만 시 WARNING 분류, RosGrpc 비활성 시 메신저 채팅 대체)
* **Params**: 없음

### 2.4. `camera`
* **설명**: 카메라 이미지 수신 및 디코딩 검증 (RosGrpc 비활성 시 메신저 파일 다운로드 API 대체)
* **Params**: 없음

### 2.5. `map`
* **설명**: SLAM 맵 데이터 및 격자 정보 수신 검증 (RosGrpc Only)
* **Params**: 없음

### 2.6. `camera_analysis`
* **설명**: 메신저 채널을 통해 카메라 이미지 분석 인공지능 스킬 및 파일 다운로드 연동을 종합 검증
* **Params**: 없음

### 2.7. `map_analysis`
* **설명**: 메신저 채널을 통해 지도 이미지 분석 인공지능 스킬 및 파일 다운로드 연동을 종합 검증
* **Params**: 없음

### 2.8. `navigation`
* **설명**: 가드레일 속도 제한 검증 또는 자연어 주행 스킬 기동 테스트
* **Params**:
  * `mode`: 주행 테스트 성격 결정 (`"guardrail"` 또는 `"motion"`)
  * `speed` (float64): `"guardrail"` 모드 시 속도 제한 기준 (m/s, 미지정 시 설정값 캐시 자동 사용)
  * `command` (string): `"motion"` 모드 시 LLM 에이전트에 내릴 로봇 이동 지시 자연어 텍스트

### 2.9. `manipulation`
* **설명**: 로봇 매니퓰레이터 관절 구동 및 제어 동작 테스트 (RosGrpc 또는 메신저 자연어 제어)
* **Params**:
  * `command` (string): 메신저 챗 모드 시 매니퓰레이터에 하달할 관절 기동 자연어 텍스트

### 2.10. `persona`
* **설명**: 메신저 채널 대화 및 `ROBOT.md` 소울 프로필 키워드 대조를 통한 LLM 에이전트 정체성(성격) 검증
* **Params**:
  * `prompt` (string): 에이전트에게 전달할 정체성 질문 텍스트

### 2.11. `custom`
* **설명**: 메신저 채널을 통해 사용자가 임의의 자연어 프롬프트를 전송하고, 그 응답에 대해 동적 성공 판별 및 키워드 매칭 여부를 검증
* **Params**:
  * `prompt` 또는 `command` (string): 메신저 채널을 통해 로봇에게 보낼 질문 또는 명령 텍스트 (필수)
  * `expect_success` (bool): 로봇 에이전트의 처리 성공 여부(`Success` 필드) 기대값 (기본값: `true`)
  * `expected_keywords` (string 또는 []string): 응답 메시지에 포함되어야 하는 키워드 목록 (선택)
  * `fail_keywords` (string 또는 []string): 응답 메시지에 포함되면 안 되는 키워드 목록 (선택)

---

## 3. 커스텀 시나리오 구동 방법

작성 완료한 시나리오 JSON 파일을 사용해 테스트를 기동하려면 `--scenario-file` 플래그에 파일 경로를 설정하여 전달합니다.

```bash
# 커스텀 시나리오 구동 예제 (robo_claw_cli가 빌드되어 있는 경로에서 실행)
./robo_claw_cli test -r butler -e office --scenario-file ./my_custom_scenario.json
```
