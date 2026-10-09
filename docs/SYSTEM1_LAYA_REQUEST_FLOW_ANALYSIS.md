## 결론

첨부 그림과 **개념적으로는 유사하지만, 실제 구현 구조는 조금 다릅니다.**

그림처럼 Laya가 모든 요청을 받아 `결정 / LLM / Rule / 기타` 중 하나를 직접 선택하는 구조는 아닙니다. 실제 구조는 다음과 같습니다.

```text
사용자 요청
   │
   ├─ 로봇 상태·메모리·스킬 컨텍스트 수집
   │
   ▼
사전 라우터 하나 선택
   ├─ SYSTEM1_ROUTER=rule → RuleRouter
   └─ SYSTEM1_ROUTER=laya → SystemOneRouter(Laya)
                              └─ 서버 장애 시에만 RuleRouter fallback
   │
   ├─ direct_skill  → LLM 없이 스킬 직접 실행
   ├─ simple_reply  → LLM 없이 고정 응답
   └─ llm           → System 2
                       ├─ 복합 명령 → TaskDecomposer → 단계별 LLM planner
                       └─ 단순 명령 → LLM planner
```

즉, 첨부 그림은 아래처럼 수정하면 코드와 더 정확히 일치합니다.

```text
                     ┌─ 직접 스킬 실행
입력 → System 1 ─────┼─ 고정 응답
       Rule 또는 Laya └─ System 2 LLM
                              ├─ Task Decomposer
                              └─ Planner → 스킬 실행

※ Laya 장애 시 RuleRouter fallback
```

### 그림과 실제 구현의 차이

- `Rule`은 Laya가 선택해서 보내는 목적지가 아닙니다.
  - `rule`과 `laya` 중 하나를 설정으로 **배타적으로 선택**합니다.
  - Laya 장애 시에만 RuleRouter를 fallback으로 사용합니다.
- `결정`이라는 별도 모듈보다는 `direct_skill`, `simple_reply`, `llm`이라는 라우팅 결과가 있습니다.
- `기타`라는 일반 분기는 없습니다.
- Laya는 작업 실행기나 planner가 아니라 **빠른 분류·라우팅 모델**입니다.
- 복합 작업의 실제 decomposition은 Laya가 아니라 별도 `TaskDecomposer`와 기존 LLM이 담당합니다.

관련 구현:

- 사전 라우터 호출:  
  `src/robo_claw_agent/robo_claw_agent/agent_node/execution.py:462-467`
- Laya의 결과 해석:  
  `src/robo_claw_agent/robo_claw_agent/agent_node/system1_router.py:328-411`
- Laya 복합 명령 우회:  
  `src/robo_claw_agent/robo_claw_agent/agent_node/system1_router.py:423-428`
- Task Decomposition 진입:  
  `src/robo_claw_agent/robo_claw_agent/agent_node/execution.py:541-558`

---

# 해당 요청의 실제 처리 흐름

입력:

> “집안에서 널려있는 수건을 수거해서 바구니에 넣어줘”

전제는 다음과 같습니다.

```dotenv
SYSTEM1_ROUTER=laya
RC_ENABLE_TASK_DECOMPOSITION=true
```

Task decomposition은 기본값이 활성화되어 있습니다.

## 1. 요청 컨텍스트 수집

System 1을 호출하기 전에 먼저 다음 정보를 수집합니다.

- 현재 로봇 상태
- 등록된 스킬
- 메모리/RAG
- 기억된 장소
- 최근 대화 등

그 후 `_router.decide()`를 호출합니다.

---

## 2. 복합 명령 여부를 규칙으로 먼저 검사

이 문장에는 다음 표현이 있습니다.

```text
수거해서 바구니에 넣어줘
     ^^^
```

`looks_compound()`는 `-아서`, `-어서`, `-해서`, `-가서` 등의 연결어미를 복합 명령으로 판정합니다.

```python
_CONNECTIVE_RE = re.compile(
    ...
    r"(?:아서|어서|해서|와서|가서|여서|면서)\s"
)
```

관련 코드:

- `agent_node/task_planner.py:21-28`
- `agent_node/task_planner.py:71-93`

따라서 이 문장은 확정적으로:

```text
looks_compound(...) == True
```

가 됩니다.

