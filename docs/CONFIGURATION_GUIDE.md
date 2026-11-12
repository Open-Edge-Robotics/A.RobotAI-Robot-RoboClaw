# 런타임 설정 가이드

이 문서는 RoboClaw 설정을 어디에서 지정하고, 어떤 실행 방식으로 적용하며,
최종적으로 어떤 값이 ROS 2 노드에 전달되는지 설명합니다.

## 권장 운영 모델

운영 로봇은 다음 흐름을 사용합니다.

```text
AI Config Server UI
  -> robot/environment profile 활성화
  -> RoboClaw CLI fetch
  -> v2 runtime manifest를 config.json 원본으로 사용
  -> .env 및 runtime files cache
  -> config-doctor
  -> launch --dry-run
  -> launch
```

공통 runtime 설정의 source of truth는 `rcf-config-contract` 저장소입니다. RoboClaw의
`contracts/contract.lock.json`이 고정한 GitLab Release artifact를 사용하며,
RoboClaw local Registry snapshot은 compatibility 비교용입니다.

개발자 로컬 실행은 `./rclaw run`(또는 `./rclaw sim`)과 `.env`를 사용합니다. 직접
`ros2 launch`를 실행할 때는 `.env`가 자동으로 읽히지 않으므로 launch argument를
직접 지정하거나 `./rclaw run`을 사용해야 합니다.

## 설정 소유권

| 설정 영역                  | 권장 소유자                            | 예시                                         |
| -------------------------- | -------------------------------------- | -------------------------------------------- |
| LLM, RAG, task 정책        | AI Config Server profile               | `llm_model`, `rag_top_k`, `strict_config`    |
| HTTP/gRPC 인증 정책        | AI Config Server profile + 배포 secret | token, CIDR, peer token                      |
| 로봇별 토픽/URDF/조작 설정 | RoboClaw robot YAML                    | `stretch3_config.yaml`, `former_config.yaml`, `cloid_config.yaml` |
| 모델 파일과 workspace 경로 | 로봇 호스트 `.env`                     | `RC_MODELS_DIR`, `RC_AGENT_WORKSPACE_DIR`    |
| ROS distro/DDS             | 실행 호스트                            | `ROS_DISTRO`, `RMW_IMPLEMENTATION`           |
| AI Config Server 자체 설정 | server `config.yaml`/Kubernetes Secret | DB path, admin/device token                  |

같은 값을 중앙 profile과 로컬 `.env`에 동시에 넣지 마세요. 예외적으로 로컬
검증을 위해 override할 때는 어떤 값을 덮어쓰는지 `config-effective`로 확인해야
합니다.

### 로봇별 지도 토픽

지도 토픽은 robot YAML의 `robo_claw_agent_node.ros__parameters`에서 지정합니다.
CLOiD의 `cloid_config.yaml`은 다음 설정을 사용합니다.

```yaml
robo_claw_agent_node:
  ros__parameters:
    map_topic: "/lio/grid_map"
    map_is_static: false
```

맵 스킬은 **명시적 `map_topic` 실행 인자 → 로봇 파라미터 → `/map`** 순서로
선택합니다. 생략하거나 공백인 인자는 로봇 설정을 사용합니다. 기본 launch는 같은
로봇 설정의 토픽을 gRPC 지도 구독에도 전달합니다. 별도 `RC_MAP_TOPIC` 환경변수나
공통 runtime contract 변경은 필요하지 않습니다.

transient-local(latched) 지도는 stamp와 관계없이 "마지막으로 발행된 맵"으로 항상
수용합니다. CLOiD `/lio/grid_map`처럼 1회 발행 뒤 stamp가 갱신되지 않는 맵도
차단하지 않습니다. 10초 freshness 상한은 volatile(실시간 스트림) 샘플에만 적용하며
수신 캐시 나이와 `header.stamp` 나이를 함께 검사합니다. `map_is_static` 파라미터는
선언·문서화를 유지하되 freshness 게이트에는 관여하지 않습니다. 맵 나이는
`capture_map`·`get_map_visual`의 `map_metadata.map_age_sec`으로 보고합니다.

