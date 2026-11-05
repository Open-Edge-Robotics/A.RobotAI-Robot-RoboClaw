# 사전 라우터의 구현 구조

이 문서는 규칙 라우터와 Laya 라우터의 책임, 결과 계약과 장애 폴백을 설명합니다.
설치·환경변수·평가 명령은 [System 1 사용 가이드](SYSTEM1_FAST_ROUTER.md)를 기준으로 합니다.

## 판단과 실행 분리

사전 라우터는 LLM planner 앞에서 `direct_skill`, `simple_reply`, `llm` 중 하나를 반환합니다.
라우터 자체가 로봇 명령을 실행하거나 복합 작업을 분해하지 않습니다.
실행 계층이 결과를 소비하고 입력·정책·주행 안전 검사를 적용합니다.

| 구성 | 구현 | 책임 |
| --- | --- | --- |
| 결과 계약 | `fast_router.py:RouteDecision` | `kind`, `skill`, `params`, `background`, `reply`, `intent`, `confidence`, `source`, `fallback`, `latency_ms`, `hint`를 보관합니다. |
| 규칙 라우터 | `fast_router.py:RuleRouter` | 직접 스킬 규칙과 단순 대화 패턴을 판정합니다. |
| Laya 클라이언트 | `system1_router.py:SystemOneClient` | `/v1/systemone` HTTP 요청과 인증·timeout을 처리합니다. |
| Laya 해석 | `system1_router.py:SystemOneRouter` | 분류 결과의 확신도·scope·모호성·이동 필요 여부를 검사합니다. |
| 선택과 폴백 | `system1_router.py:SelectedRouter` | 선택한 라우터를 호출하고 장애 시 규칙 라우터로 대체합니다. |
| 설정 | `system1_router.py:System1Config` | `SYSTEM1_*` 환경변수를 읽습니다. |

위 경로는 `src/robo_claw_agent/robo_claw_agent/agent_node/`를 기준으로 합니다.
실제 인터페이스는 `async decide(node, instruction) -> RouteDecision`입니다.
판단 불가는 `None` 대신 `kind="llm"`으로 표현합니다.

## 배타 선택과 장애 처리

기본 `SYSTEM1_ROUTER=rule`이며, `laya`를 선택하면 정상 요청에 규칙 라우터를 먼저 실행하지
않습니다. timeout·HTTP 오류·열린 circuit breaker 등 장애일 때 규칙 경로로 폴백합니다.
저신뢰·범위 밖·복합 요청은 장애가 아니므로 System 2로 넘깁니다.

Circuit breaker의 코드 기본값은 연속 실패 3회·cooldown 30초입니다.
`SYSTEM1_SHADOW=true`는 다른 라우터도 백그라운드에서 평가하지만 실행 결정은 바꾸지 않습니다.
`SYSTEM1_ROUTER=rule`로 재기동하면 수동 롤백합니다.

## 직접 실행 범위

`SYSTEM1_SCOPE=readonly`는 필수 인자 없는 공개 read 스킬을 후보로 삼습니다.
`navigation`은 기억된 장소 이름으로 `navigate_to`를 선택할 수 있게 확장합니다.
숫자·자유 텍스트 인자가 필요한 일반 스킬은 분류 결과만으로 임의 실행하지 않습니다.

복합 명령은 Laya 질의를 생략해 System 2로 넘깁니다. 기본 네이티브 도구 호출 경로는
원문을 하나의 도구 대화에서 처리하며, JSON 경로에서만 활성화된 분해기를 사용합니다.
하위 단계 라우팅은 [Eligibility Gate](SYSTEM1_ELIGIBILITY_GATE.md)를 참고합니다.
JSON 경로의 출력 교정은 네이티브 도구 선택에 강제로 적용하지 않습니다.

## 설정 전달과 배포

공통 `SYSTEM1_*` 설정은 vendored contract에 등록되어 있으며 AI Config Server 프로필의
배포 `.env`를 통해 전달합니다. 로컬 `rclaw run/sim`은 이 값을 launch 매핑으로 전달하지
않으므로 셸에서 export합니다. Docker 스크립트는 `.env`를 `--env-file`로 전달합니다.
내장 Laya 서버·GPU·캐시 설정은 [배포 가이드](SYSTEM1_LAYA_DOCKER.md)를 참고합니다.

Laya는 자체 호스팅하는 분류 모델입니다. 특정 체크포인트의 정확도·지연·메모리 사용량은
배포 하드웨어·데이터에 따라 달라지므로 시드 케이스와 현장 케이스로 평가합니다.
fine-tune·confidence calibration은 이 저장소가 자동 수행하는 런타임 기능이 아닙니다.

## 관측

`RouteDecision.trace_metadata()`는 `route.kind`, `route.source`, `route.fallback`,
`route.latency_ms`와 값이 있는 `route.intent`, `route.confidence`, `route.skill`,
`route.hint`를 기록합니다. `source=laya`만으로 실제 서버 질의 여부를 판정하지 않습니다.
기록과 평가 방법은 [LangSmith 가이드](LANGSMITH_INTEGRATION.md)를 참고합니다.
