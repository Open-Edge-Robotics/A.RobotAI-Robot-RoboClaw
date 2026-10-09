# 분해된 Task의 System 1 재라우팅 구현 제안 : 20261009
- 상태: 검토 제안(미구현)
- 목적: 복합 명령을 분해한 후, System 1이 처리할 수 있는 단일 Task는 System 2 planner 호출 없이 실행하여 지연시간을 줄입니다.
- 전제: 기존 안전 정책과 System 2 fallback을 유지합니다.

---

## 1. 현재 구현

### 1.1 복합 명령 감지

현재 복합 명령 여부는 Laya나 System 2 LLM이 아니라 Python 휴리스틱 함수가 판단합니다.

```python
looks_compound(instruction)
```

구현 위치:

```text
src/robo_claw_agent/robo_claw_agent/agent_node/task_planner.py
```

주요 위치:

- 복합 명령 패턴 정의: `task_planner.py:21-57`
- 실제 판정 함수: `task_planner.py:71-93`

판정 기준:

- `한 다음`, `한 후`, `그리고`, `그 다음` 등의 순차 표현
- `-아서`, `-어서`, `-해서`, `-가서`, `-면서` 등의 연결어미
- 명사가 아닌 `-고` 연결어미
- 종결형 지시 표현이 두 번 이상 등장
- 조건절과 행동 지시가 함께 포함된 긴 문장

예:

```text
집안에서 널려있는 수건을 수거해서 바구니에 넣어줘
```

`수거해서`의 `-해서`가 연결어미 패턴에 매칭되므로 다음과 같이 판단됩니다.

```python
looks_compound(instruction) == True
```

### 1.2 `looks_compound()` 호출 위치

#### RuleRouter

파일:

```text
agent_node/fast_router.py
```

복합 명령을 단일 direct skill이 선점하지 못하게 합니다.

```text
복합 명령
  → direct skill 라우팅 생략
  → System 2 전달
```

#### SystemOneRouter

파일:

```text
agent_node/system1_router.py:423-428
```

```python
if looks_compound(instruction):
    return RouteDecision.to_llm(
        source=self.name,
        intent="multi_step",
    )
```

복합 명령이면 실제 Laya API를 호출하지 않고 System 2로 전달합니다.

#### 실행 계층

파일:

```text
agent_node/execution.py:541-558
```

다음 조건을 만족하면 TaskDecomposer를 실행합니다.

```python
self._llm
and self._enable_task_decomposition
and self._task_decomposer.looks_compound(instruction)
```

### 1.3 분해된 Task의 현재 실행 방식

현재 TaskDecomposer는 분해한 모든 Task를 바로 System 2 planner로 전달합니다.

파일:

```text
agent_node/task_planner.py:233-243
```

```python
step_msg, step_results, step_success, step_events = (
    await node._planner.run_llm_planning_loop(
        goal_handle,
        step,
        list(step_messages),
        robot_summary,
        timeout,
        send_fb,
    )
)
```

현재 흐름:

```text
원본 복합 요청
   │
   ▼
TaskDecomposer(System 2)
   │
   ├─ 단일 Task 1 → System 2 planner
   ├─ 단일 Task 2 → System 2 planner
   └─ 단일 Task N → System 2 planner
```

분해된 Task는 다음을 거치지 않습니다.

- RuleRouter
- SystemOneRouter
- Laya
- `direct_skill` 판정
- `simple_reply` 판정

따라서 현재는 **분해된 Task 중 System 1이 처리할 수 있는지를 판단하는 기능이 없습니다.**

---

## 2. 제안 목표

분해된 Task를 무조건 System 2 planner로 보내지 않고, System 1이 처리할 가능성이 있는 Task를 선별해 기존 라우터에 전달합니다.

```text
원본 복합 요청
   │
   ▼
TaskDecomposer(System 2)
   │
   ▼
분해된 단일 Task
   │
   ▼
System 1 시도 대상인지 저비용 사전 검사
   │
   ├─ 후보
   │    ▼
   │  RuleRouter 또는 SystemOneRouter
   │    ├─ direct_skill → 안전 검증 후 직접 실행
   │    ├─ simple_reply → 즉시 처리
   │    └─ llm → System 2 planner
   │
   └─ 비후보
        ▼
      System 2 planner
```

핵심 원칙:

