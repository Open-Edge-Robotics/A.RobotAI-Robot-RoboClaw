# System 1 Fast Router 설계 (Laya 기반)

- 작성일: 2026-10-02
- 상태: 설계 초안 v2 (구현 전) — 사전 라우팅을 규칙/Laya 배타 선택 구조로 변경
- 대상: `robo_claw_agent` planner 라우팅 계층

## 1. 배경과 목적

robo-claw의 `LLMPlanner.run_llm_planning_loop`(`agent_node/planner.py`)는 사용자 요청마다 LLM(ollama/openai/azure/anthropic)을 호출해 skill chain을 계획합니다. 약한 로컬 LLM(예: `gemma4:e4b`)의 오라우팅을 교정하려고, planner에는 규칙 기반 판단이 계속 추가되고 있습니다.

| 위치 | 역할 |
|---|---|
| `execution._SIMPLE_QUERY_PATTERNS` | 단순 질의 사전 필터 |
| `planner._is_likely_conversational` | 인사/자기소개 판정 |
| `planner._route_location_query`, `_known_place_in_text` | 위치 질의(정방향/역방향) 교정 |
| `planner._force_navigation_if_misrouted` | 이동 요청 오라우팅 교정 |
| `planner._extract_save_place` | "…로 저장/기억" → `rag_add` 강제 |
| `direct_skills.check_direct_skill` | 컵 집기/지도 캡처/카메라/상대 이동 직접 실행 |

이 판단들은 대부분 **닫힌 선택지 중 하나를 고르는 분류 문제**입니다. 정규식 열거로는 끝이 없고(`_is_likely_conversational` docstring 참고), 매번 큰 LLM 왕복을 하면 지연시간과 비용이 큽니다.

**목표:** 빠른 분류 모델(System 1)이 판단을 먼저 하고, 확신이 없거나 위험한 요청만 기존 LLM planner(System 2)로 넘기는 이중 구조를 만듭니다.

### 1.1 기존 규칙의 분류

위 규칙들은 호출 시점과 역할이 서로 달라서, 두 그룹으로 나눠 다룹니다.

| 그룹 | 규칙 | 호출 위치 | 역할 | 이 설계에서의 처리 |
|---|---|---|---|---|
| **A. 사전 라우팅** | `check_direct_skill`, `_SIMPLE_QUERY_PATTERNS` | `execution.py:477`, `execution.py:546` (LLM 호출 전) | 경로 결정 | **Laya와 배타적으로 교체** |
| **B. LLM 출력 교정** | `_preserve_user_navigation_target`, `_force_navigation_if_misrouted`, `_route_location_query` | `planner.py:764`, `planner.py:883~894` (LLM 응답 후) | LLM이 만든 chain 교정 | **항상 유지** |
| **B'. 가드/추출** | `_is_likely_conversational`, `_extract_save_place`, `nav_safety`, `sensor_health` | `planner.py:863`, `planner.py:1000` 등 | 대화문 action 차단, 자유 텍스트 인자 추출, 안전 검사 | **항상 유지** |

B와 B'는 Laya의 경쟁 대상이 아닙니다. System 2(약한 로컬 LLM)의 오라우팅을 막는 안전망입니다. Laya가 System 2로 넘긴 요청도 같은 LLM이 처리하므로 이 안전망은 계속 필요합니다. 또한 Laya는 자유 텍스트를 추출할 수 없습니다.

## 2. 모델 선정

### 2.1 TypeSafe AI Jev: 보류

Jev는 System One 모델 개념을 처음 제안한 모델입니다. state와 typed question(`choice`/`score`/`noul`)을 받아, 확률이 붙은 typed 결과를 한 번에 반환합니다. 그러나 아래 이유로 **현재는 사용하지 않습니다.**

- 유료 API이고 billing 정보 등록이 필요합니다. 현재 결제 진행이 어렵습니다.
- 클라우드 전용이라 로봇 상태가 사외로 전송됩니다. 네트워크 가용성에도 의존합니다.
- 2026-09-22부터 신규 가입이 중단되었습니다.

### 2.2 Laya (convaiinnovations/laya): 채택