선택한 토픽을 수신하지 못하면 다른 지도로 자동 전환하지 않습니다. 지도 메시지의
`frame_id`는 `map_frame`과 일치해야 하며, 불일치 시 분석·탐색·양보 후보 계산을
차단합니다. 헤더가 없는 레거시 메시지는 경고와 함께 호환성을 유지합니다. 대시보드는
기존 `/map`, `/lio/grid_map` 다중 구독 동작을 유지합니다.

## 실행 방식

`rclaw launch`의 네이티브 실행은 `RMW_IMPLEMENTATION`이 지정되지 않으면
`rmw_cyclonedds_cpp`를 기본으로 사용합니다. 캐시 `.env`의 비어 있지 않은 설정은
호스트 환경변수보다 우선하며, 명시한 `CYCLONEDDS_URI`는 유지합니다. 시작 로그의
`ROS 2 RMW 구현` 항목에서 선택된 구현을 확인합니다.

discovery의 토픽·서비스·액션 목록은 실행 중인 노드의 ROS 그래프에서 직접
조회합니다. 액션 탐색은 별도의 `ros2 action list` 프로세스나 CLI daemon을 사용하지
않습니다. 시작 직후 목록이 비어 있으면 다음 주기에서 다시 탐색합니다.

### 1. 로컬 개발: `./rclaw run` / `./rclaw sim`

```bash
cp .env.example .env
task build
./rclaw run
```

시뮬레이션:

```bash
./rclaw sim
./rclaw sim --nav2
./rclaw sim --slam
```

주요 `.env` 예시:

```dotenv
RC_ROBOT_CONFIG=stretch3
RC_LLM_PROVIDER=ollama
RC_LLM_MODEL=qwen3:4b
RC_OLLAMA_BASE_URL=http://127.0.0.1:11434
RC_USE_VISION=false
RC_HTTP_HOST=127.0.0.1
RC_HTTP_PORT=8080
ROBO_CLAW_GRPC_PORT=50052
```

`./rclaw run`/`./rclaw sim`의 우선순위는 다음과 같습니다.

```text
명시적 ros2 launch assignment
  > rclaw positional/option 값
  > .env
  > rclaw 기본값
```

`./rclaw run`과 `./rclaw sim`은 Go CLI의 `ParseEnvFile`로 `.env`를 읽습니다.
shell의 `source`를 실행하지 않으므로 명령 치환이나 shell 표현식 평가에 의존하지 않습니다.
HTTP 토큰 등 매핑 테이블의 값은 `.env`에서 읽으며, 셸의 `export`만으로 launch 인수에 반영되지 않습니다.

### 2. 중앙 설정: RoboClaw CLI (`rclaw`)

```bash
./rclaw fetch butler office
./rclaw config-doctor butler office
./rclaw config-effective butler office
./rclaw launch butler office --dry-run
./rclaw launch butler office
```

Docker 실행:

```bash
./rclaw launch butler office --docker --dry-run
./rclaw launch butler office --docker --pull --image-tag 20260910
```

`fetch`는 v2 manifest를 먼저 확인하고 manifest의 `config`를 `config.json`의
원본으로 사용합니다. secret은 별도 `.env` 다운로드에서 주입됩니다. v2를
지원하지 않는 구형 서버인 경우에만 v1 API로 자동 fallback합니다.

`config-effective`는 다음 값을 표시합니다.

- 최종 값
- `.env` 또는 `config.json` 출처
- 누락된 값
- secret masking 상태

`--dry-run`은 ROS 또는 Docker를 실행하지 않고 최종 launch command를 출력합니다.
실제 로봇 실행 전에 반드시 사용하세요. command 인자 중 token/API key는 `********`로
마스킹되어 출력됩니다.