1. System 1이 처리할 가능성이 있는 단계만 System 1에 전달합니다.
2. System 1이 처리하지 못하면 기존 System 2 planner로 fallback합니다.
3. 분해된 Task가 다시 TaskDecomposer에 들어가는 재귀 구조를 만들지 않습니다.
4. 기존 direct-skill 경로의 안전 검사를 공통으로 재사용합니다.
5. 기존 단계 결과 전달, 상태 갱신, retry 및 중단 정책을 유지합니다.

---

## 3. 기대 효과

현재 복합 명령이 N개 Task로 분해되면 대략 다음 LLM 호출이 발생합니다.

```text
1회: Task decomposition LLM
N회 이상: 단계별 System 2 planning
실패 시: 단계별 planner 재시도
```

제안 구조에서는:

```text
1회: Task decomposition LLM
각 단계:
  System 1이 직접 처리 가능 → System 2 planner 호출 없음
  System 1이 처리 불가     → System 2 planner 호출
```

효과가 기대되는 작업:

- 상태 조회
- 배터리 조회
- 인자 없는 읽기 전용 스킬
- 기억된 장소로 이동
- 중단 및 취소처럼 RuleRouter가 직접 처리할 수 있는 명령
- System 1이 높은 신뢰도로 분류할 수 있는 단일 명령

예:

```text
배터리를 확인하고 주방으로 이동한 다음 사진을 찍어줘
```

예상 분해:

```text
1. 배터리 상태를 확인해
2. 주방으로 이동해
3. 주방 사진을 찍어
```

가능한 처리:

```text
1. get_status
   → System 1 직접 실행 가능

2. navigate_to(target_name="주방")
   → SYSTEM1_SCOPE=navigation이고 기억된 장소이면 직접 실행 가능

3. 사진 촬영
   → 직접 실행 범위 밖이면 System 2 planner
```

이 경우 모든 단계를 System 2에서 계획하는 것보다 planner 호출을 줄일 수 있습니다.

---

## 4. 효과가 제한적인 작업

다음과 같은 manipulation 작업은 현재 System 1 직접 실행 범위를 벗어납니다.

```text
집안에서 널려있는 수건을 수거해서 바구니에 넣어줘
```

예상 분해:

```text
1. 집안에 널려 있는 수건을 찾아 수거해
2. 수거한 수건을 바구니에 넣어
```

필요한 처리:

- 비전 인식
- 대상 물체 파라미터 추출
- 수건의 3D 위치 확인
- 팔과 그리퍼 동작
- 바구니의 3D 배치 pose 확인
- 이전 단계의 물체 파지 상태 유지
- manipulation 안전 검증

현재 Laya 직접 실행 후보는 기본적으로 다음으로 제한됩니다.

- `risk_level=read`
- 필수 입력 인자가 없는 스킬
- `SYSTEM1_SCOPE=navigation`인 경우 기억된 장소 대상 `navigate_to`

따라서 수건 관련 Task를 System 1에 전달해도 대부분 다음 경로가 예상됩니다.

```text
분해된 Task
  → Laya
  → 직접 실행 불가
  → System 2 planner
```

이 경우에는 기존 경로보다 Laya 호출 시간만 추가될 수 있습니다. 따라서 모든 하위 Task를 무조건 System 1에 전달해서는 안 됩니다.

---

## 5. 제안 설계

### 5.1 1차 eligibility 판단

분해된 Task를 실제 System 1에 전달하기 전에 저비용 정책으로 시도 대상을 판정합니다.

권장 API 예시:

```python
router.can_attempt(node, instruction)
```

또는:

```python
should_try_system1_for_step(node, instruction)
```

권장 구현 위치:

```text
agent_node/system1_router.py
```

또는 별도 정책 파일:

```text
agent_node/step_router.py
```

TaskDecomposer가 직접 라우팅 정책을 소유하지 않고, 라우터 계층이 정책을 관리하도록 합니다.

### 5.2 System 1 시도 후보

다음 조건이면 System 1을 시도할 수 있습니다.

- 하위 Task가 다시 복합 명령이 아님
- 짧고 단일한 명령
- 조회 작업일 가능성이 있음
- 기억된 장소로 이동하는 단일 작업
- 인자 없는 안전한 스킬일 가능성이 있음
- 취소·중단처럼 기존 RuleRouter가 직접 처리할 수 있는 명령

### 5.3 System 1 생략 후보

다음 조건이면 System 1을 생략하고 바로 System 2로 보냅니다.