[Laya](https://huggingface.co/convaiinnovations/laya)는 Jev와 같은 System 1 개념을 따르는 **오픈 웨이트 모델(Apache 2.0)**입니다.

| 항목 | 내용 |
|---|---|
| 구조 | encoder backbone + decision head(transformer 2층, option-marker scorer, act/escalate head), 비자기회귀 단일 forward |
| 체크포인트 | `laya`: ModernBERT-large, 421M, 영어, 512 tok<br>`laya-multilingual`: mmBERT-base, 322M, 100+ 언어, 1,024 tok (최대 8,192)<br>`laya-typed-decisions`: 도메인 fine-tune 버전 |
| 입출력 | state(text/JSON) + questions(`choice`/`score`/`noul`) → answers + confidence + routing metadata |
| API | `laya-serve`가 `POST /v1/systemone`을 제공합니다(Jev와 같은 경로). |
| 지연 | T4 GPU 기준 단일 질문 약 33~40ms, 10문항 배치 72~158ms (Jev p50은 236~276ms) |
| 메모리 | 약 650MB(multilingual) ~ 810MB(영어) |
| 비용 | 자체 호스팅이므로 무료 |

**robo-claw 관점의 장점**
- 사내·로봇 내부 배포가 가능해 데이터가 외부로 나가지 않습니다.
- 지연시간이 수십 ms 수준이라 planner 앞단에 두어도 체감 지연이 거의 없습니다.
- Jev와 요청 스키마와 엔드포인트가 같아, 나중에 Jev로 바꾸거나 함께 쓰기 쉽습니다.

**반드시 반영해야 할 한계** (모델 카드 기준)

| 한계 | 설계 반영 |
|---|---|
| 기본 체크포인트는 zero-shot typed-decision 정확도가 0.362로, 다수 클래스 기준선(0.461)보다 낮음 | **fine-tune 필수**(fine-tune 시 0.766). 3.5절의 데이터 파이프라인 |
| 루트 체크포인트는 영어 전용 | 한국어 명령이므로 **`laya-multilingual` 사용** |
| 선택지가 많으면 약함(Banking77 77개 선택지에서 0.425) | 스킬 선택은 **계층형(coarse → fine)**, 장소 선택은 후보를 줄인 뒤 질의 |
| `score`(서열 점수)가 가장 약함 | `score`는 사용하지 않고 `choice`/`noul`만 사용 |
| `noul`이 state 대신 label 문구를 따라가는 경향 | 질문 문구를 중립적으로 작성하고, 회귀 테스트로 검증 |
| 출고 상태는 과신(over-confident), ECE 0.466 | **temperature scaling 보정 후 사용**(ECE 0.081) |
| CPU에서는 느림 | GPU 서버 또는 로봇 GPU에 배치. CPU-only 로봇은 원격 서버를 사용 |

## 3. 아키텍처

### 3.1 전체 흐름

사전 라우팅 계층(그룹 A)은 **RuleRouter와 LayaRouter 중 하나만** 동작합니다. 둘을 직렬로 연결하지 않습니다. 이렇게 하면 효과를 깔끔하게 비교할 수 있고, 플래그 하나로 롤백할 수 있습니다.

```
사용자 지시문
   │
   ▼
┌──────────── 사전 라우팅 (그룹 A, 배타 선택) ────────────┐
│ SYSTEM1_ROUTER=rule → [RuleRouter]  check_direct_skill / │
│                                     _SIMPLE_QUERY_PATTERNS│
│ SYSTEM1_ROUTER=laya → [LayaRouter]  /v1/systemone        │
│                       └ 장애(timeout/circuit open) 시     │
│                         해당 요청만 RuleRouter로 자동 대체 │
└──────────────────────────────────────────────────────────┘
   │
   ├─ conversational & conf≥θ          ──▶ 응답 생성(기존 LLM, planning 생략)
   ├─ single_skill(허용 범위) & conf≥θ   ──▶ 스킬 직접 실행
   ├─ action skill(허용 범위 밖)         ──▶ System 2 (Laya 결과는 route.hint로 기록)
   └─ multi_step / ambiguous / 저신뢰    ──▶ System 2
                                              │
                                              ▼
                     [LLMPlanner.run_llm_planning_loop]  (System 2)
                                              │
                                              ▼
              [그룹 B 출력 교정 + 그룹 B' 가드]  ← 라우터 모드와 무관하게 항상 on
   ※ nav_safety / sensor_health 검사는 모든 경로에서 항상 수행
```

**두 가지 롤백 경로**

| 종류 | 트리거 | 동작 |
|---|---|---|
| 성능 롤백 (수동) | 지표 악화를 사람이 판단 | `SYSTEM1_ROUTER=rule`로 변경. config server 설정 반영 후 재기동 |
| 장애 롤백 (자동) | Laya timeout, HTTP 오류, circuit breaker open | 해당 요청(또는 breaker가 열린 동안)만 RuleRouter로 처리. System 2로 바로 넘기지 않고 기존 동작을 보장 |

### 3.2 인터페이스 (`agent_node/fast_router.py`, 신규)

```python
class RouteDecision(TypedDict):
    intent: str              # conversational | single_skill | multi_step | location_query | save_place
    category: str | None     # navigation | perception | manipulation | system | ...
    skill: str | None
    target_place: str | None
    ambiguous: float         # noul 확률(보정 후)
    confidence: float        # 보정 후 confidence
    source: str              # "rule" | "laya" | "jev"
    latency_ms: float

class FastRouter(Protocol):
    async def decide(self, instruction: str, ctx: "RouterContext") -> RouteDecision | None:
        """판단 불가 시 None → System 2로 넘긴다."""
```

구현체와 선택 로직은 다음과 같습니다.
- `RuleRouter`: **그룹 A 규칙만** 이 인터페이스 뒤로 옮깁니다(`check_direct_skill`, `_SIMPLE_QUERY_PATTERNS`). 동작은 바꾸지 않습니다. 그룹 B/B'는 planner에 그대로 둡니다.
- `SystemOneRouter`: `/v1/systemone` HTTP 클라이언트입니다. `endpoint`만 바꾸면 Laya와 Jev를 모두 쓸 수 있습니다. 내부에 timeout과 circuit breaker를 둡니다.
- `build_router(params)`: `SYSTEM1_ROUTER` 값에 따라 **하나의** 라우터를 반환합니다. `laya`일 때는 장애 대체용으로만 `RuleRouter`를 보유합니다.

```python
class SelectedRouter:
    """배타 선택: primary 하나만 판단한다. fallback은 장애 시에만 쓴다."""

    def __init__(self, primary: FastRouter, fallback: FastRouter | None, shadow: FastRouter | None):
        ...

    async def decide(self, instruction, ctx):
        try:
            decision = await self.primary.decide(instruction, ctx)
        except System1Unavailable:          # timeout / HTTP 오류 / circuit open
            decision = await self.fallback.decide(instruction, ctx) if self.fallback else None
        if self.shadow:                      # 그림자: 실행에는 영향 없이 기록만
            schedule_shadow_record(self.shadow, instruction, ctx, decision)
        return decision
```

이전 초안의 `ChainRouter`(규칙 → Laya 직렬 연결)는 제거했습니다. 직렬로 연결하면 어떤 개선이 Laya 덕분인지 측정할 수 없고, 롤백 범위도 모호해지기 때문입니다.

#### 구현 위치 (브랜치 `jev-laya`)

| 파일 | 내용 |
|---|---|
| `agent_node/fast_router.py` | `RouteDecision`(`direct_skill` / `simple_reply` / `llm`), `RuleRouter`(그룹 A 규칙) |
| `agent_node/system1_router.py` | `System1Config`(환경변수), `SystemOneClient`(HTTP), `SystemOneRouter`, `CircuitBreaker`, `SelectedRouter`, `build_router` |
| `agent_node/execution.py` | `self._router.decide()` 결과만 소비 |
| `agent_node/node.py` | 기동 시 `build_router(System1Config.from_env())` |

실제 구현의 시그니처는 `decide(node, instruction) -> RouteDecision`입니다(위 의사 코드의 `RouterContext` 대신 node를 직접 받음). 판단할 수 없을 때는 `None` 대신 `kind="llm"`을 반환합니다. Laya 모드에서도 복합 명령(`looks_compound`)은 모델을 호출하지 않고 System 2로 넘깁니다. 단일 스킬로 축약하면 나머지 절이 사라지기 때문입니다.

### 3.3 질의 스키마

**state** (최소 정보만 넣습니다)

```json
{
  "instruction": "주방으로 가서 컵 좀 봐줘",
  "recent_turns": ["...", "..."],
  "current_place": "거실",
  "robot_state": "IDLE"
}
```

**1단계 질의** (한 번의 호출로 병렬 처리)

| key | type | 선택지 |
|---|---|---|
| `intent` | choice | conversational / single_skill / multi_step / location_query / save_place |
| `category` | choice | navigation / perception / manipulation / map / hri / system / cooperation (7개 내외) |
| `needs_motion` | noul | 로봇 이동이나 조작이 필요한가 |
| `ambiguous` | noul | 대상이나 목적지가 모호한가 |

**2단계 질의** (`single_skill`이고 category가 정해졌을 때만)

| key | type | 선택지 |
|---|---|---|
| `skill` | choice | 해당 category의 스킬만(10개 이하). `skill_manager` 메타데이터에서 동적 구성 |
| `target_place` | choice | 후보 장소(최대 10개) + `none` |

- 장소 후보는 RAG/memory의 등록 장소를 문자열 유사도로 미리 좁혀서 만듭니다(`_known_place_in_text` 재사용).
- 새 장소 이름(`save_place`) 같은 **자유 텍스트 인자는 Laya가 추출할 수 없습니다.** 기존 `_extract_save_place`나 LLM이 계속 담당합니다.

### 3.4 결정 규칙

| 조건 | 처리 |
|---|---|
| timeout, HTTP 오류, 서버 미기동 | **RuleRouter로 자동 대체**(circuit breaker 카운트 증가) |
| `ambiguous` ≥ θ_amb | System 2 |
| `intent=multi_step` | System 2 |
| `intent=conversational` & conf ≥ θ_conv | planning 생략, 응답만 생성 |
| `single_skill` & `SYSTEM1_SCOPE` 허용 범위 & conf ≥ θ | 스킬 직접 실행 |
| `_is_action_skill` (이동/조작) & 허용 범위 밖 | System 2. Laya의 intent/skill/target은 `route.hint`로 trace에 기록(planner 프롬프트 주입은 Step 8에서 검토) |
| `location_query` | System 2. 이후 그룹 B의 `_route_location_query`가 결정론적으로 교정 |
| `save_place` | System 2. 장소명 추출은 그룹 B'의 `_extract_save_place`가 담당 |

임계값(θ)은 intent별로 따로 두고, 그림자 모드 데이터로 정합니다(4장 Step 6).

### 3.5 학습 데이터 파이프라인 (fine-tune)

기본 체크포인트는 zero-shot 성능이 낮으므로 robo-claw 전용 fine-tune을 전제로 합니다.

1. **수집**
   - LangSmith 트레이스에서 `instruction`, 최종 실행 skill chain, 성공 여부를 추출합니다.
   - validation 시나리오(`validation/testcases`)와 `robo_talk.py` 로그를 추가합니다.
2. **라벨링**
   - 성공한 chain으로 `intent`/`category`/`skill`/`target_place` 라벨을 자동 생성합니다.
   - 규칙 교정(`_force_navigation_if_misrouted` 등)이 작동한 사례는 교정 후 결과를 정답으로 씁니다.
   - 실패 사례와 모호 사례는 사람이 검수합니다.
3. **증강:** 한국어 구어 변형(조사, 존댓말/반말, 오타)과 영어 혼용 문장을 추가합니다.
4. **분할:** robot/env 단위로 train/val/test를 나눠 장소 이름 누수를 막습니다.
5. **학습:** `laya-multilingual` 기반으로 fine-tune합니다(RLCD 또는 모델 카드가 안내하는 절차). 이어서 val 셋으로 temperature scaling을 수행합니다.
6. **평가:** 정확도, ECE, intent별 오실행률(false-act rate)을 봅니다. 기존 RuleRouter + LLM 결과와 비교합니다.

### 3.6 배포

| 항목 | 방침 |
|---|---|
| 실행 형태 | `laya-serve` HTTP 서버. 엣지 서버용 단독 이미지(`docker/laya/`) 또는 robo-claw 이미지 내장(`INSTALL_LAYA=true` + `SYSTEM1_LOCAL_SERVER=true`, launch가 함께 실행). 두 방식 모두 `docker/laya/install_laya.sh`로 설치하고 torch 인덱스로 CPU/CUDA를 고릅니다 |
| 위치 | 1순위: 엣지 GPU 서버(여러 로봇이 공유, 로봇 제어 자원과 분리)<br>2순위: 로봇 본체(Thor) 내장 — GPU가 없으면 CPU로 자동 전환, nice·스레드 상한으로 주행·모션 제어 보호<br>상세 절차: [SYSTEM1_FAST_ROUTER.md](SYSTEM1_FAST_ROUTER.md) 3장 |
| 모델 버전 | fine-tune 체크포인트를 사내 레지스트리에 버전 태그로 관리 |
| 설정 주입 | 에이전트 프로세스 환경변수(아래 표). 현재 AI Config Server는 `SYSTEM1_*`를 `.env`로 내려주지 않으므로, 실행 방식별 전달 방법은 [SYSTEM1_FAST_ROUTER.md](SYSTEM1_FAST_ROUTER.md) 4.5절을 따릅니다. 중앙 관리는 서버 필드 추가가 필요합니다 |
| 헬스체크 | 기동 시 `/v1/systemone` dry 질의. 실패하면 경고만 남기고 RuleRouter로 동작(장애 롤백과 같음) |

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `SYSTEM1_ROUTER` | `rule` | 사전 라우팅 선택: `rule` / `laya` (**배타**). `rule`이면 현재 동작과 동일 |
| `SYSTEM1_SHADOW` | `false` | `true`면 선택되지 않은 라우터도 백그라운드로 실행해 판단만 기록. 실행에는 영향 없음 |
| `SYSTEM1_SCOPE` | `readonly` | `laya` 모드에서 Laya 단독 실행을 허용하는 범위: `readonly`(인자 없는 read 스킬) → `navigation`(+ 기억된 장소로 `navigate_to`). 숫자·자유 텍스트 인자가 필요한 스킬은 Laya가 추출할 수 없어 범위에 넣지 않는다 |
| `SYSTEM1_ENDPOINT` | — | 예: `http://laya.internal:8000` |
| `SYSTEM1_PROVIDER` | `laya` | `laya` / `jev` (향후) |
| `SYSTEM1_TIMEOUT_MS` | `300` | 초과 시 System 2 |
| `SYSTEM1_CONF_THRESHOLDS_JSON` | — | intent별 θ |
| `SYSTEM1_API_KEY` | — | 외부 provider 사용 시에만 (secret, `config-effective`에서 마스킹) |
| `SYSTEM1_SKILLS` | — | Laya 후보 스킬 목록(쉼표 구분). 미설정 시 인자 없는 read 스킬 전체 |
| `SYSTEM1_MAX_OPTIONS` | `12` | choice 선택지 상한(고카디널리티 약점 대응) |
| `SYSTEM1_SHADOW_LOG` | — | 그림자 판단을 JSONL로 남길 경로(Step 6 분석용) |

### 3.7 관측

LangSmith 트레이스에 다음 메타데이터를 붙입니다(`tracing.py`의 traceable 래퍼 활용).
- `route.router`(rule/laya), `route.source`(실제 판단한 라우터, 장애 대체 시 rule), `route.fallback`(장애 대체 여부), `route.intent`, `route.confidence`, `route.escalated`, `route.latency_ms`, `route.scope`
- 그림자 모드에서는 선택되지 않은 라우터의 판단(`shadow_decision`)과 실제 실행 결과를 함께 기록해 일치율을 계산합니다.
- 그룹 B 교정이 작동한 횟수(`correction.applied`)도 기록합니다. Laya 모드에서 이 횟수가 줄면 System 2로 넘어가는 요청의 질이 좋아졌다는 신호입니다.

## 4. 단계별 실행 계획

| Step | 내용 | 산출물 / 완료 기준 |
|---|---|---|
| 1 | **기준선 측정.** LangSmith로 요청 유형 비율, planning 지연, 오라우팅 사례를 수집합니다. LangSmith 403 문제를 먼저 해결해야 합니다. | 기준선 리포트 |
| 2 | **RuleRouter 분리.** 그룹 A 규칙(`check_direct_skill`, `_SIMPLE_QUERY_PATTERNS`)만 `FastRouter` 인터페이스 뒤로 옮기고 `build_router` 선택 지점을 만듭니다. 그룹 B/B'는 건드리지 않습니다. 동작 변화 없음. | 기존 테스트 통과, `SYSTEM1_ROUTER=rule` 기본값 |
| 3 | **Laya PoC.** `laya-multilingual`을 zero-shot으로 붙여 validation 셋에서 측정합니다. 한계 확인이 목적입니다. | 정확도, ECE, 지연 수치 |
| 4 | **데이터셋 구축 + fine-tune + 보정** (3.5절) | 체크포인트 v0, 평가 리포트 |
| 5 | **SystemOneRouter 구현 + 배포.** `laya-serve` 컨테이너, `.env` 설정, 헬스체크, circuit breaker, 장애 시 RuleRouter 대체 | `SYSTEM1_ROUTER=rule`, `SYSTEM1_SHADOW=true`로 배포 |
| 6 | **그림자 모드 (1~2주).** 실행은 RuleRouter, Laya 판단은 기록만 합니다. 규칙 대비 일치율과 차이 사례를 분석하고 intent별 θ를 정합니다. | 일치율, 오실행 0에 가까운 θ |
| 7 | **Laya 전환 (readonly).** `SYSTEM1_ROUTER=laya`, `SYSTEM1_SCOPE=readonly`. conversational과 read-only 스킬만 Laya 단독으로 처리합니다. `SYSTEM1_SHADOW=true`로 RuleRouter 판단을 계속 기록해 비교합니다. | 지연과 성공률이 기준선 이상. 미달이면 `rule`로 롤백 |
| 8 | **범위 확대.** `SYSTEM1_SCOPE=navigation`(알려진 장소로의 단순 navigate). nav_safety는 유지합니다. Laya 판단(`hint`)을 planner 프롬프트에 주입하는 것은 이 단계에서 효과를 측정한 뒤 검토합니다. | 단계별 지표 확인. 이상 시 scope 축소 또는 `rule`로 롤백 |
| 9 | **지속 개선.** 오분류 사례를 학습 데이터로 다시 넣고 정기적으로 재학습합니다. | 버전별 비교 리포트 |

## 5. 위험과 대응

| 위험 | 대응 |
|---|---|
| fine-tune 데이터 부족 | 시나리오 증강. 그동안은 `SYSTEM1_ROUTER=rule` + `SYSTEM1_SHADOW=true` 또는 `SYSTEM1_SCOPE=readonly`로 제한 |
| 잘못된 action 실행 | action 스킬은 System 2 경유가 기본. `SYSTEM1_SCOPE` 확대는 지표 기반으로 단계적으로 |
| Laya 서버 장애 | timeout + circuit breaker로 RuleRouter 자동 대체 |
| Laya 성능이 규칙보다 나쁨 | `SYSTEM1_ROUTER=rule`로 즉시 롤백. 그룹 B/B'는 항상 켜져 있으므로 롤백해도 안전망 손실이 없음 |
| 그룹 B/B'를 실수로 Laya 경로에서 우회 | 그룹 B/B'는 planner 내부에 두고 라우터 코드에서 참조하지 않음. 두 모드 모두에서 교정이 작동하는지 회귀 테스트 |
| 확률 과신 | temperature scaling. 재학습할 때마다 ECE 재측정 |
| 스킬/장소 추가 시 성능 저하 | 계층형 질의와 동적 선택지. 스킬 추가 시 회귀 테스트 |
| 신생 모델(커뮤니티 공개)의 성숙도 | `FastRouter` 추상화로 교체 가능하게 유지(Jev, 사내 분류기 등) |

## 6. 범위 밖

- 자연어 응답 생성(`answer.py`), 다단계 계획, RAG 답변: 기존 LLM이 담당합니다.
- vision 입력: Laya와 Jev 모두 텍스트만 지원합니다.
- Jev 유료 API 연동: 결제가 가능해지면 `SYSTEM1_PROVIDER=jev`로 추가 검토합니다.

## 참고

- [Laya model card (Hugging Face)](https://huggingface.co/convaiinnovations/laya)
- [TypeSafe AI: Introducing System One Models & Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
- [TypeSafe AI Docs: System One](https://docs.typesafe.ai/concepts/system-one)
- [Jev (AI model) - Wikipedia](https://en.wikipedia.org/wiki/Jev_(AI_model))
