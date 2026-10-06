# RoboClaw 🤖

OpenClaw(Node.js AI 비서 플랫폼)의 ROS2 네이티브 버전.  
자연어 명령으로 로봇을 제어하고, 실시간 인지 및 멀티 에이전트 협업이 가능한 AI 에이전트 런타임 패키지입니다.

## 🌟 주요 특징

- **멀티 LLM 지원**: Ollama(로컬), OpenAI, Azure OpenAI, Anthropic 등 다양한 모델 지원.
- **실시간 센서 연동**: 라이다, 카메라, 배터리, 위치(Odom) 등 실제 ROS2 토픽 기반 상황 판단.
- **실시간 객체 인식**: ONNX Runtime + YOLOv8로 카메라 이미지에서 COCO 80종 물체를 실시간 탐지. 기본 활성화(`use_vision:=true`), 구독자 게이팅 + Hz 상한으로 저사양 로봇에서도 부담이 적음.
- **자율 행동 (Behavior Tree)**: "스스로 행동해" 명령으로 BT 기반 자율 탐험·분석·행동 루프 시작. 배터리/탐사율 안전 조건은 BT가, 의미적 판단은 LLM이 수행.
- **지능형 플래닝**: 복잡한 명령을 하위 스킬로 분해하고, 실패 시 스스로 대안 계획(Re-planning) 수립.
- **RAG 메모리 확장**: 로컬 JSON 기반 지식 검색과 **Qdrant 벡터 DB 연동**을 모두 지원하며, 스킬 실행 경험을 스스로 교훈으로 압축해 프롬프트를 개선하는 자가 학습 루프 포함. 위치 지식은 사실(`type=location`)/관찰/스킬 로그를 구분해 저장·검색하고, 좌표→이름 역방향 조회(`identify_location`)를 제공합니다(상세: [CHANGE_LOG.md](CHANGE_LOG.md) 부록 "RAG 위치 지식 개선").
- **음성 인터페이스(HRI)**: TTS(Piper/gTTS)를 통한 음성 응답 및 STT 연동 지원.
- **물리적 안전 가드레일**: 조인트 속도 강제 제한 및 비상 정지(Emergency Stop) 기능.
- **로봇 신뢰성 향상 및 자가진단**: 물리 제약 스펙(`ROBOT_LIMITS.json`), 장애 조치 가이드(`TROUBLESHOOTING.md`) 및 실시간 자가진단 상태(`RobotHealthState`) 피드백을 통해 LLM 에이전트가 오차 한계와 하드웨어 에러 상태를 스스로 평가하고 안전하게 조치하도록 구성.
- **멀티 에이전트 협업**: 같은 ROS 네트워크의 다른 에이전트에게 작업을 위임하거나, gRPC로 연결된 원격 동료 로봇(폐쇄망 포함)과 상태를 주고받고 협업. **자율협동**(`autonomous_cooperate`)으로 동료와 지속적 자연어 대화를 주고받으며 도움 요청/지시/판단.
- **폐쇄망 최적화**: **gRPC 기반 내부 전용 메신저** 지원으로 외부 인터넷 없이도 실시간 제어 가능.
- **멀티 채널 인터페이스**: HTTP REST API는 물론, **Discord, Slack, Telegram**과 직접 연동.
- **외부 도구 연동**: MCP(Model Context Protocol) 서버에 연결해 외부 도구를 스킬처럼 호출 가능.
- **System 1 Fast Router (선택)**: 자체 호스팅 Laya 모델이 LLM 호출 전에 인사·단순 조회를 판단해 바로 처리. 규칙 라우터와 배타 선택하며 언제든 규칙으로 롤백 가능([사용법](#-system-1-fast-router-laya-선택)).

## 📦 패키지 구조

| 패키지                | 언어       | 역할                                                                                                                                                             |
| --------------------- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `robo_claw_msgs`      | IDL        | msg/srv/action 및 **gRPC(.proto)** 정의                                                                                                                          |
| `robo_claw_core`      | **C++**    | 실시간 HW 인터페이스, 속도 제한 가드레일, 50Hz 제어 루프                                                                                                         |
| `robo_claw_agent`     | **Python** | LLM 브릿지, 스킬 매니저, RAG 기반 메모리 관리, 플래너                                                                                                            |
| `robo_claw_discovery` | **Python** | ROS2 그래프 및 네트워크 내 타 에이전트 자동 탐색                                                                                                                 |
| `robo_claw_channel`   | **Python** | HTTP/gRPC/메신저(Discord 등) 통합 채널 노드, 동료 로봇(peer) gRPC 통신                                                                                           |
| `robo_claw_grpc`      | **Python** | `robo_mcp` 등 외부 시스템 연동용 `RosGrpc` 서비스(로봇 상태/내비게이션/매니퓰레이션/카메라/스킬 실행/태스크 실행 등). `robo_claw_channel`의 메신저 gRPC와는 별개 |
| `robo_claw_bringup`   | Python     | 전체 시스템 및 시뮬레이션 통합 런처                                                                                                                              |
| `robo_claw_vision`    | **C++**    | ONNX Runtime + YOLOv8 실시간 객체 인식, Detection2DArray 발행                                                                                                    |

## 🛠️ 설치 및 의존성

```bash
# 개발 도구 및 필수 라이브러리 설치
task setup-deps
```

## 🚀 빠른 시작 (`task` & `rclaw`)

RoboClaw는 **`Taskfile`**(`task`)을 통해 빌드/테스트를 관리하고, Go 기반 **`rclaw` CLI**를 통해 시스템 실행과 런타임 제어를 관리합니다.

```bash
# 1. 의존성 및 가상환경 설정
task setup-deps

# 2. 환경 변수 설정 (.env)
cp .env.example .env
# .env 파일을 열어 API 키 및 Discord 토큰 등을 입력하세요.

# 3. 빌드 (ROS 2 워크스페이스 + rclaw CLI 통합 빌드)
task build

# 4. 빠른 단위 테스트 (TDD)
task unit-test

# 5. 시스템 실행 (로컬 .env 기반 실행)
./rclaw run

# launch 인자 직접 전달 (예: camera_topic 오버라이드)
./rclaw run camera_topic:=/camera/color/image_raw

# 6. 시뮬레이션 실행 (Nav2/AMCL 포함)
./rclaw sim --nav2

# 7. 실행 프로세스 및 점유 포트 강제 정리
./rclaw kill
```

## 🐳 Docker 실행 및 외부 설정 연동

`run_robo_claw_docker.sh`를 통해 도커 빌드 없이 호스트의 설정을 즉시 적용할 수 있습니다.

```bash
# 1. 특정 로봇 설정으로 실행
./scripts/run_robo_claw_docker.sh --robot-config butler

# 2. 호스트의 설정(config) 폴더 마운트 (도커 재빌드 없이 설정 수정 가능)
./scripts/run_robo_claw_docker.sh --config-dir ./src/robo_claw_bringup/config

# 3. Butler 전용 스크립트 폴더 마운트
./scripts/run_robo_claw_docker.sh --butler-scripts /path/to/butler/scripts
```

옵션 전체 목록과 상세 설명은 [docs/DOCKER_GUIDE.md](docs/DOCKER_GUIDE.md)를 참고하세요.

환경 변수(`.env`)를 통해서도 경로를 고정할 수 있습니다:

- `RC_CONFIG_DIR`: RoboClaw 설정 폴더 경로
- `RC_BUTLER_SCRIPTS_DIR`: Butler 로봇 쉘 스크립트 폴더 경로
- `RC_SKILLS_GUIDE_FILE`: 범용 스킬 가이드 파일 경로
- `RC_ROBOT_LIMITS_FILE`: 로봇 구동 한계 스펙 파일 경로
- `RC_TROUBLESHOOTING_GUIDE_FILE`: 장애 조치 가이드 파일 경로

## 🤖 Former 로봇 구동 및 에이전트 연동 가이드

`former` 폴더 내의 스크립트들을 이용한 Former 로봇 환경(SLAM, Navigation2 등) 구동법 및 에이전트 연동에 대한 상세한 가이드는 **[former/FORMER_GUIDE.md](former/FORMER_GUIDE.md)** 파일을 참고하시기 바랍니다.

## 🧰 진단 / 운영 CLI (`rclaw`)

`rclaw`는 Go 기반 통합 CLI로 프로젝트 루트에서 즉시 빌드 및 실행할 수 있습니다. 로컬 `.env` 기반 기동(`run`/`sim`), 원격 설정 서버와 동기화(`fetch`), 시스템 기동(`launch`), 설정 컨트랙트 관리(`contract`), 시나리오 자동화 테스트(`test`), 로컬 설정 확인/관리(`config`)를 수행합니다.

```bash
# 프로젝트 루트에서 빌드:
task build

# 로컬 rclaw 설정을 플랫폼별 기본 폴더에 생성·확인·변경:
./rclaw config init
./rclaw config show
./rclaw config set server.host 10.0.0.1

# 설정 서버 동기화 및 시나리오 테스트:
./rclaw fetch butler office
./rclaw test -r butler -e office

# 설정 컨트랙트 검증:
./rclaw contract check
```

**Docker 기동 플래그** (`launch --docker`): 실행 이미지를 확실히 제어하려면 아래 플래그를 사용합니다.

- `--image-tag <태그>`: 사용할 컨테이너 이미지 태그(기본 `latest`). `:latest` 캐시 함정을 피하려면 불변 태그 권장.
- `--pull`: 실행 직전 레지스트리에서 이미지 pull(옛 로컬 이미지 재사용 방지).
- `--use-grpc`: `robo_claw_grpc`(RosGrpc, 포트 `50051`) 노드 활성화. `./rclaw test`의 텔레메트리(ping 등) 연동 시 필요.

```bash
./rclaw launch former w2_2f_2 --docker --image-tag 20260804-p1p5 --pull --use-grpc
```

> **Stretch3(모바일 매니퓰레이터)** 도 같은 방식으로 기동합니다: `./rclaw launch stretch3 <env> --docker ...`. 조작·RAG 설정은 config 서버의 `stretch3` 프로파일에서 관리하며(주행·관측 전용 Former와 달리 팔/그리퍼 조작 활성), 실기 매니퓰레이션 테스트 절차는 **[docs/STRETCH3_TEST_GUIDE.md](docs/STRETCH3_TEST_GUIDE.md)** 를 참고하세요.

> **시나리오 자동화 테스트 포트 주의**: 자연어 명령(시나리오 `custom` 스텝)은 채널 노드의 **RoboMessenger(포트 `50052`)** 로 처리됩니다. `./rclaw test`로 시나리오를 돌릴 때는 **`--port 50052`** 를 지정하세요(`50051`은 텔레메트리 전용이라 `Method not found`가 납니다).

자세한 명령어와 설정은 [robo_claw_cli/README.md](robo_claw_cli/README.md), 자동화 테스트 절차는 [validation/README.md](validation/README.md)를 참고하세요.

전체 설정 소유권, 실행 방식별 우선순위, AI Config Server profile 작성 절차는
[docs/CONFIGURATION_GUIDE.md](docs/CONFIGURATION_GUIDE.md)를 참고하세요.

공통 contract release와 저장소별 lock/update 절차는 `rcf-config-contract` 저장소의
`docs/RELEASE_WORKFLOW.md`(sibling clone 기준)를 참고하세요.

## 🎮 시뮬레이션 (Gazebo)

ROS 배포판에 따라 최적화된 시뮬레이션 엔진을 자동으로 선택합니다.

- **Jazzy**: Gazebo Harmonic (Turtlebot4 기반)
- **Humble**: Gazebo Classic (Turtlebot3 기반)

```bash
# 기본 시뮬레이션
./rclaw sim

# 내비게이션 및 AMCL(위치추정) 활성화
./rclaw sim --nav2

# SLAM(맵 생성) 및 내비게이션 활성화
./rclaw sim --slam
```

## 💬 인터페이스 연동

### 1. 메신저 (외부망)

`.env` 파일에 토큰을 설정하면 실행 시 자동으로 활성화됩니다. Discord(권장, 실시간 양방향 제어 최적화), Telegram, Slack 연동 상세는 [docs/MESSENGER_GUIDE.md](docs/MESSENGER_GUIDE.md)를 참고하세요.

### 2. gRPC 메신저 (내부망/폐쇄망 + 동료 로봇 협업)

인터넷이 차단된 환경에서 고성능 실시간 제어를 위해 RoboMessenger gRPC 서버(기본 포트 `50052`)를 지원합니다. 이 채널은 다른 RoboClaw 에이전트(동료 로봇)와의 작업 위임/상태 조회에도 쓰입니다. 원격 peer 연결을 사용하려면 peer token을 설정해야 하며, token이 없으면 서버는 loopback에만 바인딩됩니다. 설정과 스킬 목록은 [docs/PEER_COLLABORATION.md](docs/PEER_COLLABORATION.md)를 참고하세요.

### 2-1. 외부 시스템 연동 gRPC (`robo_claw_grpc`)

`robo_claw_grpc` 노드는 `robo_mcp` 등 외부 시스템을 위한 `RosGrpc` 서비스(포트 `50051`)를 제공합니다. 채널 노드의 메신저 gRPC(포트 `50052`)와는 별개입니다.

**주요 RPC:**

- **로봇 상태/제어**: `GetRobotStatus`, `GetCurrentRobotPose`, `NavigateToPose`, `EmergencyStop`, `ControlJoints`, `GetRobotMap`, `GetRobotCameraImage` 등
- **`ExecuteSkill`**: `robo_claw_agent`의 모든 스킬을 범용적으로 호출. 단일 스킬을 정확히 지정해 실행할 때 사용.
- **`ExecuteTask` (New)**: `robo_claw_agent`의 `ExecuteTask` 액션을 gRPC로 노출. 자연어 명령을 LLM 에이전트로 전달해 채널(Telegram/Slack/HTTP/peer) 수신과 동일하게 에이전트가 문맥을 추론·스킬 실행하도록 합니다. `robo_mcp`의 `send_message_to_robot` 도구가 이 RPC를 사용합니다.

> **`ExecuteSkill` vs `ExecuteTask`**: `ExecuteSkill`은 지정한 스킬 하나를 즉시 실행(LLM 추론 우회). `ExecuteTask`는 자연어 명령을 에이전트에 전달해 LLM이 상황을 판단해 적절한 스킬을 선택·실행.

`MAESTRO_IP`, `ROBOT_PORT`, `ROBOT_ID`를 설정하면 `robo_claw_grpc`가 Maestro의 FleetControl server로 outbound stream을 연결합니다. capability는 Robot의 `SKILLS.md`에서 구성하며 command 결과와 idempotency key는 local SQLite journal에 저장합니다. 설정과 disconnect 정책은 [docs/OPERATIONS.md](docs/OPERATIONS.md#maestro-fleetcontrol-outbound-connector)를 참고하세요.

### 3. HTTP API

기본 HTTP 채널은 `127.0.0.1:8080`에 바인딩됩니다. 외부에서 접근하려면 `RC_HTTP_HOST`, `RC_HTTP_ALLOWED_CIDRS_JSON`, 토큰을 명시적으로 설정하세요.

```bash
export RC_HTTP_READONLY_TOKEN="robo-read-token"
export RC_HTTP_CONTROL_TOKEN="robo-write-token"
./rclaw run

curl -H "Authorization: Bearer ${RC_HTTP_READONLY_TOKEN}" \
  http://127.0.0.1:8080/status

curl -X POST http://127.0.0.1:8080/skill \
  -H "Authorization: Bearer ${RC_HTTP_CONTROL_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"skill_name":"get_status","params":{}}'
```

- `GET /health`: 기본 헬스 체크
- `GET /status`: 읽기 전용 토큰 필요
- `POST /task`, `POST /skill`: 제어 토큰 필요
- 위험 스킬은 기본 차단되며 `skill_blocked_json` / `skill_allowed_json`으로 조정 (자세히: [docs/OPERATIONS.md](docs/OPERATIONS.md))
- 자세한 사양: [docs/HTTP_API.md](docs/HTTP_API.md)

## 🧠 System 1 Fast Router (Laya, 선택)

LLM planner를 부르기 전에 빠른 분류 모델(System 1)이 요청을 먼저 판단합니다. 인사나 단순 조회처럼 LLM이 필요 없는 요청은 바로 처리하고, 나머지는 기존 LLM planner(System 2)로 넘깁니다. 모델은 자체 호스팅하는 오픈 웨이트 [Laya](https://huggingface.co/convaiinnovations/laya)(Apache 2.0)를 사용합니다.

- **배타 선택**: 사전 라우팅은 `SYSTEM1_ROUTER=rule`(기존 규칙)과 `laya` 중 **하나만** 동작합니다. 기본값은 `rule`이라 설정하지 않으면 기존 동작과 같습니다.
- **롤백**: `SYSTEM1_ROUTER=rule`로 되돌립니다. Laya 서버 장애(timeout, HTTP 오류)가 나면 해당 요청만 자동으로 규칙 라우터가 처리합니다.
- **배치**: Laya 서버는 엣지 서버(GPU), robo-claw 컨테이너 내장(Thor GPU, 없으면 CPU), CPU 어디서든 실행할 수 있고 환경변수만 다릅니다.

| 배치 | 필수 환경변수 |
|---|---|
| 엣지 서버 (`docker/laya/compose.edge.yaml`) | `SYSTEM1_ROUTER=laya`, `SYSTEM1_ENDPOINT=http://<엣지서버>:8000`, (인증 시) `SYSTEM1_API_KEY` |
| Thor 내장 (`INSTALL_LAYA=true` 이미지 + `--gpu`) | `SYSTEM1_ROUTER=laya`, `SYSTEM1_LOCAL_SERVER=true` |

```bash
# 엣지 서버 (GPU)
cd docker/laya && LAYA_API_KEY=<토큰> docker compose -f compose.edge.yaml up -d --build

# Thor 내장: Laya 포함 이미지 빌드 후 GPU 로 실행
task docker-build-native INSTALL_LAYA=true LAYA_TORCH_INDEX_URL=<CUDA torch 인덱스>
./scripts/run_robo_claw_docker.sh --gpu --hf-cache ~/robo_claw_hf

# 적용 전 평가 (로봇 불필요)
python3 scripts/system1_eval.py --router both --endpoint http://<laya-host>:8000
```

> `SYSTEM1_*`는 에이전트가 환경변수로 직접 읽습니다. `./rclaw launch`는 AI Config Server 웹 설정의 System 1 섹션 값을, `scripts/run_robo_claw_docker.sh`는 `.env`를 전달합니다. `./rclaw run`은 `.env`를 전달하지 않으므로 셸에서 `export`합니다.

전체 환경변수, 실행 방식별 설정 위치, 단계별 적용(그림자 → readonly → navigation), 동작 확인, 문제 해결은 **[docs/SYSTEM1_FAST_ROUTER.md](docs/SYSTEM1_FAST_ROUTER.md)**를 참고하세요. 설계 배경: [docs/SYSTEM1_FAST_ROUTER_DESIGN.md](docs/SYSTEM1_FAST_ROUTER_DESIGN.md)

## 🔍 ONNX 객체 인식 (`robo_claw_vision`)

YOLOv8 ONNX 모델을 이용해 카메라 이미지에서 COCO 80종 물체를 실시간으로 탐지합니다.

### 모델 다운로드

```bash
./download_model.sh        # yolov8n (기본, ~6MB, 가장 빠름)
./download_model.sh s      # yolov8s (~22MB)
./download_model.sh m      # yolov8m (~52MB)
```

### 비전 노드 활성화

`use_vision` 기본값은 `true`입니다. `object_detector_node`는 구독자가 없거나(관련 스킬 미사용 시)
`max_inference_hz`(기본 10Hz) 상한을 넘으면 추론을 건너뛰므로 상시 실행해도 저사양 로봇 부담이
크지 않습니다. 모델 파일만 미리 다운로드해두면 됩니다:

```bash
./rclaw run vision_model_path:=/ros2_ws/models/yolov8n.onnx

# 또는 직접
ros2 launch robo_claw_bringup robo_claw.launch.py \
  vision_model_path:=/ros2_ws/models/yolov8n.onnx

# 완전히 끄려면
./rclaw run use_vision:=false
```

발행 토픽: `/object_detector_node/detections` (`vision_msgs/Detection2DArray`)

에이전트 스킬 연동:

- `get_detections` — 최신 인식 결과 1프레임 수신
- `find_object` — 특정 물체를 탐지 후 라이다/오도메트리로 위치 추정 → 시맨틱 맵 등록

## 🛠️ 주요 스킬

에이전트가 사용할 수 있는 스킬(네비게이션, 매니퓰레이션, 인지, 자율 행동, RAG, 자가 학습, 동료 로봇 협업, HRI, 안전, 시스템 등)은 [docs/SKILLS.md](docs/SKILLS.md)의 전체 레퍼런스 표를 참고하세요.

## 🔧 개발 (Lint / Test)

### 린트

```bash
task lint           # ruff + 스킬 카탈로그 + 프롬프트 톤 + go vet 일괄 검사
task lint-skills    # 스킬 카탈로그 검증 (docs/SKILLS.md 대조)
uvx ruff check src/robo_claw_agent/robo_claw_agent/   # Python만 빠르게
```

스킬을 추가/변경하면 `task lint-skills`가 `docs/SKILLS.md`와 실제 등록 스킬의 일관성을 검증합니다.

### 테스트

```bash
# 빠른 TDD 피드백 루프: ROS2/하드웨어 테스트 제외 + coverage
task unit-test

# 특정 테스트 또는 pytest 옵션 전달
task unit-test -- src/robo_claw_agent/tests/test_skill_manager.py -q

# 전체 Python + ROS2 패키지 테스트
task test
```

`pytest`의 기본 테스트 대상은 `agent`, `channel`, `discovery`, `grpc` 패키지의
테스트를 모두 포함합니다. ROS2가 설치되지 않은 환경에서는 반드시 `task unit-test`
또는 `task test -- --pytest-args -m "not ros2"`를 사용하세요. 워크스페이스가 빌드되지
않은 경우에는 먼저 `task build`를 실행해야 생성된 ROS 메시지/서비스 모듈을 사용할 수
있습니다.

테스트 계층은 `unit`, `component`, `integration`, `ros2`, `hardware`, `slow` 마커로
구분할 수 있습니다. 예를 들어 순수 단위 테스트만 실행하려면 다음과 같이 합니다.

```bash
pytest -m unit
```

### 자동화 검증 (시나리오 / QA)

- **로봇 없이(코드 회귀)**: `./validation/rag_regression/run_rag_tests.sh` — RAG 위치 지식 개선이 코드에 반영됐는지 순수 파이썬으로 검증.
- **실로봇 시나리오**: `robo_claw_cli`로 First-Use 시나리오를 자동 판정. 품질팀 공유용 절차는 **[validation/README.md](validation/README.md)** 참고.

## 📚 상세 가이드

개발 에이전트와 신규 기여자를 위한 TDD, ROS2 의존성, 안전성 및 커밋 전 체크리스트는 [AGENTS.md](AGENTS.md)를 참고하세요.

README는 핵심 개요만 담고 있습니다. 기능별 상세 설정과 레퍼런스는 아래 문서를 참고하세요.

| 문서                                                                         | 내용                                                                      |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| [docs/OPERATIONS.md](docs/OPERATIONS.md)                                     | 운영 파라미터, 태스크 큐(순차 실행), MCP 서버 연동                        |
| [docs/SKILLS.md](docs/SKILLS.md)                                             | 전체 스킬 레퍼런스                                                        |
| [docs/CONFIGURATION_GUIDE.md](docs/CONFIGURATION_GUIDE.md)                   | 설정 소유권, 실행 방식별 우선순위, AI Config Server profile               |
| [docs/RAG_AND_LEARNING.md](docs/RAG_AND_LEARNING.md)                         | RAG/Qdrant 설정, 스킬 자가 학습, 탐험 관찰 로깅                           |
| [docs/ROBOT_CONFIG.md](docs/ROBOT_CONFIG.md)                                 | `ROBOT.md` 개성, `ROBOT_LIMITS.json`, 장애 조치 `TROUBLESHOOTING.md`      |
| [docs/behavior-tree/README.md](docs/behavior-tree/README.md)                 | 자율 행동(BT) 요약과 트리 전체 구조·노드 카탈로그                         |
| [docs/behavior-tree/README.md](docs/behavior-tree/README.md)                 | BT 엔진 코어, 자율행동/반응형 트리 전체 구조와 노드 카탈로그              |
| [docs/PEER_COLLABORATION.md](docs/PEER_COLLABORATION.md)                     | 멀티 에이전트(동료 로봇) gRPC 협업/작업 위임                              |
| [docs/MANIPULATION.md](docs/MANIPULATION.md)                                 | MoveIt 기반 매니퓰레이션 설정, Stretch3 백엔드                            |
| [docs/HTTP_API.md](docs/HTTP_API.md)                                         | HTTP API 전체 레퍼런스                                                    |
| [docs/MESSENGER_GUIDE.md](docs/MESSENGER_GUIDE.md)                           | Discord/Slack/Telegram 연동 가이드                                        |
| [docs/DASHBOARD.md](docs/DASHBOARD.md)                                       | 웹 대시보드(`robo_claw_dashboard`) 상태 조회·메시지 전송                  |
| [docs/LANGSMITH_INTEGRATION.md](docs/LANGSMITH_INTEGRATION.md)               | LangSmith LLM 트레이싱/모니터링 활성화                                    |
| [docs/SYSTEM1_FAST_ROUTER.md](docs/SYSTEM1_FAST_ROUTER.md)                   | System 1 Fast Router(Laya) 설정·환경변수·운영 가이드                      |
| [docs/SYSTEM1_FAST_ROUTER_DESIGN.md](docs/SYSTEM1_FAST_ROUTER_DESIGN.md)     | System 1 Fast Router(Laya) 설계·단계별 도입 계획                          |
| [docs/DOCKER_GUIDE.md](docs/DOCKER_GUIDE.md)                                 | Docker 실행(`run_robo_claw_docker.sh`) 및 이미지 빌드(`task docker-*`)    |
| [docs/BUILD_TROUBLESHOOTING.md](docs/BUILD_TROUBLESHOOTING.md)               | 빌드/설치/실행 오류 해결                                                  |
| [docs/STRETCH3_TEST_GUIDE.md](docs/STRETCH3_TEST_GUIDE.md)                   | Stretch3 실기 매니퓰레이션 테스트 절차                                    |
| [docs/DUAL_CAMERA_GRASP.md](docs/DUAL_CAMERA_GRASP.md)                       | 헤드 카메라 병용 파지(서보 + ArUco 차분 보정) 설계·튜닝                   |
| [docs/HEAD_CAMERA_UPRIGHT_ROTATION.md](docs/HEAD_CAMERA_UPRIGHT_ROTATION.md) | 헤드 카메라 세로 마운트 회전 보정                                         |
| [docs/CLOiD_GUIDE.md](docs/CLOiD_GUIDE.md)                                   | CLOi 플랫폼 서비스 구동(submodule) 가이드                                 |
| [docs/PROMPT_TOKEN_COUNT.md](docs/PROMPT_TOKEN_COUNT.md)                     | 프롬프트 토큰 수 측정(`count_prompt_tokens.py`)·`num_ctx` 산정            |
| [docs/AI_CONFIG_VIEWER.md](docs/AI_CONFIG_VIEWER.md)                         | AI Config 활성 설정의 LLM·임베딩 하이퍼파라미터 조회(`show_ai_config.py`) |
| [docs/RESET_ROBOT_STATE.md](docs/RESET_ROBOT_STATE.md)                       | 호스트 SSH 로봇 상태 초기화(`reset_robot_state.sh`)                       |
| [docs/RESET_ROBOT_STATE_SERVER_DB.md](docs/RESET_ROBOT_STATE_SERVER_DB.md)   | 로봇 상태 + 벡터 DB 완전 초기화(`reset_robot_state_server_db.sh`)         |
| [CHANGE_LOG.md](CHANGE_LOG.md)                                               | 전체 변경 이력 — RAG 위치 지식 개선(P1~P5) 상세는 부록                    |
| [former/FORMER_GUIDE.md](former/FORMER_GUIDE.md)                             | Former 로봇 구동(SLAM/Nav2) 및 에이전트 연동                              |
| [robo_claw_cli/README.md](robo_claw_cli/README.md)                           | 설정 동기화 및 시나리오 기반 QA 자동화 CLI                                |
| [validation/README.md](validation/README.md)                                 | **자동화 테스트 가이드(QA용)** — 코드 회귀 + 실로봇 시나리오              |