- 집기, 배치 등 manipulation 작업
- perception과 manipulation이 결합된 작업
- 자유 텍스트 파라미터 추출 필요
- 좌표 또는 3D pose 필요
- 조건부 작업
- 이전 단계 결과의 의미 해석이 필요한 작업
- 분해된 Task가 여전히 복합 명령
- 위험도가 높은 스킬이 필요한 작업

단순한 키워드 목록만으로 manipulation 여부를 판정하면 규칙 유지보수 문제가 발생할 수 있으므로, 스킬 메타데이터와 함께 판단하는 것이 좋습니다.

### 5.4 Eligibility gate의 역할

Eligibility gate는 스킬을 선택하거나 실행하는 컴포넌트가 아닙니다. 다음 질문에만 답합니다.

```text
“이 하위 Task에 대해 System 1 라우터를 호출해 볼 가치가 있는가?”
```

반환값은 단순한 `bool`보다 판정 이유를 포함하는 구조가 좋습니다.

```python
@dataclass(frozen=True)
class StepRoutingEligibility:
    eligible: bool
    reason: str
    task_type: str = "unknown"
```

예:

```python
StepRoutingEligibility(
    eligible=True,
    reason="safe_read_task",
    task_type="read",
)
```

또는:

```python
StepRoutingEligibility(
    eligible=False,
    reason="manipulation_requires_planner",
    task_type="manipulation",
)
```

`reason`은 로그와 trace에 기록하여 eligibility 규칙이 실제로 시간을 줄였는지 분석하는 데 사용합니다.

### 5.5 권장 데이터 모델

현재 TaskDecomposer는 하위 Task를 문자열 리스트로 반환합니다.

```json
{
  "steps": [
    {"instruction": "배터리 상태를 확인해"},
    {"instruction": "주방으로 이동해"}
  ]
}
```

Eligibility gate를 구현할 때는 선택적인 라우팅 힌트를 추가하는 것이 좋습니다.

```json
{
  "steps": [
    {
      "instruction": "배터리 상태를 확인해",
      "task_type": "read",
      "requires_motion": false,
      "requires_runtime_result": false
    },
    {
      "instruction": "주방으로 이동해",
      "task_type": "navigation",
      "requires_motion": true,
      "requires_runtime_result": false
    },
    {
      "instruction": "수건을 집어",
      "task_type": "manipulation",
      "requires_motion": true,
      "requires_runtime_result": true
    }
  ]
}
```

권장 내부 타입:

```python
@dataclass(frozen=True)
class DecomposedStep:
    instruction: str
    task_type: str = "unknown"
    requires_motion: bool | None = None
    requires_runtime_result: bool | None = None
```

허용할 `task_type`은 닫힌 enum으로 제한합니다.

```text
read
navigation
perception
manipulation
communication
control
conditional
unknown
```

LLM이 허용되지 않은 값을 반환하거나 필드 형식이 잘못되면 `unknown`으로 정규화합니다.

중요:

- 이 메타데이터는 LLM이 생성한 힌트이므로 안전 판단의 최종 근거가 아닙니다.
- 잘못된 힌트가 들어와도 기존 SystemOneRouter의 스킬 allowlist, risk level, 필수 파라미터, `needs_motion`, confidence 및 실행 안전 검사가 최종적으로 차단해야 합니다.
- Eligibility gate의 오판은 원칙적으로 불필요한 호출 또는 최적화 기회 손실만 만들어야 하며, 위험 스킬의 우회 실행으로 이어지면 안 됩니다.

### 5.6 Eligibility 판정 순서

권장 판정 순서는 가장 저렴하고 명확한 조건부터 적용하는 것입니다.

```text
1. 기능 플래그 확인
2. 입력 유효성 확인
3. 재귀·복합 명령 차단
4. 라우터 사용 가능 여부 확인
5. 명백한 비대상 Task 차단
6. task_type별 scope 확인
7. 사용 가능한 System 1 후보 스킬 존재 여부 확인
8. eligible 반환
```

구체적인 정책:

