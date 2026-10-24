# 하위 단계 Eligibility Gate

이 문서는 복합 명령을 분해한 각 하위 단계가 사전 라우터(Rule / System 1)로 가도 되는지 판정하는 Eligibility Gate의 역할, 판정 기준, 동작 모드, 확인 방법을 설명합니다. 사전 라우터 자체의 동작과 환경변수는 [SYSTEM1_FAST_ROUTER.md](SYSTEM1_FAST_ROUTER.md)를 참고합니다.

## 1. 역할

Eligibility Gate는 TaskDecomposer가 나눈 각 Single Task가 **혼자서 완결되는지** 판정하는 관문입니다.

```text
요청 → 복합 명령 → Planner: TaskDecomposer → Single Task 1 … N
                                               │
                                               ▼
                                       Eligibility Gate ──(탈락)──────────────┐
                                               │ (통과)                        │
                                               ▼                               ▼
                                   Router (System 1 / Rule)              LLM (이전 단계 요약 참고)
                                   ├─ Direct_Skill
                                   ├─ Simple_Reply
                                   └─ LLM (확신 부족·범위 밖)
```

- **통과한 단계**는 사전 라우터가 처리합니다. 라우터는 확신도와 직접 실행 범위를 보고 Direct_Skill, Simple_Reply, LLM 중 하나를 고릅니다.
- **탈락한 단계**와 라우터가 확신하지 못한 단계는 LLM이 처리합니다. LLM은 "[이전 단계 요약]"을 함께 받으므로 앞 단계 결과를 참고할 수 있습니다.

### 1.1 필요한 이유

분해된 단계라도 모두 혼자 실행할 수 있는 것은 아닙니다. 실기 로그의 "카메라 이미지 보내고 분석해줘"는 다음 3단계로 분해됐습니다.

| 단계 | 판정 | 이유 |
|---|---|---|
| 1. 카메라로 현재 장면의 이미지를 촬영하세요. | 통과 → 라우터 | 혼자 완결됩니다. 직접 실행 후보입니다. |
| 2. 촬영한 카메라 이미지를 전송하세요. | 탈락 → LLM | "촬영한"이 1단계 결과를 가리킵니다. |
| 3. 전송한 카메라 이미지를 분석하세요. | 탈락 → LLM | "전송한"이 2단계 결과를 가리킵니다. |

관문 없이 2단계를 라우터로 보내면, 이전 결과를 모르는 System 1이 엉뚱한 스킬을 직접 실행할 수 있습니다. 반대로 관문이 없던 이전 구조에서는 1단계처럼 단순한 단계도 모두 LLM을 거쳐 단계마다 LLM 지연(라운드당 약 2~3초)이 쌓였습니다.

## 2. 판정 기준

관문은 보수적으로 판정합니다. 애매하면 LLM으로 보냅니다(안전한 방향).

| 탈락 사유 (`reasons`) | 의미 | 예 |
|---|---|---|
| `depends_on_previous` | 이전 단계 결과를 가리키는 표현이 있습니다. | "**촬영한** 이미지를 전송해", "**찾은** 컵을 집어", "**방금** 저장한 위치로 가", "**그거** 다시 해줘" |
| `conditional` | 조건이나 판단이 필요합니다. | "배터리가 20% **이하면** 충전대로 가", "사람이 **보이면** 멈춰", "**만약** 문이 닫혀 있으면", "도착**할 때** 알려줘" |
| `compound` | 분해 후에도 여러 동작이 남아 있습니다(`looks_compound`). | "주방으로 **가서** 사진 찍어" |
| `empty` | 빈 단계입니다. | — |

"화면", "정면", "표면"처럼 명사에 쓰인 '면'은 조건으로 보지 않도록 조건 표현의 어간을 한정합니다.

관문이 다루지 않는 기준은 사전 라우터가 판단합니다.

| 기준 | 담당 |
|---|---|
| 인자가 필요한 스킬(거리·좌표·새 장소 이름·검색어) | 라우터의 직접 실행 범위(`SYSTEM1_SCOPE`: 인자 없는 read 스킬, 기억된 장소로의 `navigate_to`) |
| 확신도 | 라우터 임계값(`SYSTEM1_CONF_THRESHOLDS_JSON`) |
| 동작 요청에 조회 스킬로 답하는 오응답 | 라우터의 `needs_motion` 가드 |
| 안전 | 직접 실행에도 LLM 경로와 같은 주행 안전 규칙(`ensure_navigation_safety`)과 스킬 정책을 적용합니다. |

추가로, **백그라운드 직접 실행**(예: 규칙 라우터의 컵 집기)은 단계 경로에서 쓰지 않습니다. 시작 즉시 성공으로 보고되어 다음 단계(예: 이동)가 동작이 끝나기 전에 시작되기 때문입니다.

