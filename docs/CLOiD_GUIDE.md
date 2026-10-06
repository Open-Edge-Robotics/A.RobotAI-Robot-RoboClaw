# RoboClaw CLOi 연동 가이드

이 문서는 업스트림 `robo-claw` 서브모듈이 CLOi `robo-claw` 서비스로 빌드되고 기동되는 방식을 설명합니다.

## 디렉터리 구조

업스트림 저장소는 중첩된 ROS 워크스페이스를 포함합니다. 실제 ROS 패키지는 CLOi 서비스의 `src/` 바로 아래에 있지 않습니다.

```text
services/ops/robo-claw/
├── docker/
├── docs/
└── src/
    └── robo-claw/                 # git submodule
        └── src/                   # 실제 ROS 워크스페이스
            ├── robo_claw_agent/
            ├── robo_claw_bringup/
            ├── robo_claw_channel/
            ├── robo_claw_core/
            ├── robo_claw_discovery/
            ├── robo_claw_grpc/
            ├── robo_claw_msgs/
            └── robo_claw_vision/
```

래퍼 Dockerfile과 Dev 오버레이는 이 추가 `src/` 경로를 감안합니다. CLOi의 일반 서비스처럼 보이도록 서브모듈 구조를 평탄화하거나 수정하지 마세요.

## Compose 파일

저장소의 Compose 진입점은 다음과 같습니다.

```text
docker/docker-compose.yml
```

RoboClaw 서비스 파일은 다음과 같습니다.

```text
docker/compose/robo-claw.yml
docker/compose/robo-claw.override.dev.yml
docker/compose/_common.yml
```

이 파일들을 직접 호출하지 말고 `cloi` 명령을 사용하세요. 공통 ROS 서비스 설정은 `network_mode: host`를 사용하므로 서비스 포트가 곧 호스트 포트이며, Docker `ports:` 섹션으로 발행되지 않습니다.

## 사전 준비

빌드 전에 서브모듈을 초기화합니다.

```bash
git submodule update --init --recursive
```

래퍼는 `robo_claw_bringup`이 vision·channel·grpc 패키지를 launch할 수 있어야 하므로 모든 ROS 패키지를 빌드합니다. 이미지 빌드는 MoveIt, ROS 의존성, Python 의존성, 아키텍처별 ONNX Runtime을 설치합니다. Python protobuf 모듈은 이미지 빌드와 Dev 빌드 양쪽에서 생성됩니다.

## 환경 변수

업스트림 `.env` 파일은 로컬 런타임 환경 변수를 정의하지만, CLOi는 `ros2 launch`를 직접 기동하며 이를 source하지 않습니다. 래퍼 Compose 파일이 업스트림 `.env`를 `env_file`로 컨테이너에 전달합니다.

업스트림 템플릿에서 로컬 파일을 생성합니다.

```bash
cp services/ops/robo-claw/src/robo-claw/.env.example \
   services/ops/robo-claw/src/robo-claw/.env
```

이 파일은 이미지 빌드에는 선택적이지만, Azure 또는 외부 gRPC 구동에는 필요합니다. 커밋하거나 이미지에 복사하지 마세요.

주요 설정:

```dotenv
RC_LLM_PROVIDER=azure
RC_LLM_MODEL=<azure-deployment-name>
AZURE_OPENAI_ENDPOINT=https://<resource>.openai.azure.com
AZURE_OPENAI_API_KEY=<secret>

ENABLE_GRPC=true
# RoboMessenger 기본 포트는 50052. CLOi 래퍼에서 외부 포트를 바꿀 때 override합니다.
ROBO_CLAW_GRPC_PORT=50052

# 아웃바운드 peer 클라이언트 설정. 외부 채팅 서버와는 무관합니다.
ENABLE_GRPC_CLIENT=false
GRPC_TARGET_HOST=127.0.0.1
GRPC_TARGET_PORT=50151
```

`ROBO_CLAW_ENV_FILE`로 다른 호스트 경로의 env 파일을 지정할 수 있습니다.

```bash
export ROBO_CLAW_ENV_FILE=/absolute/path/to/robo-claw.env
```

`.env`를 수정한 뒤에는 컨테이너를 재생성해야 합니다. 재시작만으로는 컨테이너 환경이 안정적으로 교체되지 않습니다.

```bash
cloi down robo-claw -f
cloi up robo-claw dev
```

## RoboClaw 로봇 프로필 (robot_config)

RoboClaw 는 `robot_config` 로 로봇별 토픽·센서·안전 설정을 선택합니다. CLOiD
실기에서는 `cloid` 프로필을 지정합니다.