| 순서 | 조건 | 결과 | reason 예시 |
|---|---|---|---|
| 1 | 단계 재라우팅 기능 비활성화 | 제외 | `feature_disabled` |
| 2 | 빈 문자열 또는 길이 제한 초과 | 제외 | `invalid_instruction` |
| 3 | `looks_compound(step)`가 True | 제외 | `still_compound` |
| 4 | 사용 가능한 라우터가 없음 | 제외 | `router_unavailable` |
| 5 | `conditional` 또는 이전 결과 해석 필수 | 제외 | `runtime_reasoning_required` |
| 6 | `manipulation` | 제외 | `manipulation_requires_planner` |
| 7 | `navigation`, scope가 navigation이 아님 | 제외 | `navigation_scope_disabled` |
| 8 | `read`, `navigation`, 안전한 `control` | 후보 | `supported_task_type` |
| 9 | `unknown` | 초기 버전에서는 제외 | `unknown_task_type` |
| 10 | 해당 범주의 허용 후보 스킬이 없음 | 제외 | `no_system1_candidates` |

초기 구현은 보수적으로 다음 범위만 허용하는 것이 좋습니다.

```text
read
navigation(SYSTEM1_SCOPE=navigation인 경우)
안전한 control 중 기존 RuleRouter가 명시적으로 지원하는 경우
```

다음은 초기 버전에서 제외합니다.

```text
perception
manipulation
conditional
unknown
자유 텍스트 또는 3D pose가 필요한 작업
```

`perception`도 읽기처럼 보일 수 있지만 카메라 방향 조정, 탐색, 이미지 파라미터 등 동작과 필수 인자가 필요한 경우가 많으므로 초기 범위에서는 제외하는 편이 안전합니다.

### 5.7 권장 클래스 및 API

라우팅 정책은 TaskDecomposer가 아니라 라우터 계층에 둡니다.

권장 구현 위치:

```text
src/robo_claw_agent/robo_claw_agent/agent_node/system1_router.py
```

규모가 커지면 별도 파일로 분리합니다.

```text
src/robo_claw_agent/robo_claw_agent/agent_node/step_routing_policy.py
```

권장 API:

```python
class StepRoutingPolicy:
    def __init__(self, config: System1Config) -> None:
        self._config = config

    def evaluate(
        self,
        node: Any,
        step: DecomposedStep,
    ) -> StepRoutingEligibility:
        ...
```

`SelectedRouter`가 이를 감싸는 편의 API를 제공할 수 있습니다.

```python
class SelectedRouter:
    def evaluate_step_eligibility(
        self,
        node: Any,
        step: DecomposedStep,
    ) -> StepRoutingEligibility:
        return self.step_policy.evaluate(node, step)
```

`can_attempt() -> bool`만 제공하는 것보다 구조화된 결과를 반환해야 제외 이유를 추적할 수 있습니다.

### 5.8 판정 코드 예시

다음은 개념적인 초기 구현 예시입니다.

```python
_ALLOWED_STEP_TYPES = {"read", "navigation", "control"}
_MAX_STEP_INSTRUCTION_CHARS = 300


class StepRoutingPolicy:
    def __init__(self, config: System1Config) -> None:
        self._config = config

    def evaluate(
        self,
        node: Any,
        step: DecomposedStep,
    ) -> StepRoutingEligibility:
        instruction = (step.instruction or "").strip()

        if not getattr(node, "_route_decomposed_steps_with_system1", False):
            return StepRoutingEligibility(False, "feature_disabled", step.task_type)

        if not instruction or len(instruction) > _MAX_STEP_INSTRUCTION_CHARS:
            return StepRoutingEligibility(False, "invalid_instruction", step.task_type)

        if looks_compound(instruction):
            return StepRoutingEligibility(False, "still_compound", step.task_type)

        task_type = normalize_task_type(step.task_type)

        if task_type in {"conditional", "manipulation"}:
            return StepRoutingEligibility(
                False,
                "manipulation_requires_planner"
                if task_type == "manipulation"
                else "runtime_reasoning_required",
                task_type,
            )

        if step.requires_runtime_result:
            return StepRoutingEligibility(
                False,
                "runtime_reasoning_required",
                task_type,
            )

        if task_type == "navigation" and self._config.scope != SCOPE_NAVIGATION:
            return StepRoutingEligibility(
                False,
                "navigation_scope_disabled",
                task_type,
            )

        if task_type not in _ALLOWED_STEP_TYPES:
            return StepRoutingEligibility(False, "unsupported_task_type", task_type)

        candidates = _skill_candidates(node, self._config)
        if not candidates:
            return StepRoutingEligibility(False, "no_system1_candidates", task_type)

        if task_type == "navigation" and NAVIGATE_SKILL not in candidates:
            return StepRoutingEligibility(False, "no_navigation_candidate", task_type)

        return StepRoutingEligibility(True, "supported_task_type", task_type)
```

