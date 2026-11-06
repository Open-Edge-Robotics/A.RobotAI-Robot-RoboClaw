# 사전 라우터와 복합 요청 처리 흐름

이 문서는 사용자 요청이 규칙·Laya 사전 라우터와 System 2를 거쳐 실행되는 경로를 설명합니다.
기본 네이티브 도구 호출 경로와 선택적인 JSON 계획·분해 경로를 구분합니다.

## 사전 라우터

`SYSTEM1_ROUTER=rule`이면 `RuleRouter`, `laya`이면 `SystemOneRouter`가 판단합니다.
둘을 직렬로 실행하지 않으며 Laya 장애 시에만 규칙 라우터로 폴백합니다.
결과는 `direct_skill`, `simple_reply`, `llm` 중 하나입니다.

복합 명령은 단일 스킬로 축약해 나머지 요청을 잃지 않도록 System 2로 넘깁니다.
Laya의 복합 명령 가드가 작동하면 `source=laya`여도 실제 서버 질의는 하지 않습니다.
명시적인 정리 시연용 직접 경로는 `direct_cleanup_demo_skill`의 별도 조건을 따릅니다.

## 기본 네이티브 도구 호출 경로

`llm_tool_calling=true`이고 provider가 이를 지원하면 복합 요청도 원문을 하나의
도구 대화에 전달합니다. 분해기·하위 단계 Eligibility Gate를 호출하지 않습니다.

```text
사용자 요청 → 문맥 수집 → Rule 또는 Laya → llm
  → API tools와 원문을 전달
  → 모델의 tool_calls를 검증·실행
  → 호출 ID에 대응하는 tool 결과를 같은 대화에 추가
  → 후속 모델 요청 또는 최종 답변
```

Ollama에서는 같은 작업의 assistant `thinking`, `content`, `tool_calls` 원본을
후속 요청에 보존합니다. 사용자에게는 사고 과정이 아닌 사용자용 문장만 전달합니다.
입력 schema·스킬 정책·주행 안전 검사를 적용하며 모델의 선택을 키워드로 교체하지 않습니다.

## JSON 계획과 작업 분해 경로

`llm_tool_calling=false`이거나 provider가 네이티브 도구 호출을 지원하지 않으면
JSON 계획을 사용합니다. `enable_task_decomposition=true`이고 복합 명령일 때만
`TaskDecomposer`가 자연어 하위 지시문을 생성합니다.

```text
사용자 요청 → 문맥 수집 → Rule 또는 Laya → llm
  → JSON 경로 + 분해 활성 + 복합 명령 검사
  → TaskDecomposer의 단계 생성
  → 단계별 Eligibility Gate와 선택적 사전 라우팅
  → 처리되지 않은 단계는 LLM planner
  → 결과를 다음 단계 문맥에 전달
```

단계 수는 기본 최대 6개이며, 분해 실패 시 원문을 한 단계로 사용합니다.
각 단계는 기본 1회 재시도하고 계속 실패하면 이후 단계를 중단합니다.
`SYSTEM1_STEP_ROUTING=off|shadow|on`은 이 분해 경로에만 영향을 줍니다.
자세한 관문 기준은 [하위 단계 Eligibility Gate](SYSTEM1_ELIGIBILITY_GATE.md)를 참고합니다.

## 수건 정리 요청의 해석

“집안에서 널려있는 수건을 수거해서 바구니에 넣어줘” 같은 요청을 Laya가 직접 분해하거나
물리 실행하지 않습니다. System 2가 도구를 선택하며 실제 호출 결과는 모델·등록 스킬·
로봇 능력에 따라 달라집니다.

`tidy_home`은 폐기물·수건·쿠션·명확한 빨래 후보를 구분합니다. 실제 집기·배치는
해당 유형의 승인 보관 장소와 비추정 3D 목표, 집기부터 배치까지의 유효한 계획,
등록된 조작 능력이 모두 필요합니다. 조건이 없으면 확인 요청·위임·미처리로 보고합니다.
`cleanup_demo`는 고정 시연 모션 요청이며 물체 이동이나 실제 정리 완료를 뜻하지 않습니다.

## 구현과 운영 설정

| 경계 | 소스 |
| --- | --- |
| 최상위 라우팅·분해 진입 조건 | `agent_node/execution.py` |
| 규칙 라우터·결과 형식 | `agent_node/fast_router.py` |
| Laya 질의·장애 폴백 | `agent_node/system1_router.py` |
| 하위 지시문 분해·실행 | `agent_node/task_planner.py` |
| LLM 도구·JSON 실행 루프 | `agent_node/planner/llm_planner.py` |

경로는 `src/robo_claw_agent/robo_claw_agent/`를 기준으로 합니다.
실행 방식별 환경변수 전달은 [System 1 가이드](SYSTEM1_FAST_ROUTER.md),
네이티브 도구 호출 스위치는 [설정 가이드](CONFIGURATION_GUIDE.md)를 참고합니다.