#### 구동 시 적용 설정 요약과 로그

`./rclaw launch`는 기동 직전에 설정 서버에서 받아 실제로 적용한 값을 섹션별로
콘솔에 출력하고, 같은 내용을 아래 경로에 로그로 남깁니다.

```text
~/.robo_claw/config_cache/<robot>_<env>/logs/applied_config_<YYYYmmdd_HHMMSS>.log
```

로그에는 다음이 포함됩니다.

| 항목           | 예시                                                           |
| :------------- | :------------------------------------------------------------- |
| 실행 프로파일  | 설정 출처(원격/캐시), `config_revision`, 캐시 동기화 시각, 로봇/환경, `ROS_DOMAIN_ID`, `agent_id`, 실행 모드 |
| LLM            | 공급자/모델/엔드포인트, `ollama_options_json`의 키별 값(`num_ctx`, `temperature` 등) |
| 임베딩 · RAG   | 임베딩 모델/엔드포인트, 벡터 백엔드, Qdrant URL/컬렉션/타임아웃, Top-K, 유사도 임계값 |
| 채널 · HTTP · gRPC | Discord/Telegram/Slack 활성화, gRPC 서버/클라이언트와 peer 수, HTTP 포트/속도 제한/스킬 allowlist 수 |
| 센서 · 비전    | 카메라·그리퍼·LiDAR·IMU 토픽, 비전 사용 여부와 모델 경로 |
| 에이전트 · 태스크 | 태스크 큐/분해/strict config/자가학습, 메모리·워크스페이스 경로, CLOiD 스위치 |
| 설정 파일      | 마운트되는 `ROBOT.md`, `SKILLS.md`, `TROUBLESHOOTING.md`, `ROBOT_LIMITS.json`의 존재 여부와 크기 |

각 줄에는 값의 출처(`config.json`, `.env`, `runtime`)가 함께 표시됩니다. 서버 값이
`.env` override로 바뀐 항목과, launch 경로가 사용하지 않는 `.env` 값(예: `RC_LLM_MODEL`)을
바로 구분할 수 있습니다. token/API key는 `********`로 마스킹되며 로그 파일 권한은 `0600`,
보관 개수는 최근 30개입니다.

```bash
# 기동 없이 캐시된 설정의 요약만 다시 확인
./rclaw config-summary butler office

# 요약 출력을 생략하고 기동
./rclaw launch butler office --no-config-summary
```

#### 로봇 없이 gRPC와 LLM을 실행합니다

`--mock-robot`은 기존 서버 동기화·캐시·모델·프롬프트·인증·RAG 설정을 유지하면서
로봇 의존 스킬을 가짜 성공으로 대체합니다. 로컬 네이티브 실행에서만 지원하며,
Gazebo·Nav2·Docker 옵션과 함께 사용하지 않습니다.

```bash
source /opt/ros/jazzy/setup.bash
source install/setup.bash
./rclaw launch former seongmin_local --mock-robot
```

core·manipulator·vision·깊이 relay·peer client 및 하드웨어용 RosGrpc(50051)를
비활성화합니다. RoboMessenger(기본 50052)는 기존 `enable_grpc`와 토큰 설정을
따르므로 talk에서 같은 IP·포트·토큰으로 연결합니다.

로봇 의존 스킬은 정책과 입력 스키마를 검증한 뒤 실제 실행·로봇 사전조건 확인 없이
`success=true`, `mocked=true`, `executed=false`와 호출 인자를 반환합니다.
배터리·좌표·카메라 값이나 파일을 만들어 반환하지 않습니다. 시간·로컬 파일·RAG
스킬은 실제 실행하되, 임의 스크립트·현재 위치 조회·외부 플러그인은 보수적으로
대체합니다. 가짜 스킬 결과는 스킬 학습 이력에 기록하지 않습니다.

