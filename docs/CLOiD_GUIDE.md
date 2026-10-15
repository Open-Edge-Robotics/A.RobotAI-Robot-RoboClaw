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
| `cloid_config.yaml` | core/agent 센서·토픽, agent 카메라, 조작 활성화 및 정리 표시 모션 설정 |
| `ROBOT_LIMITS.cloid.json` | 주행 한계와 URDF(hmc_v2_hand) 기준 팔·waist·neck 조인트 하드 한계 |
| `SKILLS.cloid.1.md` | CLOiD 1호 전용 스킬 가이드(주행과 CLOi 등록 상체 모션 사용 가능) |
| `SKILLS.cloid.2.md` | CLOiD 2호 전용 스킬 가이드(팔·상체 구동 오류, 주행·인지·조회·정지 전용) |
| `ROBOT.cloid.1.md` | CLOiD 1호 전용 소울 프로필(팔·상체 모션을 쓰는 정상 개체의 개성) |
| `ROBOT.cloid.2.md` | CLOiD 2호 전용 소울 프로필(팔·상체 오류 상태를 담담히 알리는 개성) |

### 개체별 스킬·소울 가이드 선택

CLOiD 는 같은 `cloid` 프로필을 쓰면서 개체 상태가 달라 스킬 가이드와 소울 프로필을
1호/2호로 분리했습니다. 하드웨어·토픽 설정(`cloid_config.yaml`, `ROBOT_LIMITS.cloid.json`)은
두 개체가 공유하고, 런타임에 주입되는 문서만 개체별로 지정합니다.

- `skills_guide_file`(또는 `RC_SKILLS_GUIDE_FILE`)을 지정하지 않고 `robot_config:=cloid`
  로 기동하면 launch 는 `SKILLS.cloid.md` 를 먼저 찾고, 없으면 `SKILLS.cloid.1.md` 를
  1호 기본값으로 사용하며 개체 번호 미지정 경고를 출력합니다.
- 같은 방식으로 `robot_soul_file`(또는 `RC_ROBOT_SOUL_FILE`)을 지정하지 않으면
  `ROBOT.cloid.1.md` 가 1호 소울 기본값이 됩니다.
- 2호는 두 문서 경로를 모두 명시합니다.

```dotenv
RC_ROBOT_CONFIG=cloid
RC_AGENT_ID=cloi02
RC_SKILLS_GUIDE_FILE=/ros2_ws/src/robo_claw_bringup/config/SKILLS.cloid.2.md
RC_ROBOT_SOUL_FILE=/ros2_ws/src/robo_claw_bringup/config/ROBOT.cloid.2.md
```

- 설정서버로 기동하는 배포는 서버가 내려주는 `SKILLS.md`·`ROBOT.md` 내용이 우선하므로,
  2호 디바이스 설정에는 `SKILLS.cloid.2.md`·`ROBOT.cloid.2.md` 내용을 등록합니다.
- 2호 배포에서는 상체 표시 모션이 발행되지 않도록 `cloid_cleanup_indicator_enabled` 를
  `false` 로 두고 `cloid_cleanup_indicator_motion_ids_json` allowlist 를 비웁니다.

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
  일반 조작 계열 스킬은 실행 단에서 차단됩니다.

### VLA 구현 전 동료 정리 시연

CLOiD의 `tidy_home`은 명확한 폐기물뿐 아니라 수건, 쿠션/방석, 명확한 빨래/세탁물도 관찰 후보로 인식합니다. CLOiD 2호는 이를 직접 옮길 수 없으며, 생활용품은 유형별 안전 보관 장소가 확인되는 실제 `manipulation` 동료에게만 요청할 수 있습니다. 명확한 폐기물은 `cleanup_demo` 능력을 제공하는 CLOiD 1호에 단일 시연 요청을 보낼 수 있습니다. 이 고정 모션 시연은 물체를 집거나 옮기는 기능이 아닙니다. 2호는 요청 전 Nav2 안전 검사를 거친 짧은 상대 이동으로 접근 공간을 확보합니다. 이동이 거부되면 요청을 보내지 않습니다.