---

## 3. 중요한 점: 이 요청은 실제 Laya 서버로 전송되지 않음

Laya 라우터에는 다음 가드가 있습니다.

```python
if looks_compound(instruction):
    return RouteDecision.to_llm(
        source=self.name,
        intent="multi_step"
    )
```

즉, 이 요청에서는:

- Laya 모델에 intent 질문을 보내지 않음
- Laya가 스킬을 고르지 않음
- Laya가 하위 작업을 만들지 않음
- 즉시 `llm`, `intent=multi_step`으로 System 2에 넘김

관련 코드:

`agent_node/system1_router.py:423-428`

라우팅 결과는 대략 다음과 같습니다.

```json
{
  "kind": "llm",
  "source": "laya",
  "intent": "multi_step"
}
```

`source=laya`이지만 실제 Laya 추론을 수행했다는 뜻은 아닙니다. `SystemOneRouter`가 복합 명령 가드로 판단했다는 의미입니다.

---

## 4. TaskDecomposer 진입

실행 계층에서 다음 조건을 다시 확인합니다.

```python
self._llm
and self._enable_task_decomposition
and self._task_decomposer.looks_compound(instruction)
```

세 조건이 참이면 `TaskDecomposer.run()`을 호출합니다.

관련 코드:

`agent_node/execution.py:541-558`

---

## 5. System 2 LLM이 자연어 하위 작업으로 분해

여기서 별도의 LLM 호출이 발생합니다. Laya가 아닌, robo-claw에 설정된 기존 planner LLM을 사용합니다.

예상 가능한 분해 결과는 다음과 같습니다.

```json
{
  "steps": [
    {
      "instruction": "집안에 널려 있는 수건을 찾아 수거해"
    },
    {
      "instruction": "수거한 수건을 바구니에 넣어"
    }
  ]
}
```

다만 이것은 **예시일 뿐 정확한 출력은 실행 시 LLM 응답에 따라 달라집니다.** LLM이 아래처럼 한 단계로 반환할 수도 있습니다.

```json
{
  "steps": [
    {
      "instruction": "집안의 수건을 찾아 수거한 뒤 바구니에 넣어"
    }
  ]
}
```

분해 최대 단계는 기본 6개이고, 분해가 실패하거나 JSON 파싱이 실패하면 원문 전체를 단일 단계로 사용합니다.

```python
steps = await self._decompose(...)
if not steps:
    steps = [instruction]
```

관련 코드:

- 분해 호출: `agent_node/task_planner.py:192-199`
- 순차 실행: `agent_node/task_planner.py:210-275`

---

## 6. 각 하위 지시문을 System 2 planner가 계획하고 실행

각 하위 단계는 다시 Laya를 거치지 않습니다. 바로 다음 메서드로 들어갑니다.

```python
node._planner.run_llm_planning_loop(...)
```

예를 들어 두 단계로 분해됐다면:

```text
1단계: 집안에 널려 있는 수건을 찾아 수거해
   ↓
System 2 LLM planner
   ↓
스킬 선택 및 실행
   ↓
결과와 관측 데이터 저장

2단계: 수거한 수건을 바구니에 넣어
   ↓
최신 로봇 상태 + 1단계 결과를 프롬프트에 추가
   ↓
System 2 LLM planner
   ↓
스킬 선택 및 실행
```

2단계에는 1단계 실행 결과가 전달됩니다.

```text
[이전 단계 요약]
1. '집안에 널려 있는 수건을 찾아 수거해' -> 성공/실패
[결과 데이터] ...
```

각 단계는 기본적으로 실패 시 1회 재시도하고, 실패가 계속되면 이후 단계는 실행하지 않고 중단합니다.

---

# 어떤 스킬을 사용할 가능성이 있는가?

프롬프트상 “집 전체를 한 번 정리”하는 요청은 `tidy_home`을 사용하도록 안내되어 있습니다.

`agent_node/prompts.py:108-111`

```text
'집 정리해줘', '알아서 정리해줘'
→ tidy_home(scope='home')
```

따라서 단일 계획으로 처리되거나 planner가 전체 의도를 유지하면 다음과 같은 계획이 나올 가능성이 있습니다.

```json
{
  "skill": "tidy_home",
  "params": {
    "scope": "home"
  }
}
```