이 옵션은 스킬 성공 처리 경로를 확인하는 용도이며 실제 로봇 동작 검증을 대신하지
않습니다. RAG·MCP 초기화와 LLM 서버 등 로봇 이외의 외부 의존성은 기존 config를
그대로 사용합니다. 옵션을 빼면 기존 실제 실행으로 돌아갑니다.

### 3. 직접 `ros2 launch`

직접 실행은 고급 디버깅 용도입니다.

```bash
ros2 launch robo_claw_bringup robo_claw.launch.py \
  robot_config:=former \
  llm_provider:=ollama \
  llm_model:=llama3.2 \
  use_vision:=false
```

이 방식은 root `.env`나 AI Config Server를 자동으로 읽지 않습니다. 운영 실행에는
`./rclaw launch` 또는 `./rclaw run`을 사용하세요.

## 설정 우선순위

최종 runtime 값은 실행 방식에 따라 다음 순서로 결정됩니다.

### CLI 운영 실행

```text
명시적 CLI flag
  > local override/environment
  > v2 server manifest
  > robot profile YAML
  > application default
```

### `./rclaw run` / `./rclaw sim`

```text
명시적 launch assignment
  > command option
  > .env
  > script default
```

### ROS YAML

```text
launch override
  > selected robot_config YAML
  > agent.yaml
  > node code default
```

## 주요 설정 그룹

### LLM

| 키                       | 설명                                     |
| ------------------------ | ---------------------------------------- |
| `RC_LLM_PROVIDER`        | `azure`, `openai`, `anthropic`, `ollama` |
| `RC_LLM_MODEL`           | 대화 모델 이름                           |
| `AZURE_OPENAI_ENDPOINT`  | Azure endpoint                           |
| `AZURE_OPENAI_API_KEY`   | Azure secret                             |
| `OPENAI_API_KEY`         | OpenAI secret                            |
| `ANTHROPIC_API_KEY`      | Anthropic secret                         |
| `RC_OLLAMA_BASE_URL`     | Ollama endpoint                          |
| `RC_OLLAMA_OPTIONS_JSON` | Ollama options object                    |

### RAG와 task

| 키                                | 설명                               |
| --------------------------------- | ---------------------------------- |
| `RC_ENABLE_RAG`                   | RAG 활성화                         |
| `RC_RAG_VECTOR_BACKEND`           | `local` 또는 `qdrant`              |
| `RC_RAG_TOP_K`                    | 검색 결과 수                       |
| `RC_RAG_SCORE_THRESHOLD`          | 0~1 score threshold                |
| `RC_ENABLE_SKILL_LEARNING`        | skill learning 활성화              |
| `RC_ENABLE_TASK_DECOMPOSITION`    | 복합 명령 분해                     |
| `RC_TASK_DECOMPOSITION_MAX_STEPS` | 최대 하위 단계                     |
| `RC_STRICT_CONFIG`                | 필수 파일 누락 시 기동 실패        |
| `RC_LLM_FAIL_FAST`                | LLM health check 실패 시 기동 실패 |

운영 환경에서는 `RC_STRICT_CONFIG=true`, `RC_LLM_FAIL_FAST=true`를 권장합니다.

### HTTP와 gRPC

| 키                           | 설명                                  |
| ---------------------------- | ------------------------------------- |
| `RC_HTTP_HOST`               | HTTP bind address                     |
| `RC_HTTP_PORT`               | HTTP port                             |
| `RC_HTTP_READONLY_TOKEN`     | read-only token                       |
| `RC_HTTP_CONTROL_TOKEN`      | control token                         |
| `RC_HTTP_ALLOWED_CIDRS_JSON` | 허용 CIDR array                       |
| `ROBO_CLAW_GRPC_PORT`        | RoboMessenger server port, 기본 50052 |
| `GRPC_PEER_TOKEN`            | peer 인증 token                       |
| `GRPC_PEER_TOKENS_JSON`      | peer별 token object                   |
| `GRPC_TARGET_PEERS_JSON`     | 연결 대상 peer array                  |

