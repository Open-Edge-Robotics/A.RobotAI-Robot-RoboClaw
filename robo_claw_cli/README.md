# RoboClaw CLI (`rclaw`)

로봇 구동(Bringup) 환경 상태를 검증하고 AI 에이전트 성능을 시나리오 기반으로 자동화 진단하기 위한 Go 기반의 CLI 도구입니다. 프로젝트 루트에서 `task build`로 빌드하여 `./rclaw`로 바로 실행할 수 있습니다. 설정 서버(`config.yaml`의 `server.host`/`server.port`)로부터 로봇/환경별 활성 설정 프로필과 테스트 시나리오를 동기화하고, 그 설정으로 로봇 시스템을 직접 기동할 수도 있습니다.

---

## 사전 준비

- **Go 1.26+** (`go.mod`에 명시된 최소 버전. `go version`으로 확인)
- **[Task](https://taskfile.dev)** (`Taskfile.yml` 기반 빌드/테스트 실행에 필요. 없어도 `go build`로 직접 빌드는 가능)

### Task 설치

아래 중 편한 방법을 하나 선택하세요.

```bash
# 1. 공식 설치 스크립트 (권장) — ~/.local/bin 에 설치
sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b ~/.local/bin
# ~/.local/bin이 PATH에 없다면 셸 설정(.bashrc/.zshrc 등)에 추가:
#   export PATH="$HOME/.local/bin:$PATH"

# 2. Go 툴체인이 있다면 go install로도 설치 가능
go install github.com/go-task/task/v3/cmd/task@latest

# 3. Snap 패키지 (Ubuntu 등)
sudo snap install task --classic
```

설치 확인:

```bash
task --version
```

---

## 빌드

프로젝트 루트의 `Taskfile.yml`에 정의된 태스크로 빌드하는 것을 권장합니다(의존성 정리 `go mod tidy` 자동 포함).

```bash
# 프로젝트 루트에서:
task build              # 호스트 아키텍처용 최적화 바이너리(./rclaw) 빌드
task build-all          # linux/amd64 + linux/arm64 바이너리를 bin/ 에 빌드
task test               # 전체 유닛 테스트 실행
task contract-check     # 설정 컨트랙트 lock 및 drift 일괄 검증
task fmt                # go fmt 전체 적용
task vet                # go vet 정적 분석
task clean              # 빌드 산출물(./rclaw, bin/) 삭제
task --list-all         # 사용 가능한 전체 태스크 목록 확인
```

| 태스크           | 설명                                                                      |
| ---------------- | ------------------------------------------------------------------------- |
| `build` (기본)   | 호스트 아키텍처용 최적화 빌드 (`-ldflags="-s -w"`), 루트에 `./rclaw` 생성 |
| `build-all`      | `build-linux-amd64` + `build-linux-arm64` 동시 실행 (`bin/`에 저장)       |
| `test`           | `go test -v ./...` 실행                                                   |
| `contract-check` | `./rclaw contract check` 실행                                             |
| `fmt`            | `go fmt ./...`                                                            |
| `vet`            | `go vet ./...`                                                            |
| `clean`          | `rclaw` 바이너리 및 `bin/` 삭제                                           |

Task 없이 직접 빌드하려면:

```bash
cd robo_claw_cli && go build -o ../rclaw .
```

---

## 주요 명령어 및 실행 예시

### 설정 컨트랙트 관리 (`contract`)

RoboClaw는 `contracts/contract.lock.json`으로 공통 `rcf-config-contract` release를
고정합니다. 설정 계약(lock), 환경변수 문서화(.env.example), 생성 코드를 일괄 점검하려면
다음 명령을 사용합니다.

```bash
./rclaw contract check
```

공통 contract release를 갱신할 때는 다음 명령을 사용합니다. (다운로드 직후 아티팩트 코드가 자동 생성 및 동기화됩니다.)

```bash
# 로컬 릴리즈 번들 경로에서 갱신:
./rclaw contract update --from /path/to/release-bundle

# 또는 공개 GitLab Release HTTPS 직접 다운로드:
./rclaw contract update --version v2.0.1
```

`--version`은 `wkqco33/rcf-config-contract`의 GitLab Release를 HTTPS로
다운로드하고 checksum lock을 갱신한 뒤 관련 소비자 코드(Go, Python, Bash, Schema, Docs)를
자동 생성합니다. 네트워크가 없는 환경에서는 `--from` local bundle을 사용합니다.

생성 파일은 `config-schema/generated/`, `src/robo_claw_bringup/launch/_generated_runtime_config.py`,
`scripts/generated_runtime_env.sh`, `robo_claw_cli/contract/runtime_config_generated.go`에
위치하며 직접 편집하지 않습니다.

### 설정 진단 (`config-doctor`)

ROS를 실행하지 않고 v2 runtime manifest의 schema, revision, LLM, ROS domain
설정을 검증합니다. `fetch`는 v2 manifest를 `config.json`의 설정 원본으로
사용하고 secret은 `.env`에서 읽습니다. 구형 ai-config-server는 v1 endpoint로
자동 fallback합니다.

```bash
./rclaw config-doctor butler office
```

운영 서버 연결은 HTTPS가 기본입니다. 로컬 개발 서버에서만
`server.allow_insecure_http: true`를 명시하세요.

### 1. 원격 설정 목록 조회 (`list`)

설정 서버에 등록된 설정 프로필 목록을 표로 조회합니다. 로봇 이름과 환경명이 모두 필요합니다.

```bash
./rclaw list --robot former --env lab2
```

프로필 ID, 이름, 대상 로봇/환경, 활성화 여부, LLM 제공자/모델, RAG 사용 여부가 표로 출력됩니다.

### 2. 원격 설정 동기화 및 로컬 캐싱 (`fetch`)

원격 설정 서버에 접속하여 해당 로봇 및 구동 환경에 활성화된 설정을 다운로드하고 로컬 디렉토리(`~/.robo_claw/config_cache/<robot>_<env>/`)에 캐싱합니다. `config.json`, `.env`, `ROBOT.md`, `SKILLS.md`, `TROUBLESHOOTING.md`, `ROBOT_LIMITS.json`, (있다면) `scenario.json`까지 함께 저장됩니다.

```bash
# 사용법: ./rclaw fetch [robot_name] [environment]
./rclaw fetch butler office
```

### 3. 원격 설정으로 로봇 시스템 기동 (`launch`)

`fetch`와 동일하게 원격 설정을 동기화(실패 시 로컬 캐시로 자동 폴백)한 뒤, 그 설정으로 RoboClaw 시스템을 **로컬 네이티브 프로세스(`ros2 launch`) 또는 Docker 컨테이너**로 직접 기동합니다. `./rclaw run` / `./rclaw sim`이 로컬 `.env`를 쓰는 것과 달리, `launch`는 설정 서버에서 받아온 프로필(LLM 제공자/모델, RAG, HTTP 보안, MCP, gRPC 피어, 카메라/비전, 태스크 큐 등 전체 파라미터)을 그대로 launch 인자로 변환해 전달합니다.

AI config의 `agent_id`는 `agent_id:=...` launch 인자로 전달되고, `grpc_target_peers_json`은 `target_peers_json:=...`으로 전달됩니다. `agent_id`가 없으면 RoboClaw launch의 기본값 `robo_claw_agent`가 사용되므로 다중 로봇에서는 각 config에 고유한 값을 저장해야 합니다. `allow_remote_task_execution`은 안전상 launch/config 기본 권한으로 자동 활성화하지 않으며 `autonomous_cooperate` 스킬 호출 파라미터로만 적용됩니다.

```bash
# 사용법: ./rclaw launch [robot_name] [environment] [flags]

# 로컬 네이티브로 기동
./rclaw launch butler office

# Docker 컨테이너로 기동 (lgecloudroboticstask/robo-claw:latest 이미지 사용)
./rclaw launch butler office --docker

# 시뮬레이션 모드 + Nav2
./rclaw launch butler office --sim --nav2

# 시뮬레이션 모드 + SLAM(자동으로 Nav2도 함께 활성화됨) + 월드/모델/맵 지정
./rclaw launch butler office --sim --slam --world my_world --model waffle --map my_map.yaml
```

주요 플래그:

- `--docker`: Docker 컨테이너 기반으로 기동 (미지정 시 로컬 `ros2 launch`)
- `--sim`: 시뮬레이션 launch 파일(`robo_claw_sim.launch.py`) 사용
- `--nav2`: (시뮬레이션) Nav2 내비게이션 활성화
- `--slam`: (시뮬레이션) SLAM + Nav2 동시 활성화 (`--nav2`보다 우선)
- `--world <name>`, `--model <name>`, `--map <file>`: (시뮬레이션) 월드/로봇 모델/맵 파일 지정
- `-d, --debug`: ROS 2 디버그 로깅 활성화

동작 방식 메모:

- HTTP 채널 포트(`RC_HTTP_PORT`, 기본 8080)가 이미 사용 중이면 자동으로 다른 포트를 찾아 사용합니다.
- Docker 모드는 `lgecloudroboticstask/robo-claw:latest` 이미지를 `--network host --privileged`로 실행하며, X11 포워딩(`xhost +local:docker`)과 시리얼 장치(`/dev/ttyUSB0`, `/dev/ttyACM0`) 마운트를 자동 처리합니다.
- 비전 모델 경로(`RC_VISION_MODEL_PATH`)가 Docker 모드에서 프로젝트 `models/` 아래 있으면 컨테이너 경로(`/ros2_ws/models/...`)로 자동 변환됩니다.

### 4. 시나리오 기반 자동화 테스트 실행 (`test`)

로봇 및 AI 에이전트의 능력을 순차적 시나리오에 맞춰 검증하고 마크다운 보고서(`test_report_*.md`)를 작성합니다.
카메라 획득 및 이미지 분석/지도 분석 단계에서 다운로드받은 테스트 이미지는 로컬 실행 경로 아래 `report_images/` 디렉토리에 실시간 보관되며, 생성된 마크다운 보고서 표 내부에 자동으로 이미지 링크가 삽입됩니다.

```bash
# 기본 사용법
./rclaw test -r butler -e office

# 외부 사용자 정의 테스트 시나리오 JSON 파일을 사용해 구동할 때
./rclaw test -r butler -e office --scenario-file ./my_custom_scenario.json

# 주요 옵션 플래그
# -r, --robot: 대상 로봇 이름 (예: butler)
# -e, --env: 대상 구동 환경 (예: office)
# -n, --run-navigation: 네비게이션 주행 및 속도 제약 테스트 수행 여부
# -m, --run-manipulation: 매니퓰레이션 관절 제어 테스트 수행 여부
# -f, --fetch-latest: 실행 전 강제로 원격 설정 서버에서 설정을 새로 고침
# -o, --output: 마크다운 보고서 저장 경로 커스터마이징
```

#### 💡 원격 시나리오 자동 연동 및 폴백(Fallback) 흐름

`test` 명령어 기동 시 `--scenario-file` 플래그를 통해 로컬 파일을 명시하지 않는 경우, 아래의 동적 폴백 흐름으로 테스트를 수행합니다.

1. **서버 동기화**: 설정 서버 API(`/api/v1/scenarios/active`)를 호출하여 해당 로봇/환경에 배포 활성화된 테스트 시나리오 JSON을 가져와 캐싱 및 사용합니다.
2. **로컬 캐시 재사용**: 네트워크 에러 등으로 서버 통신이 실패할 경우, 이전에 캐싱해 둔 로컬 캐시 폴더 내 `scenario.json` 파일을 찾아 실행합니다.
3. **내장 디폴트 시나리오**: 로컬 캐시 파일마저 없는 첫 구동 시, 소스 내에 기본 내장된 디폴트 검증 시나리오로 폴백하여 안정적으로 실행을 이어나갑니다.

### 5. 로컬 설정 확인/관리 (`config`)

`rclaw` 자체의 로컬 설정(`config.yaml`)을 **플랫폼별 기본 폴더**에 저장하고, 커맨드로 쉽게 확인하고 바꿀 수 있습니다.

```bash
./rclaw config            # 현재 유효(적용 중) 설정값 확인
./rclaw config path       # 설정 파일 경로 및 플랫폼 출력
./rclaw config show       # 현재 유효 설정값 출력 (환경변수 반영)
./rclaw config raw        # 디스크의 config.yaml 원본 출력
./rclaw config init       # 기본 config.yaml을 기본 폴더에 생성
./rclaw config set server.host 10.0.0.1   # 값 변경
./rclaw config edit       # $EDITOR로 설정 파일 직접 편집
```

각 커맨드에 `--path <경로>`를 주면 기본 폴더 대신 특정 파일을 대상으로 합니다.

#### 설정 저장 위치 (플랫폼별 기본 폴더)

`rclaw`는 OS 표준 사용자 설정 디렉토리 아래 `robo_claw_cli/config.yaml`을 기본 저장 경로로 사용합니다.
환경변수 `ROBO_CLAW_CLI_CONFIG_DIR`이 설정되어 있으면 그 값을 우선합니다.

| 플랫폼     | 기본 경로                                                           |
| ---------- | ------------------------------------------------------------------- |
| Linux/Unix | `$XDG_CONFIG_HOME/robo_claw_cli/config.yaml` (기본 `~/.config/...`) |
| macOS      | `~/Library/Application Support/robo_claw_cli/config.yaml`           |
| Windows    | `%AppData%\\robo_claw_cli\\config.yaml`                             |
| 덮어쓰기   | `$ROBO_CLAW_CLI_CONFIG_DIR/config.yaml`                             |

#### 설정 우선순위

`config.yaml` 로드 시 아래 순서로 파일을 병합하며, **뒤에 오는 파일이 앞을 덮어쓰고** 시스템 환경변수(`ROBO_CLAW_CLI_*`)가 최우선입니다.

1. 실행 위치(`./config.yaml`)
2. 실행 파일이 있는 곳의 `config.yaml` / `robo_claw_cli/config.yaml`
3. 플랫폼별 기본 폴더의 `config.yaml`
4. (최우선) `ROBO_CLAW_CLI_*` 환경변수

변경 가능한 키: `name`, `device_token`, `server.host`, `server.port`, `server.allow_insecure_http`, `log.level`, `contract.project`, `contract.base_url`

### 6. 로봇 초기화 (`reset-robot`)

테스트 전 로봇 상태/메모리/벡터 DB 초기화용 bash 스크립트(`scripts/reset_robot_*.sh`, `reset_memory.sh`)를
단일 CLI 입구로 실행합니다. 스크립트가 진실 소스이며, 호스트 SSH 또는 로봇 직접 실행을 그대로 위임합니다.

```bash
# config 캐시 + 로컬 메모리 삭제 (Qdrant 제외) — 호스트 SSH
./rclaw reset-robot state --robot former@10.159.172.69 --dry-run
# 로봇 직접: 컨테이너 정지 + 메모리/시맨틱/콜드 + Qdrant 컬렉션 삭제
QDRANT_URL=http://10.159.172.74:6333 COLLECTION=robo_claw_former_0045_w2_2f ./rclaw reset-robot memory -y
# config 캐시 + 메모리 + Qdrant 컬렉션 모두 (권장)
./rclaw reset-robot full --robot former@10.159.172.69 --qdrant-url http://10.159.172.74:6333 \
  --collection robo_claw_former_0045_w2_2f --dry-run
```

공용 플래그: `--robot <user@host>`, `--qdrant-url`, `--collection`, `--container`, `--restart`,
`-y`/`--yes`, `--dry-run`, `--skip-config`/`--skip-memory`/`--skip-qdrant`. 상세 옵션은
`docs/RESET_ROBOT_STATE.md`(state), `docs/RESET_ROBOT_STATE_SERVER_DB.md`(full) 참고.

### 7. 버전 / 셸 자동완성

```bash
./rclaw version               # 버전 정보 출력
./rclaw completion bash       # 셸 자동완성 스크립트 생성 (wcli 내장 커맨드)
```

---

## 설정 정보 (`config.yaml` / 환경변수)

`config.yaml`과 환경변수(`ROBO_CLAW_CLI_` 접두사)를 사용하여 CLI 동작을 제어합니다. 실행 파일과 같은 위치의 `config.yaml`뿐 아니라 **플랫폼별 사용자 기본 설정 폴더(기본 `~/.config/robo_claw_cli/config.yaml`)도 자동으로 탐색**하므로, 어느 환경에서든 동일한 로컬 설정을 사용할 수 있습니다. 기본 폴더의 파일이 실행 디렉토리의 파일보다 우선 적용되며, 로컬 저장 위치는 `rclaw config` 커맨드로 생성·확인·변경할 수 있습니다(위 `config` 절 참고).

| 설정 키        | 환경변수명                   | 기본값          | 설명                                  |
| -------------- | ---------------------------- | --------------- | ------------------------------------- |
| `name`         | `ROBO_CLAW_CLI_NAME`         | `robo_claw_cli` | 애플리케이션 이름                     |
| `device_token` | `ROBO_CLAW_CLI_DEVICE_TOKEN` | `""`            | 설정 서버 인증용 디바이스 Bearer 토큰 |
| `server.host`  | `ROBO_CLAW_CLI_SERVER_HOST`  | `0.0.0.0`       | 설정 서버 호스트 IP                   |
| `server.port`  | `ROBO_CLAW_CLI_SERVER_PORT`  | `8080`          | 설정 서버 포트                        |
| `log.level`    | `ROBO_CLAW_CLI_LOG_LEVEL`    | `info`          | 콘솔 출력 로그 레벨                   |

`device_token`처럼 민감한 값이 들어가는 `config.yaml`은 저장소에 커밋하지 말고 배포 시 환경변수(`ROBO_CLAW_CLI_DEVICE_TOKEN` 등)로 주입하는 것을 권장합니다.

---

## 내부 구조 참고

- `cmd/`: 각 서브커맨드(`list`, `fetch`, `launch`, `test`, `version`) 구현
- `launcher/`: `launch` 커맨드의 로컬/Docker 기동 로직, 원격 설정 → ROS launch 인자 변환
- `tester/`: `test` 커맨드의 시나리오 실행기 및 마크다운 리포트 생성
- `proto/`: gRPC 클라이언트 스텁 (`messenger_pb`, `robo_pb`) — 테스트 도구가 내부적으로 사용
- `wcli`: CLI 프레임워크 ([github.com/wkqco33/wcli](https://github.com/wkqco33/wcli)). 외부 Go 모듈 의존성으로 사용됩니다.