`tidy_home`은 다음 작업을 합니다.

1. 기억된 집안 장소 목록 조회
2. 각 장소를 한 번씩 방문
3. 현재 장면 분석
4. 정리 대상을 판단
5. 집기 및 배치 계획 검증
6. 승인된 배치 목적지가 있을 때만 집기·배치
7. 불가능하면 확인된 동료 로봇에 위임하거나 해당 대상을 처리하지 않음

하지만 중요한 제한이 있습니다.

## 수건과 바구니 처리 제한

현재 `tidy_home`은 모든 생활용품을 임의로 옮기는 일반 정리 기능은 아닙니다.

코드상 정책은:

- 모호한 생활용품은 이동하지 않음
- 로컬 배치에는 승인된 비추정 3D pose 필요
- 집기와 배치가 모두 포함된 완전한 계획만 실행
- 승인된 disposal target과 일치해야 실행

관련 코드:

`skills/tidy_home_skill.py:39-64`

따라서 실제로 수건을 바구니에 넣으려면 최소한 다음 조건이 필요합니다.

- 집안 방/장소가 시맨틱 맵 또는 RAG에 등록되어 있음
- 수건을 비전으로 탐지하고 3D 위치를 얻을 수 있음
- 로봇에 수건을 집을 수 있는 manipulation 스킬이 있음
- 바구니가 승인된 정리 목적지로 등록되어 있음
- 바구니의 추정값이 아닌 검증된 `target_pose`가 있음
- 집기 후 배치까지 안전한 계획이 만들어짐

이 조건이 충족되지 않으면 실제 물리 동작을 하지 않고 다음과 같은 형태로 끝날 수 있습니다.

- 기억된 정리 장소 없음
- 수건이 모호한 생활용품으로 판단됨
- 승인된 바구니 3D pose 없음
- 완전한 집기·배치 계획 없음
- manipulation 능력 부족
- 동료 로봇에 위임
- 작업 중단 또는 미처리 보고

---

# System 1이 직접 decompose하는가?

## 답: 아니요

정확히 나누면 다음과 같습니다.

| 구성 요소 | 역할 | Decompose 여부 |
|---|---|---|
| Laya/System 1 | 빠른 intent·스킬·모호성 분류, 직접 실행 또는 System 2 전달 | 하지 않음 |
| `looks_compound()` | 연결어미 기반 복합 명령 감지 | 분해하지 않고 감지만 함 |
| `TaskDecomposer` | LLM에게 자연어 하위 지시문 생성을 요청 | 직접 오케스트레이션 |
| 기존 System 2 LLM | 실제 하위 지시문 생성 및 단계별 스킬 계획 | 수행 |
| `tidy_home` | 장소 순회, 장면 분석, 정리 계획·실행 | 스킬 내부의 별도 정리 오케스트레이션 |

즉 전체 표현은 다음이 가장 정확합니다.

```text
System 1:
"이 요청은 복합 작업이므로 내가 직접 실행하지 않고 System 2로 넘긴다."

TaskDecomposer + System 2:
"요청을 하위 지시문으로 분해하고 각 단계를 계획·실행한다."
```

## 이 요청의 최종 요약 플로우

```text
“집안에서 널려있는 수건을 수거해서 바구니에 넣어줘”
   │
   ▼
로봇 상태/메모리 컨텍스트 수집
   │
   ▼
SystemOneRouter
   │
   ├─ “수거해서” 연결어미 감지
   └─ multi_step 판정
       ※ Laya 서버 호출은 하지 않음
   │
   ▼
RouteDecision(kind="llm", intent="multi_step")
   │
   ▼
TaskDecomposer
   │
   └─ 기존 LLM으로 자연어 단계 생성
       예: 수건 수거 → 바구니 배치
   │
   ▼
각 단계마다 System 2 planner 실행
   │
   ├─ 스킬 선택
   ├─ 안전/입력 검증
   ├─ 실행
   ├─ 실패 시 기본 1회 재시도
   └─ 결과를 다음 단계에 전달
   │
   ▼
전체 성공 또는 실패 단계에서 중단
```

따라서 현재 구현에서 **Laya는 router이고, 실제 task decomposition은 별도의 System 2 LLM 경로가 수행합니다.**