token이 없으면 RoboMessenger는 loopback에만 bind됩니다. 원격 peer를 사용하려면
강한 random token을 설정하세요.

### Vision과 센서

| 키                                   | 설명                    |
| ------------------------------------ | ----------------------- |
| `RC_USE_VISION`                      | ONNX detector 활성화    |
| `RC_VISION_MODEL_PATH`               | 모델 파일 경로          |
| `RC_CAMERA_TOPIC`                    | head camera override    |
| `RC_GRIPPER_CAMERA_TOPIC`            | gripper camera topic    |
| `RC_GRIPPER_VISION_MAX_INFERENCE_HZ` | gripper inference limit |
| `RC_LIDAR_TOPIC`                     | 진단용 lidar topic      |
| `RC_IMU_TOPIC`                       | 진단용 IMU topic        |

`RC_USE_VISION=true`이면 모델 파일이 실제로 존재해야 합니다.

## AI Config Server 사용 절차

1. 대시보드에서 `robot_name`과 `environment`를 선택합니다.
2. profile을 생성하거나 기존 profile을 복제합니다.
3. provider, model, RAG, channel, safety, vision 값을 입력합니다.
4. JSON 입력 필드가 있다면 array/object schema를 확인합니다.
5. 저장합니다. 일반 수정은 활성 상태를 변경하지 않습니다.
6. 별도의 활성화 작업으로 해당 profile을 활성화합니다.
7. 로봇에서 `fetch`를 실행합니다.
8. `config-doctor`와 `launch --dry-run`으로 확인합니다.
9. 검증이 끝난 뒤 실제 launch를 실행합니다.

활성 설정 수정과 활성화는 별도 작업입니다. 실수로 다른 profile을 실행하지
않도록 robot/environment를 항상 확인하세요.

## 문제 해결

### 서버 설정을 가져오지 못함

```bash
./rclaw config-doctor butler office
```

- HTTPS 인증서와 server host/port 확인
- `ROBO_CLAW_CLI_DEVICE_TOKEN` 확인
- 구형 서버라면 v1 fallback 로그 확인
- cache가 있으면 `config-effective` 실행

### 설정값이 기대와 다름

```bash
./rclaw config-effective butler office
./rclaw launch butler office --dry-run
```

`.env`가 `config.json`보다 우선된 값인지, robot YAML이 launch override로
덮어쓴 값인지 확인하세요.

### 시뮬레이션에서 토픽 설정이 반영되지 않음

`./rclaw sim`을 사용하고, 직접 launch를 사용한다면 `camera_topic`, `depth_topic`,
`pointcloud_topic`, `lidar_topic`, `imu_topic`을 명시적으로 전달하세요.

### 구동 당시 어떤 설정이었는지 확인

기동할 때마다 적용 설정 요약이 `~/.robo_claw/config_cache/<robot>_<env>/logs/`에
`applied_config_<YYYYmmdd_HHMMSS>.log`로 남습니다. 최신 로그를 확인한 뒤, 현재
캐시 상태와 비교합니다.

```bash
ls -1t ~/.robo_claw/config_cache/<robot>_<env>/logs | head
./rclaw config-summary <robot> <env>
```

로그의 `config_revision`과 캐시 동기화 시각으로 어느 설정을 받아 구동했는지
확인할 수 있고, 항목별 출처(`config.json` / `.env` / `runtime`)로 override 경로를
추적할 수 있습니다. 로그에 secret 값은 기록되지 않습니다.

### secret이 노출된 것 같음

- CLI 출력에 secret을 직접 넣지 마세요.
- cache 파일 권한은 directory `0700`, 파일 `0600`이어야 합니다.
- device token과 provider API key를 즉시 회전하세요.
- `config.json`, `.env`, backup 파일을 공유하지 마세요.

## 설정 변경 후 체크리스트

