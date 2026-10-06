# LangSmith 트레이싱/모니터링 통합

robo_claw의 모든 LLM 동작(계획 수립, RAG 쿼리 재작성, 임베딩, 이미지 분석)을
[LangSmith](https://smith.langchain.com)에서 추적·모니터링할 수 있는 **선택적(optional) 트레이싱 계층**을
제공합니다. 구현은 `src/robo_claw_agent/robo_claw_agent/tracing.py`입니다.

## 설계 원칙

- **완전 선택적**: `langsmith` 패키지가 없거나 트레이싱 환경변수가 꺼져 있으면 모든
  트레이싱 코드는 no-op으로 동작합니다. 기존 실행 경로/성능/의존성에 영향 없음.
- **단일 진입점**: 모든 LLM 호출이 `llm_bridge` 계층을 통과하므로, 벤더 SDK 클라이언트를
  이 계층에서 한 번만 감싸 토큰 사용량·모델·지연시간을 자동 캡처합니다.
- **환경변수 구동**: LangSmith SDK 표준 환경변수(`LANGSMITH_*`)만으로 켜고 끈다. 별도의
  ROS 파라미터/launch 인자 배선이 필요 없다. 에이전트 노드 프로세스가 환경변수를 직접 읽으므로,
  실행 방식에 따라 값을 넣는 위치가 다르다(아래 "2. 환경변수 설정" 참고).

## 활성화 방법

### 1. 의존성 설치

```bash
task setup-deps   # 또는: uv sync
```

`pyproject.toml`에 `langsmith`가 추가되어 있으므로 위 명령으로 함께 설치됩니다.

### 2. 환경변수 설정

실행 방식에 따라 `LANGSMITH_*` 값을 넣는 위치가 다릅니다.

| 실행 방식 | 설정 위치 |
| --- | --- |
| `./rclaw launch <robot> <env>` (로컬, `--docker` 모두) | AI Config Server 프로필의 LangSmith 설정 섹션. 서버가 캐시 `.env`(`~/.robo_claw/config_cache/<robot>_<env>/.env`)로 내려주며, 이 파일은 동기화 때마다 다시 생성되므로 직접 수정하지 않습니다. |
| `scripts/run_robo_claw_docker.sh` | 저장소 루트 `.env`(또는 `RC_ENV_FILE`로 지정한 파일). `--env-file`로 컨테이너에 전달됩니다. |
| `./rclaw run` / `./rclaw sim` | **셸에서 `export`합니다.** 이 명령은 저장소 `.env`를 launch 인자를 만드는 데만 쓰고 프로세스 환경변수로 넘기지 않으므로, `.env`에 넣은 `LANGSMITH_*`는 에이전트에 전달되지 않습니다. |
| `ros2 launch` 직접 실행 | 셸에서 `export`합니다. |

서버 프로필이 제공하는 항목은 `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`,
`LANGSMITH_ENDPOINT`입니다. `LANGSMITH_TRACING_SAMPLING_RATE`, `LANGSMITH_HIDE_INPUTS` 같은 그 밖의
SDK 변수는 `./rclaw launch`(로컬)에서는 셸 `export`로, Docker에서는 `scripts/run_robo_claw_docker.sh`의
`.env`로 지정합니다.

`.env`(Docker 스크립트) 또는 셸 `export`로 지정하는 값의 예시입니다.

```dotenv
# LangSmith 트레이싱
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...          # LangSmith에서 발급받은 API 키
LANGSMITH_PROJECT=robo-claw         # (선택) 프로젝트 이름, 기본값 "default"
# LANGSMITH_ENDPOINT=https://api.smith.langchain.com  # (선택) 자체 호스팅 시
```

> legacy 변수(`LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY` 등)도 그대로 인식됩니다.

### 3. 실행 및 확인

```bash
task build         # 소스 변경 반영

# ./rclaw run 은 .env 의 LANGSMITH_* 를 전달하지 않으므로 export 한 뒤 실행
set -a; source <(grep -E '^LANGSMITH_' .env); set +a
./rclaw run
```

에이전트 노드 시작 로그에 다음 중 하나가 출력된다:

- `LangSmith tracing ENABLED (project=..., endpoint=...)` → 활성화됨
- `LangSmith tracing disabled (...)` → 환경변수 off
- `LANGSMITH_TRACING is enabled but the 'langsmith' package is not installed...`
  → `uv sync` 필요
- `LANGSMITH_TRACING is enabled but LANGSMITH_API_KEY is not set...`
  → 키 누락(트레이스 업로드 안 됨)

이후 채널(HTTP `/task`, 메신저 등)로 명령을 내리면 LangSmith 대시보드의 해당
프로젝트에 실행 트리(run tree)가 나타난다.

## 트레이싱 계층 구조 (LangSmith run tree)

```
run_llm_planning_loop            (chain)  ← 태스크 1건 = 트레이스 1건
├─ collect_task_context          (chain)
│  ├─ query_reformulate → *Bridge.chat   (llm, 토큰/모델 캡처)
│  └─ rag_search → *Bridge.embed         (llm/retriever)
└─ (라운드 반복)
   ├─ *Bridge.chat               (llm)   ← 벤더 SDK 래퍼가 토큰 사용량 자동 기록
   └─ 스킬 실행 결과가 messages 로 피드백
```

- OpenAI/Azure/Anthropic: **SDK 클라이언트 래핑**으로 토큰 사용량·모델명이 정확히 기록됨.
- Ollama: SDK 래퍼가 없어 `@traceable(run_type="llm")`로 입력 메시지/출력 텍스트를 캡처
  (토큰 메타데이터는 Ollama가 일관되게 제공하지 않음).

`tracing.py`의 공개 API:

- `is_tracing_env_enabled()` — 트레이싱 환경변수 on/off 판단.
- `is_active()` — 환경변수 on **이면서** `langsmith` import 가능 여부.
- `wrap_openai_client(client)` / `wrap_anthropic_client(client)` — 활성 시 벤더 SDK
  클라이언트를 LangSmith 래퍼로 감싸 반환, 비활성/실패 시 원본 그대로 반환.
- `traceable(*args, **kwargs)` — `langsmith.traceable`의 안전 래퍼. 비활성/미설치 시
  원본 함수를 그대로 실행하는 no-op 데코레이터. 동기·비동기 함수 모두 지원.
- `init_tracing(emit=...)` — 시작 시 활성화 상태를 진단해 로깅.

모든 함수는 `langsmith` 미설치 또는 래핑 실패 시 예외를 삼키고 원본 동작을 유지하도록
설계되어, 트레이싱 문제로 로봇 제어 경로가 중단되지 않는다.

## 상세 로그와 실행 그래프를 표시하는 방법

### 현재 화면이 단순하게 보이는 이유

현재 instrumentation은 다음 경계에 적용되어 있습니다.

- `execute_task`
- `run_llm_planning_loop`
- `collect_task_context`
- `runtime_operation` (`llm_chat`, `rag_search`, `get_robot_summary`, `skill:*` 등)
- `parse_llm_plan`
- `navigation_safety_gate`
- `SkillManager.execute`
- `skill_policy_check`
- `skill_preconditions`
- `skill_schema_validation`
- `skill_attempt_direct`, `skill_attempt_with_timeout`
- 각 provider의 `chat`, `embed`, `analyze_image`
- OpenAI/Azure/Anthropic SDK client wrapper

따라서 Task → context → LLM → plan parse → safety → skill 실행과 retry까지 child run tree로
확인할 수 있습니다. 다음 동작은 아직 독립된 child run이 아니라 상위 결과나 일반 Robot log로
표시됩니다.

- TaskQueue 대기 시간
- ROS action feedback의 세부 sample
- ROS action cancel 확인 과정
- skill recovery 내부 단계
- peer/FleetControl 통신

LangSmith 실행 그래프는 자동으로 Python call graph나 ROS graph를 만드는 기능이 아닙니다.
`traceable`로 계측된 함수와 SDK wrapper 호출만 parent-child run tree에 나타납니다.

### 1. 모든 trace를 수집하도록 환경변수 설정

실행 방식별 위치("활성화 방법 > 2. 환경변수 설정")에 다음 값을 설정합니다. `./rclaw launch --docker`는 서버 프로필의 네 항목(`TRACING`/`API_KEY`/`PROJECT`/`ENDPOINT`)만 전달하므로, sampling·hide 값까지 쓰려면 `scripts/run_robo_claw_docker.sh`와 `.env`를 사용합니다.

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=robo-claw-detailed
LANGSMITH_ENDPOINT=https://api.smith.langchain.com

# 1.0은 trace 100% 수집입니다. 운영 부하가 크면 0.1 등으로 낮춥니다.
LANGSMITH_TRACING_SAMPLING_RATE=1.0

# 상세 입력/출력을 확인하려면 false로 둡니다.
LANGSMITH_HIDE_INPUTS=false
LANGSMITH_HIDE_OUTPUTS=false
```

여러 LangSmith workspace에 접근 가능한 API key라면 필요에 따라 workspace ID도 지정합니다.
AI Config Server의 LangSmith 설정 섹션에서도 workspace ID를 입력할 수 있으며, 해당 값은
배포 `.env`와 런타임 설정에 포함됩니다.

```dotenv
LANGSMITH_WORKSPACE_ID=<workspace-id>
```

`LANGSMITH_HIDE_INPUTS=false`, `LANGSMITH_HIDE_OUTPUTS=false`는 사용자 명령, prompt, Robot
상태와 LLM 응답이 외부 LangSmith에 업로드될 수 있다는 의미입니다. 운영 Robot에서는 개인
정보, credential, camera base64, 전체 map 또는 민감한 위치 정보가 포함되지 않도록 먼저
redaction을 적용해야 합니다.

### 2. Docker/launch process에 환경변수가 전달됐는지 확인

설정을 변경한 뒤 기존 container/process를 완전히 재시작합니다. `./rclaw launch`는 서버 프로필을 다시 동기화해 반영합니다.

```bash
./rclaw kill
./rclaw launch <robot-profile> <environment> --docker --use-grpc
```

실행 중인 container에서 실제 환경변수를 확인합니다.

```bash
docker ps --format '{{.ID}} {{.Names}}'
docker exec <container-name-or-id> env | grep -E '^(LANGSMITH|LANGCHAIN)_'
```

최소한 다음 값이 보여야 합니다.

```text
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=...
LANGSMITH_PROJECT=robo-claw-detailed
LANGSMITH_TRACING_SAMPLING_RATE=1.0   # scripts/run_robo_claw_docker.sh 의 .env 로 지정한 경우에만
```

API key 원문을 운영 로그나 화면 캡처에 노출하지 않습니다.

### 3. 실제 planning 경로를 호출해 trace 생성

`get_status` 같은 direct/read-only skill만 호출하면 LLM planning loop를 우회할 수 있어 상세한
LLM run tree가 생성되지 않습니다. 자연어 Task endpoint를 사용해 실제 planning 경로를
호출합니다.

```bash
curl -X POST http://127.0.0.1:8080/task \
  -H "Authorization: Bearer ${RC_HTTP_CONTROL_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"instruction":"현재 상태를 확인하고 주변 상황을 한 문장으로 설명해줘"}'
```

LangSmith의 `robo-claw-detailed` project에서 `run_llm_planning_loop` trace를 열고 `Children`
또는 tree view를 펼칩니다. Project가 다르면 trace가 없는 것처럼 보이므로 `.env`의 project
이름과 LangSmith 화면의 project filter가 같은지 확인합니다.

### 4. Skill 실행을 그래프의 child node로 표시

Skill 실행은 중앙 실행 경계인
`src/robo_claw_agent/robo_claw_agent/skill_manager.py`의 `SkillManager.execute`에
`traceable(run_type="tool")`가 적용되어 있습니다. 각 개별 skill 파일을 모두 감싸는 대신 중앙
경계를 계측해 누락과 중복 trace를 줄입니다.

현재 적용 형태:

```python
from robo_claw_agent.tracing import traceable


@traceable(
    run_type="tool",
    name="SkillManager.execute",
    tags=["robot-skill"],
)
def execute(self, skill_name, params=None, timeout_sec=30.0):
    ...
```

이렇게 하면 planning trace 아래에 다음 구조가 나타납니다.

```text
run_llm_planning_loop
├─ OpenAIBridge.chat 또는 OllamaBridge.chat
├─ SkillManager.execute
│  └─ skill 결과
└─ OpenAIBridge.chat 또는 OllamaBridge.chat
```

Skill parameter에 token, 파일 내용, image base64가 포함될 수 있으면 `process_inputs`와
`process_outputs`를 사용해 LangSmith에 전달하기 전에 제거합니다.

```python
def redact_skill_inputs(inputs):
    safe = dict(inputs)
    params = dict(safe.get("params") or {})
    for key in ("token", "api_key", "image_base64", "file_content"):
        if key in params:
            params[key] = "<redacted>"
    safe["params"] = params
    safe.pop("self", None)
    return safe


@traceable(
    run_type="tool",
    name="SkillManager.execute",
    process_inputs=redact_skill_inputs,
)
def execute(self, skill_name, params=None, timeout_sec=30.0):
    ...
```

### 5. Safety, retry, ROS action을 별도 node로 표시

Policy, precondition, schema validation과 skill attempt는 현재 별도 child run으로 계측됩니다.
ROS action 세부 단계는 필요할 경우 아래 권장 경계를 추가 계측합니다.

| 계측 위치 | 권장 run name | run type | 표시할 정보 |
| --- | --- | --- | --- |
| `_check_skill_policy` | `skill_policy_check` | `chain` | 적용됨: allow/deny와 실패 이유 |
| `check_preconditions` | `skill_preconditions` | `chain` | 적용됨: 조건 통과 여부와 실패 이유 |
| `validate_input_schema` | `skill_schema_validation` | `chain` | 적용됨: schema 통과 여부와 오류 |
| `_execute_direct`, `_execute_with_timeout` | `skill_attempt_*` | `tool` | 적용됨: attempt, timeout, duration, result |
| navigation goal 전송 | `ros_navigation_goal` | `tool` | goal ID, map/frame, target |
| action feedback 처리 | `ros_action_feedback` | `chain` | progress, current pose |
| action cancel | `ros_action_cancel` | `tool` | requested/confirmed/timeout |
| recovery 실행 | `skill_recovery` | `tool` | 실패 원인과 recovery 결과 |

고주파 feedback을 매번 run으로 만들면 trace 수와 비용이 급증합니다. feedback은 다음 중 하나로
제한합니다.

- 진행률이 10% 이상 변할 때만 기록
- 1~5초 주기로 sampling
- child run 대신 상위 run의 metadata/event로 요약

### 6. TaskQueue 대기와 전체 Task lifecycle 표시

전체 Task의 queue → planning → skill → result 흐름을 보려면
`agent_node/execution.py`의 Task 진입점을 root trace로 두고 Task ID를 metadata에 넣습니다.

권장 tree:

```text
execute_task                         metadata: task_id, robot_id, channel
├─ task_queue_wait                   queue_position, wait_ms
├─ collect_task_context
│  ├─ query_reformulate
│  ├─ rag_search
│  └─ get_robot_summary
├─ run_llm_planning_loop
│  ├─ llm_chat
│  ├─ parse_llm_plan
│  └─ SkillManager.execute
│     ├─ skill_policy_check
│     ├─ skill_attempt
│     └─ skill_recovery
└─ task_result                       success, status, duration
```

고정 decorator metadata가 아니라 Task별 metadata를 전달하려면 traceable 함수 호출 시
`langsmith_extra`를 사용합니다.

```python
result = traced_execute_task(
    instruction,
    langsmith_extra={
        "metadata": {
            "task_id": task_id,
            "robot_id": robot_id,
            "channel": channel,
        },
        "tags": [robot_id, channel],
    },
)
```

metadata에는 검색과 filter에 필요한 작은 값만 넣고 prompt 전체나 sensor raw data는 inputs에
중복 저장하지 않습니다.

### 7. RAG 검색 내용을 자세히 표시

현재 `collect_task_context` 아래에서 RAG가 실행되지만 memory backend 내부 단계는 별도 run이
아닐 수 있습니다. 다음 단위를 계측합니다.

```text
query_reformulate        llm
embed_query              embedding
vector_search            retriever
result_filter            chain
render_skill_lessons     retriever
```

Retriever run의 output에는 전체 vector를 넣지 않고 다음만 기록합니다.

```json
{
  "query": "회의실 위치",
  "top_k": 5,
  "result_count": 3,
  "document_ids": ["..."],
  "scores": [0.91, 0.82, 0.73]
}
```

embedding vector 전체, camera image, 대용량 map을 output에 넣으면 trace 크기와 업로드 지연이
커지므로 기록하지 않습니다.

### 8. Robot/Task별 검색이 가능하도록 metadata와 tag 구성

모든 root trace에 다음 metadata를 통일해 넣습니다.

```json
{
  "robot_id": "former-01",
  "session_id": "boot-session-id",
  "task_id": "task-id",
  "mission_id": "mission-id",
  "channel": "http",
  "map_id": "floor-1",
  "software_version": "git-sha"
}
```

권장 tag:

```text
robot:former-01
channel:http
provider:ollama
mode:production
```

LangSmith Traces 화면에서 `robot_id`, `task_id`, error 여부, latency, token usage로 filter하고
saved view를 만들면 Robot별 실시간 모니터링이 쉬워집니다.

### 9. Trace ID를 Robo-Claw 로그와 연결

LangSmith run 안에서 ROS log와 같은 trace ID를 출력하려면 현재 run tree ID를 가져옵니다.

```python
from langsmith.run_helpers import get_current_run_tree

run = get_current_run_tree()
trace_id = str(run.trace_id if run else "")
node.get_logger().info(f"task started trace_id={trace_id}")
```

Task ID, command ID, trace ID를 함께 로그에 넣으면 LangSmith trace에서 실제 Robot 로그로 이동할
수 있습니다. API key, prompt 원문, image base64는 correlation log에 넣지 않습니다.

### 10. 실시간 업로드 지연을 줄이고 종료 시 flush

LangSmith SDK는 비동기 buffer를 사용할 수 있어 dashboard 반영에 짧은 지연이 생길 수 있습니다.
장시간 실행되는 Robot process에서는 정상이며 매 호출마다 강제 flush하지 않습니다.

테스트 process나 정상 shutdown 시 남은 trace를 전송하려면 다음을 사용합니다.

```python
from langsmith import Client

client = Client()
client.flush()
client.close()
```

강제 종료, `kill -9`, 전원 차단에서는 buffer가 전송되지 않을 수 있습니다. 종료 hook에서만
flush하고 emergency stop 경로가 LangSmith network 응답을 기다리게 하지 않습니다.

### 11. Dashboard에서 상세 그래프 확인

LangSmith에서 다음 순서로 확인합니다.

1. `.env`의 `LANGSMITH_PROJECT`와 같은 project를 선택합니다.
2. `Traces`에서 `run_llm_planning_loop` 또는 `execute_task` root run을 선택합니다.
3. child run tree를 펼칩니다.
4. `Inputs`, `Outputs`, `Metadata`, `Feedback`, `Errors` 탭을 확인합니다.
5. `robot_id`, `task_id`, provider tag로 saved filter를 만듭니다.
6. latency와 error를 기준으로 dashboard/monitor를 구성합니다.
7. 대표 Task dataset을 만들고 성공 여부, tool selection, latency 평가를 연결합니다.

그래프가 한 줄로만 보이면 환경변수 문제가 아니라 child 함수가 아직 `traceable`로 계측되지 않은
경우가 대부분입니다.

### 12. 상세 trace가 보이지 않을 때 점검 순서

```bash
# 1. package 확인
uv run python -c 'import langsmith; print(langsmith.__version__)'

# 2. 환경변수 확인
uv run python - <<'PY'
import os
for key in (
    "LANGSMITH_TRACING",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
    "LANGSMITH_TRACING_SAMPLING_RATE",
    "LANGSMITH_HIDE_INPUTS",
    "LANGSMITH_HIDE_OUTPUTS",
):
    print(f"{key}={os.getenv(key)}")
PY

# 3. Robo-Claw 시작 로그 확인
./rclaw run 2>&1 | grep -i langsmith
```

| 증상 | 확인 방법 |
| --- | --- |
| trace가 전혀 없음 | tracing 값, API key, outbound HTTPS, project filter 확인 |
| LLM run만 보임 | SkillManager/TaskQueue/ROS action에 child instrumentation 추가 |
| token usage가 없음 | Ollama가 아닌 OpenAI/Anthropic wrapper 사용 여부 확인 |
| 입력/출력이 비어 있음 | `LANGSMITH_HIDE_INPUTS/OUTPUTS` 확인 |
| 일부 trace만 보임 | `LANGSMITH_TRACING_SAMPLING_RATE=1.0` 확인 |
| trace가 서로 분리됨 | thread/context 경계와 root trace nesting 확인 |
| dashboard 반영이 늦음 | process 정상 종료 flush, network/proxy 확인 |
| trace가 너무 큼 | image, vector, map, file content redaction 적용 |

## 안전성 / 롤백

- 트레이싱을 끄려면 `LANGSMITH_TRACING=false`로 설정하거나 해당 값을 제거합니다(`./rclaw launch`는 서버
  프로필, Docker 스크립트는 `.env`, `./rclaw run`은 셸 환경).
- 비활성 상태에서 `traceable` wrapper는 LangSmith run/client를 만들지 않고 원래 함수를 직접
  호출합니다.
- 원래 반환 객체, 예외, sync/async 실행 방식은 그대로 유지됩니다.
- OpenAI/Anthropic client wrapper도 비활성 상태에서는 원본 SDK client를 그대로 반환합니다.
- tracing 비활성 경로는 sync 반환값, async 반환값과 원래 예외 보존 테스트로 검증합니다.
- redaction은 LangSmith에 전달할 복사본에만 적용하며 실제 Skill parameter/result를 변경하지
  않습니다.
- 트레이싱 계층은 설정 파일(`agent.yaml`, launch 인자)이 아닌 환경변수만 읽습니다. 값을 바꾼 뒤
  container/node를 재시작하면 반영되며, 값을 넣는 위치는 실행 방식에 따릅니다("2. 환경변수 설정" 참고).

```dotenv
LANGSMITH_TRACING=false
```

```bash
./rclaw kill
./rclaw launch <robot-profile> <environment> --docker --use-grpc
```

이 설정에서는 상세 tracing 코드가 Robo-Claw의 planning, Skill 실행, retry, safety, ROS 제어
결과에 영향을 주지 않습니다.
