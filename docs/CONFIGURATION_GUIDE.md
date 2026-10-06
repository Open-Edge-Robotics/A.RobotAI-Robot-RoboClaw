# RoboClaw Configuration Guide

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

## 실행 방식

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

`.env`는 shell 문법으로 source되므로 신뢰할 수 있는 파일만 사용하세요.

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
실제 로봇 실행 전에 반드시 사용하세요.

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
- [ ] simulation 또는 mock 환경에서 먼저 검증했는가

## 새 설정 추가 시 수정 위치

새 중앙 설정을 추가하기 전에 공통 contract release와 vendored lock을 확인합니다.

```bash
./robo_claw_cli contract check
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
`contract-update`와 `generate`를 실행합니다.
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
./robo_claw_cli contract update --from /path/to/release-bundle

# 또는 공개 GitLab Release 직접 갱신:
./robo_claw_cli contract update --version v2.0.1
```

업데이트 후 일괄 검증을 수행합니다.

```bash
./robo_claw_cli contract check
```

새 설정은 먼저 어느 계층의 소유인지 결정해야 합니다. 같은 값을 여러 계층에
중복 추가하지 마세요.

### 중앙 runtime profile 설정

AI Config Server UI에서 설정하고 CLI로 배포할 값은 다음 순서로 수정합니다.

1. **영속 모델**
   - `ai-config-server/database/models/roboclaw_config.go`
   - JSON field name, Go type, DB default를 추가합니다.
2. **입력 검증**
   - `ai-config-server/handler/roboclaw_config.go`
   - enum, 범위, 의존성, JSON shape를 추가합니다.
3. **생성 artifact**
   - `ai-config-server/handler/config_files.go`
   - RoboClaw `.env` key 또는 runtime file mapping을 추가합니다.
4. **v2 schema**
   - `ai-config-server/schemas/robo-claw-runtime-config-v2.schema.json`
   - type, required condition, enum, minimum/maximum을 추가합니다.
5. **프론트엔드 model**
   - `ai-config-server/frontend/lib/models/config_model.dart`
   - constructor, `fromJson`, `toJson`, default를 추가합니다.
6. **프론트엔드 form**
   - `ai-config-server/frontend/lib/screens/config_tab.dart`
   - controller/state, load, save를 추가합니다.
   - 관련 section widget에도 입력 필드를 추가합니다.
7. **RoboClaw CLI DTO**
   - `robo_claw/robo_claw_cli/cmd/types.go`
   - `robo_claw/robo_claw_cli/launcher/types.go`
   - 두 DTO의 field/tag를 동일하게 유지합니다.
8. **launch mapping**
   - `robo_claw/robo_claw_cli/launcher/args.go`
   - `.env` 또는 v2 config를 ROS launch argument로 변환합니다.
9. **ROS launch 선언과 소비**
   - 공통 설정이면 `robo_claw/src/robo_claw_bringup/launch/_common_launch_args.py`
   - 실제 wiring은 `robo_claw.launch.py`
   - simulation도 지원하면 `robo_claw_sim.launch.py` forwarding을 함께 수정합니다.
10. **local 실행 mapping**
    - `robo_claw_cli/cmd/run.go`
    - local `.env` key를 launch argument로 변환합니다.
11. **예시와 문서**
    - `robo_claw/.env.example`
    - 이 문서와 `ai-config-server/docs/CONFIGURATION_GUIDE.md`

### 로봇 profile YAML 설정

특정 로봇의 topic, URDF, hardware, manipulation tuning처럼 중앙 서버에서
공통으로 관리하지 않을 값은 다음만 수정합니다.

1. `robo_claw/src/robo_claw_bringup/config/<robot>_config.yaml`
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