- [ ] robot/environment가 올바른가
- [ ] profile이 활성화되어 있는가
- [ ] provider와 credential 조합이 올바른가
- [ ] model/path가 실제 존재하는가
- [ ] limits와 safety 설정이 올바른가
- [ ] HTTP/gRPC bind와 token이 안전한가
- [ ] `config-doctor`가 통과했는가
- [ ] `launch --dry-run` 결과를 검토했는가
- [ ] 적용 설정 요약의 `config_revision`과 `.env` override가 의도한 값인가
- [ ] simulation 또는 mock 환경에서 먼저 검증했는가

## 새 설정 추가 시 수정 위치

새 중앙 설정을 추가하기 전에 공통 contract release와 vendored lock을 확인합니다.

```bash
./rclaw contract check
```

공통 contract 원본은 다음 저장소의 파일입니다.

```text
rcf-config-contract/contract/runtime-config.yaml
```

생성 결과는 다음에 저장됩니다.

```text
config-schema/generated/runtime-config.schema.json
config-schema/generated/runtime-contract.json
config-schema/generated/CONFIG_REFERENCE.md
robo_claw_cli/contract/runtime_config_generated.go
src/robo_claw_bringup/launch/_generated_runtime_config.py
scripts/generated_runtime_env.sh
```

생성 파일은 직접 수정하지 않습니다. 공통 contract release를 갱신한 뒤
`./rclaw contract update --from /path/to/release-bundle`을 실행합니다.
이 명령이 contract와 lock을 갱신하고 소비자 코드를 자동 생성합니다.
Go contract 파일은 CLI가 사용할 canonical field metadata를 담고 있습니다.
launch contract와 shell mapping은 다음 단계의 runtime wiring에서 사용할
generated metadata입니다. `rclaw`(launch/run/sim)와 CLI의 gRPC port mapping은 이미 이 generated
metadata를 사용합니다. Dart contract는 AI Config Server가 자체 vendored contract
generator로 생성합니다.

`robo_claw`만 단독으로 clone해도 vendored contract, CLI, launch, shell 검증이 실행됩니다.
AI Config Server artifact는 AI Config Server 저장소가 자체 vendored contract로
생성하므로 sibling 저장소가 필요하지 않습니다.

공통 contract를 vendoring한 저장소에서는 로컬 설정을 직접 변경하지 않습니다.
source of truth는 `rcf-config-contract/contract/runtime-config.yaml`이며, 공통
저장소에서 release를 만든 뒤 다음 명령으로 소비 저장소를 갱신합니다.

```bash
# 로컬 릴리즈 번들 경로에서 갱신 및 코드 자동 생성:
./rclaw contract update --from /path/to/release-bundle

# 또는 공개 GitLab Release 직접 갱신:
./rclaw contract update --version v2.0.1
```

업데이트 후 일괄 검증을 수행합니다.

```bash
./rclaw contract check
```

새 설정은 먼저 어느 계층의 소유인지 결정해야 합니다. 같은 값을 여러 계층에
중복 추가하지 마세요.

### 중앙 runtime profile 설정

AI Config Server UI에서 설정하고 CLI로 배포할 값은 다음 순서로 수정합니다.

1. `rcf-config-contract`에서 필드·타입·기본값·환경변수·API/launch/ROS 매핑을 정의하고 release합니다.
2. 이 저장소에서 `./rclaw contract update --from /path/to/release-bundle` 또는 `--version`으로 갱신합니다.
3. 생성 metadata를 사용하는 CLI·launch·shell consumer를 확인합니다. 설정별로 `robo_claw_cli/cmd/types.go`,
   `robo_claw_cli/launcher/types.go`, `launcher/args.go`, `cmd/run.go`와 실제 노드 소비 코드를 검토합니다.
4. 공통 launch 선언은 `src/robo_claw_bringup/launch/_common_launch_args.py`, 배선은
   `robo_claw.launch.py`와 `robo_claw_sim.launch.py`에서 확인합니다.