이 코드는 eligibility만 판단합니다. `eligible=True`가 곧 직접 실행을 의미하지 않습니다. 이후 반드시 기존 라우터가 최종 결정을 내려야 합니다.

```python
eligibility = router.evaluate_step_eligibility(node, step)

if eligibility.eligible:
    route = await router.decide(node, step.instruction)
else:
    route = RouteDecision.to_llm(
        source="step_eligibility",
        intent="requires_planning",
        hint={"eligibility_reason": eligibility.reason},
    )
```

### 5.9 TaskDecomposer 연동 방식

`TaskDecomposer.run()`의 단계별 planner 직접 호출을 `_execute_step()`으로 교체합니다.

```python
async def _execute_step(
    self,
    *,
    goal_handle: Any,
    step: DecomposedStep,
    messages: list[dict[str, str]],
    robot_summary: dict,
    timeout: float,
    send_fb: Any,
):
    node = self.node
    router = node._router
    eligibility = router.evaluate_step_eligibility(node, step)

    set_current_trace_metadata({
        "step.route.eligible": eligibility.eligible,
        "step.route.eligibility_reason": eligibility.reason,
        "step.route.task_type": eligibility.task_type,
    })

    if eligibility.eligible:
        route = await router.decide(node, step.instruction)
        set_current_trace_metadata(route.trace_metadata())

        if route.kind == ROUTE_DIRECT_SKILL:
            return await node._execute_routed_skill(
                route=route,
                instruction=step.instruction,
                timeout=timeout,
                send_fb=send_fb,
            )

        if route.kind == ROUTE_SIMPLE_REPLY:
            return make_simple_reply_step_result(route.reply)

    return await node._planner.run_llm_planning_loop(
        goal_handle,
        step.instruction,
        messages,
        robot_summary,
        timeout,
        send_fb,
    )
```

중요한 구현 규칙:

- `_execute_step()`은 `_execute_task_inner()`를 다시 호출하지 않습니다.
- `route.kind == llm`이면 TaskDecomposer가 아니라 planner만 호출합니다.
- 직접 실행 코드는 새로 복제하지 않고 공통 `_execute_routed_skill()`을 사용합니다.
- 단계별 retry는 기존 `TaskDecomposer.run()`의 while loop가 계속 담당합니다.
- 이전 단계 요약과 최신 로봇 상태를 포함한 `messages`를 planner fallback에 그대로 전달합니다.

### 5.10 Task type 생성과 검증

TaskDecomposer 프롬프트의 응답 형식을 다음처럼 확장할 수 있습니다.

```text
[응답 형식]
{
  "steps": [
    {
      "instruction": "<하위 지시문>",
      "task_type": "read|navigation|perception|manipulation|communication|control|conditional|unknown",
      "requires_motion": true|false,
      "requires_runtime_result": true|false
    }
  ]
}
```

파싱 시 보수적으로 검증합니다.

```python
_ALLOWED_TASK_TYPES = {
    "read",
    "navigation",
    "perception",
    "manipulation",
    "communication",
    "control",
    "conditional",
    "unknown",
}


def normalize_task_type(value: Any) -> str:
    value = str(value or "").strip().lower()
    return value if value in _ALLOWED_TASK_TYPES else "unknown"
```

Boolean 필드가 실제 bool이 아니면 `None`으로 처리합니다. 누락되거나 불명확한 정보는 안전한 방향으로 판단합니다.

```text
task_type 누락/오류 → unknown → System 2
requires_runtime_result 불명확 + 조건 표현 존재 → System 2
navigation인데 목적지 불명확 → 최종 SystemOneRouter에서 System 2
```

기존 문자열 기반 테스트 및 호출부와의 호환을 위해 전환 기간에는 문자열도 허용할 수 있습니다.

```python
if isinstance(raw_step, str):
    step = DecomposedStep(instruction=raw_step)
elif isinstance(raw_step, dict):
    step = DecomposedStep(...)
```

다만 `unknown`을 기본적으로 System 2로 보내면 기존 동작과 동일하므로 안전한 점진 적용이 가능합니다.

### 5.11 RuleRouter와 Laya 모드 처리

`SYSTEM1_ROUTER=rule`과 `SYSTEM1_ROUTER=laya`에서 동일한 eligibility API를 사용하되 최종 판정은 선택된 primary router에 맡깁니다.