시연 요청은 방 이름, 대상 이름, 관찰 요약을 전달합니다. 대상의 3D 좌표나 파지 궤적은 전달하지 않습니다. 1호는 자신의 기억 장소에서 방을 해석해 이동한 뒤 카메라로 대상을 다시 확인해야 합니다. 대상이 다시 보이지 않거나 모호·위험하면 모션을 실행하지 않습니다.

1호의 시연 경로 플래그는 기본 `true`이며, `RC_AGENT_ID`가 `cloid01` 또는 `cloi01`일 때만 코드 기본 allowlist `[22, 60]`을 사용합니다. ID 22는 좌우 탐색(`scan`), ID 60은 등록된 `wipe_2` 모션입니다. **1호 전용** `RC_CLOID_CLEANUP_DEMO_MOTION_IDS_JSON` 값으로 목록을 덮어쓸 수 있고, `[]`를 지정하면 시연 스킬이 등록되지 않습니다. ID 59도 명시적 allowlist에 넣을 수 있습니다. 단, 시연 선택기는 실행 시점 모션 카탈로그의 `pre_id`가 비어 있는 항목만 선택하며 선행 모션을 자동 실행하지 않습니다. 따라서 카탈로그에 ID 60의 `pre_id=59`가 표시되면 ID 60은 계속 차단됩니다. 2호에는 기본값이나 시연 스킬을 적용하지 않으며, 공통 CLOi 설정의 기존 상태 표시 모션도 기본 비활성화합니다.

CLOi MotionCmd에는 완료 응답이 없으므로 이 경로의 결과는 모션 **실행 요청**까지만 확인합니다. 메시지는 임시 시연과 실제 정리 여부를 구분하며, 물체를 집거나 옮겼다거나 정리가 완료됐다고 보고하지 않습니다. `manipulation_enabled=false`는 계속 유지합니다.

직접 모션 스킬 `execute_cloid_motion`은 사용자가 `confirm=true`로 실행을 명시 승인하면 카탈로그의 단일 `pre_id` 선행 모션을 먼저 요청하고 10초 뒤 대상 모션을 요청합니다. 선행 ID 누락·순환·중첩은 차단합니다. 완료 피드백은 없으므로 10초는 완료 보장이 아닌 간격 설정입니다. 이 순차 실행은 정리 시연에 적용되지 않습니다. 정리 시연은 기존처럼 `pre_id` 모션을 선택하지 않으며, `pre_id` 선행 모션도 실행하지 않습니다.

## 포장용 최소 부피 자세

CLOiD 는 출하 포장처럼 로봇 부피를 최소로 줄여야 할 때 boxing 자세를 사용합니다. 이
자세는 허리를 URDF 한계까지 숙이고(`waist_joint_1..3`) 양팔을 앞으로 접어
(`arm_joint_4=90°`) 전고를 낮춥니다. RoboClaw 는 `set_cloid_packing_pose` 스킬로 이
자세를 요청합니다.

- **실행 경로**: 로봇 tofu 의 `script_runner` action(`/script_runner/run_script`,
  `tofu_msgs/action/RunScript`)에 `ros2 run tofupy cloid.move_boxing` 실행을
  요청합니다. `tofu_msgs` 는 RoboClaw 가 wire-compatible subset 으로 `src/tofu_msgs` 에
  vendoring 하며, `task_manager_msgs` 와 같은 방식입니다.
- **전제**: `tofu_control` daemon 과 `script_runner` 가 실행 중이어야 합니다.
  `script_runner` 서버가 없으면 스킬은 `script_runner_unavailable` 로 fail closed 합니다.
  `tofu_msgs/action/RunScript` 인터페이스가 없으면 `motion_interface_unavailable` 입니다.