```dotenv
RC_ROBOT_CONFIG=cloid
RC_AGENT_ID=cloi02
```

프로필 파일은 업스트림 저장소의 `src/robo_claw_bringup/config/`에 있으며, launch 가
같은 이름의 파일을 자동으로 찾아 주입합니다.

| 파일 | 역할 |
| :--- | :--- |
| `cloid_config.yaml` | core/agent 센서·토픽, agent 카메라, 조작 활성화 설정 |
| `ROBOT_LIMITS.cloid.json` | 주행 한계와 URDF(hmc_v2_hand) 기준 팔·waist·neck 조인트 하드 한계 |
| `SKILLS.cloid.md` | CLOiD 전용 스킬 가이드(런타임 주입) |

CLOiD 실기 제약은 다음과 같습니다(휴머노이드 스킬 개발 시 전제).

- 2D LaserScan(`/scan`)을 발행하지 않습니다. 장애물 인지는 3D 라이다
  (`/lidar_points_bounded`)와 Nav2 costmap이 담당합니다.
- 로봇의 `diff_drive_controller`가 `/cmd_vel`을 `geometry_msgs/TwistStamped`로
  구독합니다. RoboClaw 의 직접 cmd_vel 제어(`geometry_msgs/Twist`)는 이 로봇에
  도달하지 않으므로 베이스 이동·회전은 Nav2 action(`navigate_to_pose`, `spin`)을
  사용합니다.
- AMCL이 없어 `/amcl_pose` 와 `/reinitialize_global_localization` 이 없습니다
  (`self_localize` 사용 불가).
- RGB 카메라는 `sensor_msgs/CompressedImage` 만 발행합니다.
- 팔/hand 제어 백엔드가 아직 없어 `manipulation_enabled=false` 로 기동하며,
  조작 계열 스킬은 실행 단에서 차단됩니다.

## x64 DevBox SIL

x64 SIL 명령은 체크아웃의 경로 스코프 DevBox 안에서 실행합니다.

```bash
DEVBOX_NAME="$(./scripts/cloi.sh devbox --print-name)"
docker exec -u cloi -t "$DEVBOX_NAME" bash -lc \
  'cd /home/cloi/ws/cloi_entropos && cloi build robo-claw dev'
```

Dev 서비스를 시작합니다.

```bash
docker exec -u cloi -t "$DEVBOX_NAME" bash -lc \
  'cd /home/cloi/ws/cloi_entropos && cloi up robo-claw dev'
```

Dev 모드는 실제 중첩 ROS 워크스페이스를 bind-mount 합니다.

```text
services/ops/robo-claw/src/robo-claw/src -> /ws/src
services/ops/robo-claw/docker/build.sh -> /ws/build.sh
services/ops/robo-claw/docker/launch.sh -> /ws/launch.sh
```

Dev 시작마다 오버레이가 `/ws/build`, `/ws/install`, `/ws/log`를 제거하고, protobuf 모듈을 재생성하며, symlink install을 수행합니다. 이는 이미지 빌드의 오래된 CMake 소스 경로를 방지합니다.

## ARM64 로봇

ARM64 로봇에는 로봇 호스트에서 네이티브 빌드하는 방식을 권장합니다.

```bash
uname -m                         # 기대값: aarch64
cloi build robo-claw release
cloi up robo-claw release
```

`cloi build`와 `cloi up`은 x64 DevBox 내부가 아니라 **물리 로봇 호스트에서 직접** 실행해야 합니다.

소스가 PC에 있고 로봇에 SSH로 접근 가능하면, DevBox에서 명시적 ARM64 크로스 빌드와 이미지 전송도 지원합니다.

```bash
cloi build robo-claw release \
  --platform linux/arm64 \
  --tag robo-claw-arm64-test \
  --to-device cloi@<ROBOT_IP>
```

이 방식은 이미지만 전송합니다. 로봇에는 CLOi compose/config 체크아웃과 별도 `.env` 파일이 여전히 필요합니다. Dev 이미지를 전송하는 경우 로봇에도 소스 트리가 필요합니다(`robo-claw.override.dev.yml`이 소스를 bind-mount하고 컨테이너 시작 시 재빌드하기 때문). 실제 로봇 운영은 release 배포를 권장합니다.

## 외부 gRPC 서버

`ENABLE_GRPC=true`이면 CLOi 래퍼를 통해 channel 노드가 실행됩니다. RoboMessenger 서버는 기본 포트 `50052`를 사용합니다. CLOi 배포에서는 포트를 변경할 수 있습니다(예: `50152`).