```text
Eligibility gate
   │
   ├─ primary=rule → RuleRouter.decide()
   └─ primary=laya → SystemOneRouter.decide()
                         └─ 장애 시 RuleRouter fallback
```

Eligibility를 판단하기 위해 RuleRouter를 먼저 실행한 뒤 다시 Laya를 호출하는 직렬 구조는 권장하지 않습니다. 기존 설계의 배타 선택 원칙과 평가 기준이 흐려질 수 있기 때문입니다.

단, `SYSTEM1_ROUTER=rule`에서는 네트워크 비용이 없으므로 atomic step에 대한 RuleRouter 호출 범위를 넓게 가져갈 수 있습니다. `SYSTEM1_ROUTER=laya`에서는 불필요한 HTTP 호출을 줄이기 위해 보수적인 eligibility 정책을 적용합니다. 이 차이를 정책 옵션으로 명시할 수 있습니다.

### 5.12 안전 경계

Eligibility gate는 최적화 계층이며 보안 또는 로봇 안전의 최종 경계가 아닙니다. 다음 계층을 절대로 우회하지 않아야 합니다.

```text
_skill_candidates()
  → read risk 및 필수 인자 필터

interpret_response()
  → confidence, ambiguity, needs_motion 및 scope 검사

스킬 입력 schema 검증
  → 파라미터 형식 검사

nav_safety / sensor_health
  → 실행 시점 안전 검사

공통 direct-skill executor
  → 상태·timeout·실패 처리
```

특히 TaskDecomposer가 `task_type=read`라고 잘못 출력하더라도 manipulation 스킬이 System 1 후보 목록에 들어가지 않아야 합니다. Eligibility 결과만으로 스킬 이름을 선택하거나 `_skills.execute()`를 직접 호출해서는 안 됩니다.

---

## 6. 2차 판단: 기존 System 1 재사용

1차 eligibility gate를 통과한 Task는 기존 `SelectedRouter.decide()`에 전달합니다.

```text
하위 Task
   │
   ▼
can_attempt() == True
   │
   ▼
SelectedRouter.decide()
   │
   ├─ SYSTEM1_ROUTER=rule → RuleRouter
   ├─ SYSTEM1_ROUTER=laya → SystemOneRouter/Laya
   └─ Laya 장애 → RuleRouter fallback
         │
         ├─ direct_skill → 직접 실행
         ├─ simple_reply → 즉시 처리
         └─ llm → System 2 planner
```

SystemOneRouter의 기존 최종 정책을 유지합니다.

직접 실행 조건:

- `intent == single_skill`
- intent confidence가 임계값 이상
- 선택한 skill이 허용 후보에 포함
- skill confidence가 임계값 이상
- 모호성이 임계값 미만
- 허용되지 않은 motion 요청이 아님
- 필요한 목적지 파라미터가 존재

하나라도 만족하지 않으면 System 2 planner로 fallback합니다.

---

## 7. TaskDecomposer 변경 제안

현재 단계 실행 코드를 별도 메서드로 분리합니다.

현재:

```python
while attempt <= max_retries and not step_success:
    step_msg, step_results, step_success, step_events = (
        await node._planner.run_llm_planning_loop(...)
    )
```

제안:

```python
while attempt <= max_retries and not step_success:
    step_msg, step_results, step_success, step_events = (
        await self._execute_step(
            goal_handle=goal_handle,
            step=step,
            messages=step_messages,
            robot_summary=robot_summary,
            timeout=timeout,
            send_fb=send_fb,
        )
    )
```

개념적인 `_execute_step()` 구조:

```python
async def _execute_step(...):
    router = self.node._router

    if router.can_attempt(self.node, step):
        route = await router.decide(self.node, step)

        if route.kind == ROUTE_DIRECT_SKILL:
            return await execute_routed_skill(...)

        if route.kind == ROUTE_SIMPLE_REPLY:
            return ...

    return await self.node._planner.run_llm_planning_loop(...)
```

책임 분리:

```text
TaskDecomposer
  → 단계 순서, 이전 결과 전달, retry, 중단 관리

Router 계층
  → System 1 시도 가능성 및 라우팅 판단

공통 실행 계층
  → direct skill 안전 검증 및 실행

System 2 planner
  → System 1이 처리하지 못한 단계 계획
```

---

## 8. 재귀적 decomposition 방지

분해된 Task를 기존 `_execute_task_inner()`에 다시 넣는 방식은 피해야 합니다.

잘못된 재진입 예:

```text
분해된 Task
  → _execute_task_inner()
  → looks_compound=True
  → TaskDecomposer
  → 다시 분해된 Task
```

권장 구조:

```text
_execute_task_inner()
   └─ TaskDecomposer.run()
        └─ execute_decomposed_step()
             ├─ System 1 direct route
             └─ System 2 planner fallback
```

`execute_decomposed_step()`에서는 System 1 라우팅은 허용하지만 TaskDecomposer 재진입은 금지합니다.

---

## 9. direct-skill 실행 코드 공통화

현재 최상위 direct-skill 실행 코드는 `execution.py`에 있습니다. 분해된 Task에서 별도 실행 코드를 새로 만들면 안전 처리 차이가 발생할 수 있습니다.

따라서 다음과 같은 공통 실행 메서드를 추출하는 것이 좋습니다.

```python
async def _execute_routed_skill(
    self,
    route: RouteDecision,
    timeout: float,
    send_fb,
):
    ...
```

최상위 라우팅과 분해 단계 라우팅이 동일한 메서드를 사용하도록 합니다.

반드시 유지할 처리:

- `ensure_stretch_navigation_safety`
- 입력 schema 검증
- sensor health 검사
- navigation safety
- 위험 스킬 직접 실행 차단
- 실행 상태 업데이트
- 이미지 및 메시지 전송
- timeout 처리
- 취소 요청 처리
- 실패 결과 처리
- LangSmith route metadata 기록

---

## 10. TaskDecomposer 출력 확장 대안

TaskDecomposer가 하위 Task에 실행 특성을 함께 출력하는 방안도 검토할 수 있습니다.

예:

```json
{
  "steps": [
    {
      "instruction": "배터리 상태를 확인해",
      "task_type": "read",
      "requires_motion": false
    },
    {
      "instruction": "주방으로 이동해",
      "task_type": "navigation",
      "requires_motion": true
    },
    {
      "instruction": "수건을 집어",
      "task_type": "manipulation",
      "requires_motion": true
    }
  ]
}
```

결정 정책 예:

```python
if task_type == "read":
    try_system1 = True
elif task_type == "navigation" and scope == "navigation":
    try_system1 = True
elif task_type == "manipulation":
    try_system1 = False
else:
    try_system1 = False
```

주의사항:

- `task_type`은 LLM 출력이므로 그대로 신뢰하면 안 됩니다.
- 실제 등록 스킬, risk level, 필수 입력, scope와 안전 정책으로 재검증해야 합니다.
- 분해기가 분해와 스킬 계획을 동시에 수행하면 책임이 커집니다.

초기 구현에서는 TaskDecomposer 출력 형식을 변경하기보다 기존 System 1 라우터를 선택적으로 재사용하는 방식이 더 안전합니다.

---

## 11. 설정 제안

기능을 단계적으로 적용할 수 있도록 별도 설정을 추가하는 방안을 검토합니다.

예:

```text
RC_ROUTE_DECOMPOSED_STEPS_WITH_SYSTEM1=false
```

기본값은 `false`로 두고 평가 후 활성화합니다.

추가로 고려할 수 있는 설정:

```text
RC_SYSTEM1_STEP_ROUTING_MODE=eligible_only
RC_SYSTEM1_STEP_ROUTING_TIMEOUT_MS=100
```

의미:

- `eligible_only`: eligibility gate를 통과한 단계만 System 1 호출
- 단계 라우팅 timeout은 최상위 요청보다 짧게 설정 가능

실제 환경변수 이름과 설정 스키마 반영은 구현 단계에서 프로젝트 규칙에 맞게 확정합니다.

---

## 12. 관측성과 평가 지표

기능 도입 전후를 비교하기 위해 다음 항목을 기록하는 것이 좋습니다.

단계별 trace metadata 예:

```text
step.route.attempted
step.route.kind
step.route.source
step.route.fallback
step.route.latency_ms
step.route.skill
step.route.skipped_reason
step.planner.called
step.execution.success
```

평가 지표:

- 요청 전체 응답 시간
- Task당 평균 planning 시간
- System 2 planner 호출 횟수
- System 1 직접 실행 비율
- System 1 호출 후 다시 System 2로 간 비율
- Laya timeout 누적 시간
- 직접 실행 오라우팅 비율
- 작업 성공률
- 안전 정책 거부율
- 단계별 retry 횟수

특히 다음 값을 확인해야 합니다.