5. AI Config Server의 UI·schema·배포 artifact는 해당 저장소의 vendored contract와 generator로 갱신합니다.
   이 저장소에서 sibling 서버의 DB·Flutter 파일 경로나 구현 언어를 가정하지 않습니다.
6. `.env.example`·설정 문서·mapping 테스트를 갱신하고 `task contract-check`, `task unit-test`를 실행합니다.

### 로봇 profile YAML 설정

특정 로봇의 topic, URDF, hardware, manipulation tuning처럼 중앙 서버에서
공통으로 관리하지 않을 값은 다음만 수정합니다.

1. `src/robo_claw_bringup/config/<robot>_config.yaml`
2. 필요하면 `agent.yaml`의 공통 agent parameter
3. launch에서 해당 YAML을 읽고 override하는지 확인
4. `ROBOT_LIMITS.<robot>.json`과 관련 문서 동기화
5. 해당 robot의 simulation 또는 hardware-like 테스트 추가

### local/deployment 전용 설정

ROS distro, DDS, 모델 저장 경로, workspace, Docker mount처럼 로봇 호스트마다
달라지는 값은 중앙 DB model에 추가하지 않습니다.

- `.env.example`
- `robo_claw_cli/launcher/docker.go`
- `sim/humble/env.sh` 또는 `sim/jazzy/env.sh`

### 필수 테스트

설정 하나를 추가할 때 최소한 다음 테스트를 함께 추가합니다.

- AI Config Server handler create/update validation test
- v2 runtime manifest response test
- Flutter model round-trip test
- CLI manifest/DTO decode test
- CLI launch argument mapping test
- launch argument 전달 test
- `rclaw sim` forwarding test

설정이 실제 ROS parameter로 소비된다면 최종적으로 다음 경로를 검증해야 합니다.

```text
DB model
 -> API response
 -> v2 manifest/.env
 -> CLI cache
 -> launch argument
 -> ROS parameter
 -> node consumer
```


### Ollama 도구 호출의 대화 컨텍스트

Ollama 네이티브 도구 호출은 [공식 agent loop 예제](https://docs.ollama.com/capabilities/tool-calling#python)에 따라 assistant 메시지의 `thinking`, `content`, `tool_calls`를 같은 작업의 후속 요청에 함께 전달합니다. 사용자에게 보여주는 답변에서는 사고 과정 태그를 제거하지만, 후속 도구 호출에 사용하는 원본 메시지는 보존합니다.

각 도구 결과는 `role: tool`, `tool_name`, `content`로 연결합니다. 실행 실패와 안전 차단도 도구 결과로 기록하고, 체인이 실패하여 후속 호출을 실행하지 않은 경우에는 `NotExecuted` 결과를 전달합니다. 컨텍스트 보존은 기존 스킬 검증과 안전 정책을 거친 실행 흐름에 적용합니다.


네이티브 도구 호출이 활성화되어 있고 프로바이더가 이를 지원하면, 복합 요청도 원문 그대로 하나의 도구 루프에서 처리합니다. 별도 명령 분해기를 호출하거나 단계마다 대화를 새로 만들지 않습니다. 따라서 도구 호출과 결과 이력이 다음 라운드에 이어지며, 단계 전환을 위한 `[전체 명령]`·`[이전 단계 요약]` system 메시지를 추가하지 않습니다. 네이티브 도구 호출을 사용하지 않는 경로에서는 기존 `enable_task_decomposition` 설정을 적용합니다.

스위치는 `llm_tool_calling`(`RC_LLM_TOOL_CALLING`)이며 계약 `llm.tool_calling`으로 AI Config Server 프로필에서 선택합니다. 기본값은 `true`이고, provider가 도구 호출을 지원하지 않으면 값과 무관하게 JSON 경로로 폴백합니다. 복합 명령 처리를 분해기 경로로 되돌리려면 이 값을 `false`로 두고 `RC_ENABLE_TASK_DECOMPOSITION`을 유지합니다.