- **한계**: `cloid.move_boxing` 은 실행 완료 피드백을 주지 않습니다. 이 스킬은 스크립트
  정상 종료(`exit_code=0`)만 확인하고 실제 자세 도달은 확인하지 않습니다
  (`completion_confirmed=false`).
- **사용**: 수동 실행은 정지 상태에서 사용자의 명시적 요청(`confirm=true`)으로만 실행합니다.
  주행 전 자세 준비는 아래 "주행 전 boxing 자세 prelude" 가 internal 스킬로 담당하므로,
  사용자 명령으로 이동할 때 이 스킬을 계획에 넣을 필요가 없습니다.
- **등록 범위**: `robot_config:=cloid` 프로필이면 개체(1호/2호) 구분 없이 등록됩니다.
- **대안 스크립트 주의**: `hmc.boxing` 은 손가락을 쥐고 10초 뒤 양팔 gain 을 0으로 풀어
  힘을 빼므로, 자세 유지가 필요한 포장 용도로는 사용하지 않습니다.
- **한계값 확인**: `cloid.move_boxing` 의 허리 `waist_joint_3`(-67°)는 RoboClaw 한계 파일의
  하한(-66.0°)보다 약 1° 크므로, 로봇의 `use_limits` 가 켜진 배포에서는 자세가 제한될 수
  있습니다. 실측 CLOiD 1호는 `TOFU_USE_LIMITS=false` 로 동작합니다.

## 주행 전 boxing 자세 prelude

CLOiD 는 모션이나 조작을 마친 뒤 상체 자세가 바뀌어 팔이 몸 밖으로 나올 수 있습니다. 그
상태로 베이스가 움직이면 벽·문틀·가구에 팔이 걸릴 수 있으므로, Stretch3 의
`stow_for_navigation` 과 같은 위치에서 **베이스 이동 직전에 boxing 자세를 먼저
요청**합니다.

- **스킬**: internal 스킬 `cloid_navigation_pose`(`is_internal=true`, LLM/사용자
  비노출). tofu 실행 계약은 `set_cloid_packing_pose` 와 동일하며 공용 헬퍼를 씁니다.
- **삽입 지점**: `agent_node/nav_safety.py` 의 `ensure_navigation_safety()` 가
  베이스가 움직이는 스킬(`navigate_to`, `move_relative`, `rotate`, `face_direction`,
  `approach_object`, `follow_waypoints`, `patrol`, `explore`, `reactive_navigate`,
  `condition_reactive` 등) 직전마다 삽입합니다. 삽입된 항목은
  `_policy_prelude` 표시로 자체완결형/백그라운드 체이닝 규칙에서 제외됩니다.
- **Raw Nav2 경로**: 자율행동 frontier 탐험, 순찰 방문, 제자리 관찰처럼 스킬 체인을
  거치지 않고 Nav2 goal 을 직접 보내는 경로는 `prepare_navigation()` 이 같은 절차를
  수행합니다.
- **fail closed**: 자세 요청이 실패하거나 준비 상태를 기록할 수 없으면 이동을
  거부합니다. 조용히 주행하지 않습니다.
- **반복 방지와 무효화**: 같은 이동 구간에서 매 tick 재요청하지 않도록 준비 상태를
  노드에 기록하고, 팔/그리퍼를 움직이는 스킬이 성공하면(SkillManager 성공 후처리 훅)
  그 상태를 지웁니다. 다음 이동 전에 자동으로 다시 요청합니다.
- **직접 호출 경로**: HTTP/gRPC/메신저로 베이스 스킬을 곧바로 호출하면 체인 삽입이
  없으므로, `NavigationSafetyGate` 가 준비 상태를 확인해 준비 전이면 이동을
  거부합니다.
- **confirm 없음**: 런타임 안전 절차이므로 confirm 을 요구하지 않습니다. 공개 스킬
  `set_cloid_packing_pose` 의 confirm 게이트는 그대로 유지됩니다.