```text
Channel node ready [HTTP:8080, gRPC:50052]
```

peer token이 설정되면 gRPC 서버가 원격 인터페이스에 바인딩됩니다. 같은 LAN의 클라이언트는 다음 주소를 사용합니다.

```text
<ROBOT_IP>:50052
```

외부 서버 포트와 아웃바운드 peer 포트는 별개입니다.

```text
ROBO_CLAW_GRPC_PORT -> 인바운드 외부 채팅 서버
GRPC_TARGET_PORT    -> 아웃바운드 peer 클라이언트
```

리스너 확인:

```bash
ss -ltnp | grep -E ':50052|:50152'
```

외부 클라이언트는 RoboMessenger protobuf 계약과 `ChatStream` RPC를 사용해야 합니다. raw TCP 연결은 포트 개방만 확인할 뿐 gRPC 프로토콜이나 인증을 검증하지 않습니다.

`GRPC_PEER_TOKEN` 또는 peer token 매핑이 설정되어 있으면 외부 클라이언트가 일치하는 인증 메타데이터를 보내야 합니다. 신뢰할 수 없는 네트워크에서 gRPC 인증을 비활성화해 두지 마세요.

## 로그와 상태

서비스 로그 팔로우:

```bash
cloi logs robo-claw
```

제한된 로그 샘플 캡처:

```bash
timeout 20s cloi logs robo-claw || true
```

런타임 상태 확인:

```bash
cloi status
```

실행 중 컨테이너 진입:

```bash
cloi shell robo-claw
```

컨테이너 내 패키지 설치 확인:

```bash
ros2 pkg list | grep robo_claw
```

## 기대되는 시작 로그

래퍼와 LLM 설정이 정상 동작함을 나타내는 메시지입니다.

```text
Summary: 8 packages finished
Channel node ready [HTTP:8080, gRPC:50052]
LLM healthcheck passed
LLM init complete (RAG disabled): azure
AgentNode ready
```

CAN 인터페이스가 없는 로컬 PC에서는 다음 경고가 정상입니다.

```text
CAN interface can0 not found
```

## 트러블슈팅

### 알 수 없는 빌드 대상 (Unknown build target)

서비스 ID는 `robot-claw`가 아니라 `robo-claw`를 사용합니다.

```bash
cloi build robo-claw dev
```

### 패키지 `robo_claw`를 찾을 수 없음

단일 ROS 패키지 `robo_claw`는 존재하지 않습니다. 실제 통합 대상은 `robo_claw_bringup`이며, 래퍼는 다음을 복사해야 합니다.

```text
services/ops/robo-claw/src/robo-claw/src/ -> /ws/src/
```

### bind mount가 "directory onto file" 오류를 반환

DevBox 경로가 호스트 Docker 데몬에 전달된 경우입니다. 현재 경로 스코프의 DevBox에서 `cloi up`을 실행하고, `scripts/cloi.sh`가 `CLOI_ROOT`에 호스트 체크아웃 경로를 사용하는지 확인하세요.

### `bash: - : invalid option`

Dev 명령의 YAML 배열/블록 스칼라가 잘못된 경우입니다. 렌더링된 명령이 개별 인자를 담고 있어야 합니다.

```text
Args=["bash", "-c", "source ..."]
```

### `Address already in use`

`ThreadingHTTPServer((host, port), ...)` 트레이스백은 RoboMessenger gRPC 포트 `50052`가 아니라 HTTP 포트 `8080`을 가리킵니다. 호스트 네트워크 리스너를 확인하세요.

```bash
ss -ltnp | grep -E ':8080|:50052|:50152'
```

### LLM이 restricted 모드로 초기화됨

먼저 `.env` 변수가 컨테이너 내부에 존재하는지 확인합니다.

```bash
docker exec cloi-robo-claw bash -lc \
  'test -n "$AZURE_OPENAI_ENDPOINT" && echo endpoint=set || echo endpoint=unset'
```

엔드포인트에 접근 가능한데 초기화가 예기치 않은 `timeout_sec` 생성자 인자를 보고하면, 서브모듈에 `AzureOpenAIBridge` 생성자에 `timeout_sec`을 전달하지 않는 LLM 초기화 수정이 포함되어 있는지 확인하세요.

### ONNX 모델 파일을 찾을 수 없음

vision 패키지는 모델 파일 없이도 빌드에 성공할 수 있습니다. launch 설정이 기대하는 경로에 모델을 준비하세요.

```text
/ros2_ws/models/yolov8s.onnx
```

이것은 Azure LLM 연결 문제와 별개입니다.
