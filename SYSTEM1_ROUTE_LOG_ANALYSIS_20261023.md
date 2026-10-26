# System1 사전 라우터 분류 정확도 분석 (2026-10-23 로그)

`cloi-robo-claw` 컨테이너 1회 기동 구간(ROS 시각 1792732594 ~ 1792733171)의 로그를 근거로
System1 사전 라우터(Laya)의 분류가 기대대로 동작하는지 점검한 기록입니다. 이 문서는 해당 로그
1건에 대한 정적 분석이며, Laya 재측정과 코드 변경은 포함하지 않습니다.

## 1. 분석 대상 환경

| 항목 | 값 | 출처 |
| --- | --- | --- |
| 선택 라우터 | `router=laya` | `System1 router: router=laya ...` 로그 |
| shadow | `shadow=True` (rule 라우터를 그림자로 실행) | 동일 |
| scope | `readonly` | 동일 |
| endpoint | `http://192.168.50.212:8000` (외부 Laya, `local_server=False`) | 동일 |
| agent id | `cloid01` | `AgentNode ready [id=cloid01]` |
| 스킬 수 | 102 (MCP 14 포함) | `Loaded 102 skill(s)` |

shadow 항목의 `shadow` 필드는 Laya가 아니라 **rule 라우터의 판단**입니다
(`build_router`, `system1_router.py:581`).

## 2. 결론

1. 이번 세션에서 Laya는 **fast path 결정을 한 번도 내리지 못했습니다**. 6회 호출 전부 `llm`으로
   escalate되었습니다(`direct_skill` 0건, `simple_reply` 0건).
2. intent 라벨은 정답이 아닌 경우가 다수입니다. 특히 이동/복합 명령 2건과 날씨 조회 1건이
   `smalltalk`으로 분류되었습니다.
3. 그럼에도 **동작 결과는 모두 정상**이었습니다. 모든 항목의 confidence가 임계값 미만이라
   System 2(LLM planner)로 넘어갔고, LLM이 올바른 스킬을 계획했습니다.
4. 따라서 현재 구성의 System1은 **품질 이득 없이 호출당 305~400ms 지연만 추가**하고 있습니다.
5. 계측에도 결함이 있습니다. `agree` 필드는 서로 다른 intent를 "일치"로 계산하고
   (`system1_router.py:533`), 판단 근거인 `hint`(`ambiguous`, `needs_motion`, `skill_confidence`)는
   로그에 남지 않습니다(`_decision_record`, `system1_router.py:547`). 이 로그만으로는 "왜 그렇게
   판단했는지"를 재구성할 수 없습니다.

## 3. 요청별 판정

| # | 지시문 | Laya(primary) | 기대 경로 | 실제 경로 | 판정 |
| --- | --- | --- | --- | --- | --- |
| 1 | 만나서 반가워 | `llm`, intent=smalltalk 0.3631 | `simple_reply` | llm → LLM 즉답(0스킬) | 라벨 정답, 임계값 미달 |
| 2 | 주방으로 갔다가 안방으로 와줘 | `llm`, intent=**smalltalk** 0.3668 | llm(복합 명령) | llm → 3스킬 계획(`follow_waypoints` 2개 웨이포인트) | **라벨 오분류** |
| 3 | 안방 안쪽으로 와줘 | `llm`, intent=single_skill 0.3674 | llm | llm → `navigate_to` 실패 → `analyze_scene` 재계획 | 결과 정상 |
| 4 | 오른쪽으로 2미터 이동해줘 | `llm`, intent=single_skill 0.7759 | `direct_skill` `move_relative` | llm → `move_relative` 실행 | **rule이 정답**, Laya는 미달 |
| 5 | 주방으로 갔다가 카메라 이미지 보내고 안방으로 와줘 | (호출 생략) intent=multi_step | llm | llm → 3스킬 | 정상(compound 가드 동작) |
| 6 | 곤지암 지역 오늘 날씨 알려줘 | `llm`, intent=**smalltalk** 0.5439 | 스킬 조회 계열 | llm → `weather_info_tool` | **라벨 오분류** |
| 7 | 만나서 반가워 | `llm`, intent=smalltalk 0.3631 | `simple_reply` | llm | 1번과 동일 |

- 전 항목 `fallback=false`입니다. Laya 서버 장애(timeout / HTTP 오류 / circuit open)로 대체된
  항목은 없습니다. 즉 **가용성 문제가 아니라 판단 품질 문제**입니다.
- 4번은 이 로그에서 유일하게 유효한 불일치입니다. `shadow`(rule)는
  `direct_skill move_relative {"forward":0.0,"lateral":-2.0}`을 냈고 Laya는 `llm`을 냈습니다
  (`agree=false`).

## 4. 원인 분석

### 4.1 후보 스킬 목록이 명령 유형을 배제합니다

`_skill_candidates`(`system1_router.py:231-259`)는 scope가 `readonly`이면 `navigate_to`를
후보에서 제외하고(`:238`), `risk_level != "read"`(`:241`) 또는 필수 인자가 있는 스킬(`:244`)도
제외합니다. 결과적으로:

- 이동 명령의 정답인 `navigate_to`가 선택지에 없습니다.
- `move_relative`는 필수 인자(`forward` 등)가 있어 선택지에 없습니다.
- 정답 선택지가 없는 상태에서 Laya는 가장 그럴듯한 잔여 라벨(`smalltalk` / `single_skill`)을
  고르게 됩니다. 2번·4번·6번이 이 패턴입니다.

`SCOPE=readonly`에서는 fast path로 처리 가능한 요청 집합이 "인자 없는 read 스킬"로 한정됩니다.
그런데 그 영역에서도 아래 4.2의 confidence 문제로 결정이 나오지 않았습니다.

### 4.2 intent confidence가 임계값에 도달할 수 없습니다

`DEFAULT_THRESHOLDS`(`system1_router.py:59-70`)는 `smalltalk=0.90`, `single_skill=0.85`를
요구합니다. 관측된 intent confidence는 0.3631, 0.3668, 0.3674, 0.5439, 0.7759입니다.

intent는 4지선다이고 균등 prior가 0.25이므로, 0.36~0.37은 **사실상 동점 판정**입니다.
즉 이 세션 기준으로 `smalltalk` fast path는 수학적으로 도달 불가능합니다. 코드 주석
(`system1_router.py:65-68`)에는 평범한 인사에 0.65~0.84, 제스처 인사에 0.93~0.96이 관측되었다고
기록되어 있어, **현재 모델/프롬프트/선택지 구성에서 분포가 달라졌거나 재측정이 필요한 상태**로
보입니다.

### 4.3 복합 명령 휴리스틱에 false negative가 있습니다

`_CONNECTIVE_RE`(`task_planner.py:25-28`)는 `았다가|었다가|했다가`만 잡습니다. `"갔다가"`는
선행 음절의 초성이 달라 매칭되지 않습니다. 실측 결과:

```text
'주방으로 갔다가 안방으로 와줘'                        -> looks_compound = False   # 누락
'주방으로 갔다가 카메라 이미지 보내고 안방으로 와줘'    -> looks_compound = True
```

2번 지시문이 Laya로 전달된 이유가 이것입니다(5번은 `-고` 연결어미로 잡혔습니다).

### 4.4 shadow `agree` 지표가 의미를 상실합니다

`_start_shadow`(`system1_router.py:521-544`)는 `kind`와 `skill`만 비교합니다.

```python
"agree": record.get("kind") == primary.kind
and record.get("skill", "") == primary.skill,
```

rule 라우터는 `intent`를 채우지 않으므로(`RuleRouter.decide_sync`), 양쪽이 `llm`이면 intent가
`smalltalk`이든 빈 문자열이든 `agree=true`입니다. 이 로그도 6/7(86%)이 `agree=true`로 기록되었지만
실제 의미 일치는 그보다 훨씬 낮습니다. 그림자 로그를 근거로 "두 라우터가 대체로 일치한다"고
판단하면 잘못된 결론에 도달합니다.

### 4.5 판단 근거가 로그에 남지 않습니다

`_decision_record`(`system1_router.py:547-559`)는 `kind/skill/params/intent/confidence/source/
fallback/latency_ms`만 기록하고 `hint`를 버립니다. 따라서 `ambiguous`, `needs_motion`,
`skill_confidence`, `target_place` 값은 ROS 로그에 없습니다. 이 값들은
`RouteDecision.trace_metadata()`를 통해 LangSmith trace에만 실립니다
(`execution.py:543`). "왜 smalltalk로 판단했는지"를 로그로 확인할 수 없는 직접적 이유입니다.

### 4.6 latency와 timeout

관측 latency는 304.9 / 373.3 / 367.4 / 399.6 / 382.4 / 316.0 ms입니다. 기본
`SYSTEM1_TIMEOUT_MS=300`(`.env.example:341`, `system1_router.py:98`)인데 실패 경고가 없으므로,
운영 환경에서 타임아웃을 상향해 사용 중인 것으로 보입니다. 어느 값인지는 로그에 출력되지 않아
확정할 수 없습니다.

## 5. 위험 시나리오

`주방으로 갔다가 안방으로 와줘`가 `smalltalk`으로 분류되었습니다. 이번에는 confidence 0.37이라
escalate되어 안전했지만, 같은 문형이 `smalltalk >= 0.90`으로 판정되면
`ROUTE_SIMPLE_REPLY`(`system1_router.py:374-382`)로 처리되어 **고정 응답만 반환하고 이동은
실행되지 않습니다**(`execution.py:615-619`). 4.3의 가드 누락 때문에 이 시나리오를 막는 방어선은
confidence 하나뿐입니다.

## 6. 개선안 (우선순위)