```text
절약한 System 2 planning 시간
  > 추가된 System 1 호출 시간
```

System 1을 호출했지만 대부분 `llm`으로 반환된다면 해당 Task 유형은 eligibility 대상에서 제외해야 합니다.

---

## 13. 테스트 제안

### 단위 테스트

- 단순 read Task는 System 1을 시도합니다.
- 기억된 장소 이동은 navigation scope에서 System 1을 시도합니다.
- manipulation Task는 System 1을 생략합니다.
- 복합 하위 Task는 System 1을 생략합니다.
- System 1이 `llm`을 반환하면 planner를 정확히 한 번 호출합니다.
- System 1이 `direct_skill`을 반환하면 planner를 호출하지 않습니다.
- Laya 장애 시 RuleRouter fallback이 적용됩니다.
- 단계 실행 중 TaskDecomposer가 재귀적으로 호출되지 않습니다.
- 직접 실행 실패 시 기존 retry 및 중단 정책을 유지합니다.
- 이전 단계 결과와 최신 로봇 상태가 다음 단계에 전달됩니다.

### 통합 시나리오

#### System 1 이점이 있는 요청

```text
배터리를 확인하고 주방으로 이동해줘
```

기대:

- 조회 단계 직접 실행
- navigation scope이면 이동 단계 직접 실행 가능
- System 2 planner 호출 감소

#### 혼합 요청

```text
배터리를 확인하고 주방으로 이동해서 컵을 집어줘
```

기대:

- 배터리 조회: System 1
- 주방 이동: 조건 충족 시 System 1
- 컵 집기: System 2

#### manipulation 중심 요청

```text
수건을 수거해서 바구니에 넣어줘
```

기대:

- manipulation 단계의 불필요한 System 1 호출 생략
- 기존 System 2 및 안전 정책 유지

---

## 14. 단계적 도입 방안

### 1단계: 관측만 수행

- 분해된 Task에 대해 `can_attempt()` 결과만 기록합니다.
- 실제 실행 경로는 변경하지 않습니다.
- 어떤 유형이 System 1 후보로 분류되는지 확인합니다.

### 2단계: read-only 활성화

- 인자 없는 read 스킬만 직접 실행합니다.
- 오라우팅 및 지연시간을 비교합니다.

### 3단계: navigation 활성화

- `SYSTEM1_SCOPE=navigation`일 때 기억된 장소 이동을 허용합니다.
- 기존 navigation safety를 그대로 적용합니다.

### 4단계: 정책 조정

- System 1을 호출한 뒤 대부분 System 2로 넘어가는 Task 유형을 제외합니다.
- confidence threshold와 eligibility 규칙을 조정합니다.

### 5단계: 확대 여부 검토

- manipulation은 별도 안전 설계와 정확한 인자 추출 수단이 준비되기 전까지 System 1 직접 실행 범위에 포함하지 않습니다.

---

## 15. 최종 권장안

모든 분해 Task를 무조건 System 1에 전달하는 방식은 권장하지 않습니다.

권장 구조:

```text
TaskDecomposer
   │
   ▼
분해된 Task
   │
   ▼
Router eligibility gate
   │
   ├─ read / 알려진 장소 navigation / 안전한 단일 명령
   │      → 기존 System 1 라우터
   │      → direct_skill 또는 System 2 fallback
   │
   └─ manipulation / pose 필요 / 복합·조건부 작업
          → System 1 생략
          → System 2 planner
```

구현 책임:

| 기능 | 권장 담당 |
|---|---|
| 원본 복합 명령 감지 | 기존 `looks_compound()` |
| 하위 Task의 System 1 시도 가능성 | `SelectedRouter.can_attempt()` 또는 별도 `StepRoutingPolicy` |
| 실제 direct/LLM 결정 | 기존 `RuleRouter` / `SystemOneRouter` |
| 단계 순서·결과 전달·retry | `TaskDecomposer` |
| direct skill 안전 실행 | 공통 실행 메서드 |
| 처리 불가 단계 계획 | 기존 System 2 planner |

핵심 결론:

> 분해된 Task에 System 1을 재사용하는 것은 일부 복합 요청의 지연시간을 줄일 수 있습니다. 다만 모든 Task에 적용하면 manipulation 요청에서 불필요한 Laya 호출이 추가될 수 있으므로, 라우터 계층의 eligibility gate를 통해 안전하고 단순한 Task만 선택적으로 System 1에 전달하는 방식이 적절합니다.