- **스위치**: contract `agent.cloid_navigation_pose_enabled`(env
  `RC_CLOID_NAVIGATION_POSE_ENABLED`, launch `cloid_navigation_pose_enabled`, 기본
  `false`)로 켭니다. 스킬 모듈이 등록되지 않으면 게이트도 함께 비활성화됩니다.
- **개체 적용**: 1호/2호 구분 없이 등록되며, 상체 구동 오류가 있는 개체는 운영 설정에서
  스위치를 `false` 로 두어 비활성화합니다.
- **미적용 경로**: `scan_room`(제자리 360° 스캔)과 `self_localize` 는 Nav2 spin/navigate 를
  직접 호출하고 메타데이터에 베이스 모션 부작용이 없어 현재 prelude 삽입 대상이 아닙니다.
  이 두 스킬을 주행 전 자세와 함께 쓰려면 운영자가 먼저 자세 요청을 수행해야 합니다.
- **한계**: `cloid.move_boxing` 은 완료 피드백을 주지 않으므로 실제 자세 도달은 확인하지
  않습니다(`completion_confirmed=false`). 자세 준비 상태는 "요청 성공 후 상체를 움직이지
  않음"까지만 보장합니다. boxing 자세 자체의 주행 안전성(무게 중심·전고·라이다 가림)은
  실기에서 검증이 필요합니다.
- **복원 없음**: 도착 후 자세를 되돌리지 않습니다. 복원이 필요하면 운영자가
  `set_cloid_packing_pose` 이외의 복원 수단을 준비해야 합니다.

## 상황별 표현 모션 자동 재생

사용자 상호작용을 친근하게 만들기 위해, CLOiD 개체가 태스크를 마친 뒤 상황에 맞는
등록 모션을 한 번 재생할 수 있습니다. 기본값은 꺼짐이며 `RC_CLOID_EXPRESSION_ENABLED`
(launch `cloid_expression_enabled`)로 켭니다.

- **동작**: 태스크 완료 후 `cloid_express` 내부 스킬이 상황(인사·대화·탐색·실패)에
  맞는 모션을 카탈로그에서 골라 `execute_cloid_motion` 경로로 한 번 요청합니다.
  반복을 피하려고 직전 모션과 다른 후보를 우선 선택합니다.
- **상황 판정**: 인사 표현이 있으면 인사, 탐색 표현이 있으면 탐색, 태스크 실패면 실패,
  그 외에는 대화 모션을 고릅니다.
- **태스크당 1회**: 태스크 완료 시 한 번만 백그라운드로 요청하며 응답을 지연시키지
  않습니다.
- **주행 태스크 제외**: `navigate_to`, `move_relative` 같은 베이스 주행 스킬이 실행된
  태스크이거나 명령에 이동 표현이 있으면 재생하지 않습니다. 주행 중·직후의 상체 모션은
  위험하기 때문입니다.
- **allowlist**: 선택 가능한 모션 ID는 코드 상수(`cloid_expression_policy`)로 고정되어
  있고 모두 `pre_id` 없는 모션입니다. 카탈로그에 없거나 `pre_id` 가 있으면 재생하지
  않습니다. `rcf-config-contract` `agent.cloid_expression_enabled`(env
  `RC_CLOID_EXPRESSION_ENABLED`, launch `cloid_expression_enabled`)로 계약에 등록되어
  있습니다.
- **미확인 사항**: `MotionCmd` 는 완료 응답을 주지 않으므로 결과는 모션 실행 요청까지이며
  실제 자세 도달은 확인하지 않습니다. 실기에서 안전성 검증은 아직 하지 않았습니다.
- **개체별 설정**: 모든 cloid 개체에 등록되므로, 상체 구동 오류가 있는 2호는 개체
  설정에서 `RC_CLOID_EXPRESSION_ENABLED=false` 로 두어 비활성화합니다.

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