## 3. 동작 모드

`SYSTEM1_STEP_ROUTING` 환경변수로 정합니다. 기존 System 1 도입과 같은 순서(그림자 → 전환)로 적용합니다.

| 값 | 동작 |
|---|---|
| `off` | 관문을 쓰지 않습니다. 모든 단계를 LLM이 처리합니다(이전 동작). |
| `shadow` (기본) | 관문 판정과 라우터 결정을 로그로만 남기고, 실행은 LLM이 합니다. 동작 변화가 없습니다. |
| `on` | 관문을 통과하고 라우터가 Direct_Skill 또는 Simple_Reply를 고른 단계는 LLM 없이 처리합니다. |

- 사전 라우터는 `SYSTEM1_ROUTER` 설정을 그대로 씁니다. `rule`이면 규칙 라우터, `laya`면 Laya가 단계를 판단합니다.
- `on` 모드에서 직접 실행이 실패하면 그 기록을 남기고 같은 단계를 LLM으로 다시 처리합니다.
- 라우터 호출이 실패해도(예외) 해당 단계는 LLM으로 처리합니다.

설정 예시입니다.

```dotenv
SYSTEM1_STEP_ROUTING=shadow   # 처음에는 판정만 기록
# SYSTEM1_STEP_ROUTING=on     # 그림자 기록 확인 후 전환
```

## 4. 확인 방법

기동 로그에 모드가 표시됩니다.

```text
System1 step routing: mode=shadow
```

복합 명령의 각 단계마다 판정 로그가 남습니다.

```text
System1 step gate: {"mode": "shadow", "step_index": 1, "step": "카메라로 현재 장면의 이미지를 촬영하세요.", "eligible": true, "reasons": [], "route": {"kind": "direct_skill", "skill": "capture_camera_image", ...}, "would": "direct_skill", "action": "llm", "error": null}
System1 step gate: {"mode": "shadow", "step_index": 2, "step": "촬영한 카메라 이미지를 전송하세요.", "eligible": false, "reasons": ["depends_on_previous"], "route": null, "would": "llm", "action": "llm", "error": null}
```

| 필드 | 의미 |
|---|---|
| `eligible`, `reasons` | 관문 통과 여부와 탈락 사유 |
| `route` | 라우터 결정(통과한 단계만). `source`는 실제 판단한 라우터(rule / laya) |
| `would` | `on` 모드였다면 했을 처리(direct_skill / simple_reply / llm) |
| `action` | 실제로 한 처리. `shadow`에서는 항상 `llm` |
| `error` | 라우터 호출 오류 |

`shadow` 기록에서 `would`가 `direct_skill`인 단계가 기대한 스킬을 골랐는지 확인한 뒤 `on`으로 전환합니다. 직접 실행된 단계는 `Direct skill routing: <skill>` 로그가 함께 남습니다.

## 5. 구현 위치

| 파일 | 내용 |
|---|---|
| `src/robo_claw_agent/robo_claw_agent/agent_node/eligibility_gate.py` | 판정 규칙(`check_step_eligibility`)과 모드 파싱(`step_routing_mode`). ROS 의존 없는 순수 모듈 |
| `src/robo_claw_agent/robo_claw_agent/agent_node/task_planner.py` | `TaskDecomposer._try_step_fast_path`: 단계마다 관문 → 라우터 → 처리, 판정 로그 |
| `src/robo_claw_agent/robo_claw_agent/agent_node/execution.py` | `_execute_direct_route`: 최상위 명령과 하위 단계가 같은 직접 실행 규칙(주행 안전 규칙, 상태 갱신, 결과 이미지 전송)을 공유 |
| `src/robo_claw_agent/robo_claw_agent/agent_node/node.py` | 기동 시 `SYSTEM1_STEP_ROUTING` 읽기 |
| `src/robo_claw_agent/tests/test_eligibility_gate.py` | 판정 규칙(실기 분해 단계 포함)과 모드별 TaskDecomposer 동작 테스트 |

## 6. 제약 사항

- 판정은 한국어 표현 규칙에 기반합니다. 규칙에 없는 지시어나 조건 표현은 통과될 수 있으며, 이때는 라우터의 확신도·범위·가드가 다음 방어선입니다.
- 분해기(LLM)가 단계 문장을 어떻게 쓰느냐에 따라 판정이 달라집니다. "그 이미지를 보내" 대신 "카메라 이미지를 보내"로 쓰면 통과될 수 있으므로, `shadow` 기록으로 실제 분해 문장을 확인합니다.
- AI Config Server 웹 설정에는 아직 `SYSTEM1_STEP_ROUTING` 필드가 없습니다. 웹 설정으로 관리하려면 서버에 필드를 추가해야 하며, 그 전에는 기본값(`shadow`)이 적용됩니다.