| 순위 | 항목 | 대상 | 검증 방법 |
| --- | --- | --- | --- |
| 1 | 복합 명령 연결어미 보강(`갔다가/왔다가/봤다가` 등 과거 연결어미) | `task_planner.py:25-28` | `looks_compound` 케이스 테스트 선행(Red) 후 최소 수정 |
| 2 | `agree` 판정에 intent 비교 추가, `_decision_record`에 `hint` 포함 | `system1_router.py:521-559` | shadow 엔트리 단위 테스트(`test_system1_router.py:504` 인근) |
| 3 | scope/후보 정책 재검토: navigation까지 라우팅할 의도면 `SYSTEM1_SCOPE=navigation` + `SYSTEM1_SKILLS` 명시, 아니면 기대 효과를 read 조회 한정으로 문서·임계값에 반영 | `.env`, `docs/SYSTEM1_FAST_ROUTER.md` | 런치/config 테스트 + 재기동 로그 |
| 4 | 임계값 재산정 | `SYSTEM1_CONF_THRESHOLDS_JSON` | `scripts/system1_eval.py --router both --endpoint http://192.168.50.212:8000` |

4번은 현재 0.36 분포를 전제로 하면 0.90/0.85가 아니라 0.35~0.55 구간에서 측정해야 합니다.
`validation/system1/router_cases.jsonl`(32건)과 `laya_cases.jsonl`(33건)을 기준선으로 사용합니다.

## 7. 검증 상태

실행한 항목:

- 저장소 코드 정적 대조 (`system1_router.py`, `fast_router.py`, `task_planner.py`, `execution.py`)
- `looks_compound` 입력별 실측(위 4.3 값)

미실행 항목(근거 없음, 추정 표기함):

- Laya 엔드포인트 재측정(`system1_eval.py`, `--router both`): 3·6절 임계값 제안은 재측정 전 가설입니다.
- 코드 수정 및 테스트 실행: 변경 없음.
- 실제 `SYSTEM1_TIMEOUT_MS`, `SYSTEM1_CONF_THRESHOLDS_JSON` 운영 값 확인: 로그 미출력.

## 8. 부록: 원본 shadow 로그

```text
System1 shadow: {"ts": 1792732749.117, "instruction": "만나서 반가워", "primary": {"kind": "llm", "skill": "", "params": {}, "intent": "smalltalk", "confidence": 0.3631, "source": "laya", "fallback": false, "latency_ms": 304.9}, "shadow": {"error": null, "kind": "llm", "skill": "", "params": {}, "intent": "", "confidence": null, "source": "rule", "fallback": false, "latency_ms": 0.0}, "agree": true}
System1 shadow: {"ts": 1792732778.483, "instruction": "주방으로 갔다가 안방으로 와줘", "primary": {"kind": "llm", "skill": "", "params": {}, "intent": "smalltalk", "confidence": 0.3668, "source": "laya", "fallback": false, "latency_ms": 373.3}, "shadow": {"error": null, "kind": "llm", "skill": "", "params": {}, "intent": "", "confidence": null, "source": "rule", "fallback": false, "latency_ms": 0.0}, "agree": true}
System1 shadow: {"ts": 1792732886.103, "instruction": "안방 안쪽으로 와줘", "primary": {"kind": "llm", "skill": "", "params": {}, "intent": "single_skill", "confidence": 0.3674, "source": "laya", "fallback": false, "latency_ms": 373.9}, "shadow": {"error": null, "kind": "llm", "skill": "", "params": {}, "intent": "", "confidence": null, "source": "rule", "fallback": false, "latency_ms": 0.0}, "agree": true}
System1 shadow: {"ts": 1792732981.243, "instruction": "오른쪽으로 2미터 이동해줘", "primary": {"kind": "llm", "skill": "", "params": {}, "intent": "single_skill", "confidence": 0.7759, "source": "laya", "fallback": false, "latency_ms": 399.6}, "shadow": {"error": null, "kind": "direct_skill", "skill": "move_relative", "params": {"forward": 0.0, "lateral": -2.0}, "intent": "", "confidence": null, "source": "rule", "fallback": false, "latency_ms": 0.0}, "agree": false}
System1 shadow: {"ts": 1792733018.829, "instruction": "주방으로 갔다가 카메라 이미지 보내고 안방으로 와줘", "primary": {"kind": "llm", "skill": "", "params": {}, "intent": "multi_step", "confidence": null, "source": "laya", "fallback": false, "latency_ms": 0.0}, "shadow": {"error": null, "kind": "llm", "skill": "", "params": {}, "intent": "", "confidence": null, "source": "rule", "fallback": false, "latency_ms": 0.0}, "agree": true}
System1 shadow: {"ts": 1792733080.971, "instruction": "곤지암 지역 오늘 날씨 알려줘", "primary": {"kind": "llm", "skill": "", "params": {}, "intent": "smalltalk", "confidence": 0.5439, "source": "laya", "fallback": false, "latency_ms": 382.4}, "shadow": {"error": null, "kind": "llm", "skill": "", "params": {}, "intent": "", "confidence": null, "source": "rule", "fallback": false, "latency_ms": 0.0}, "agree": true}
System1 shadow: {"ts": 1792733171.803, "instruction": "만나서 반가워", "primary": {"kind": "llm", "skill": "", "params": {}, "intent": "smalltalk", "confidence": 0.3631, "source": "laya", "fallback": false, "latency_ms": 316.0}, "shadow": {"error": null, "kind": "llm", "skill": "", "params": {}, "intent": "", "confidence": null, "source": "rule", "fallback": false, "latency_ms": 0.0}, "agree": true}
```
