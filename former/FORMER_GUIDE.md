# Former 로봇 구동 및 에이전트 연동 가이드

`former` 폴더에는 Former 로봇의 ROS 2 환경(SLAM, Navigation2 등)을 구동하기 위한 Docker 런처와, RoboClaw 에이전트를 Former 로봇 설정에 맞춰 실행하기 위한 단축 스크립트들이 포함되어 있습니다.

로봇 하드웨어 환경 및 플랫폼 사양(PC vs Jetson, 카메라 토픽 등)에 따라 **`former0045/`** 와 **`former0047/`** 전용 폴더로 분리되어 제공됩니다.

## 1. 디렉토리 구조

```text
former/
├── config/                     # 공용 SLAM 파라미터 (mapper_params.yaml 등)
├── params/                     # 공용 Nav2 파라미터 (nav2_params.yaml 등)
├── maps/                       # 사전 작성된 맵 (w2_2f, w2_2f_2, w2_5f 등)
├── autostart/                  # 부팅 자동 시작(systemd) unit 템플릿과 설치 스크립트 (두 로봇 공용)
├── former0045/                 # former-0045 로봇 전용 스크립트
│   ├── run_former_env.sh       # ROS 2 환경 통합 컨트롤러
│   ├── former_nav2_run.sh      # Nav2 백그라운드 구동 래퍼
│   ├── former_nav2_rviz.sh     # Nav2 RViz 모니터링
│   ├── former_slam_run.sh      # SLAM 백그라운드 구동 래퍼
│   ├── former_slam_rviz.sh     # SLAM RViz 모니터링
│   ├── run_former_docker.sh    # RoboClaw 에이전트 구동 런처
│   ├── run_nav_stable.sh       # 실기용 Nav2 기동 (노드 분리 + cmd_vel 리맵 + 스캔 재스탬프)
│   ├── restart_nav.sh          # nav2 컨테이너만 정리 후 재기동
│   └── README.md
└── former0047/                 # former-0047 로봇 전용 스크립트 (Jetson/GPU 특화)
    ├── run_former_env.sh       # ROS 2 환경 통합 컨트롤러 (--runtime nvidia, :0 디스플레이 등)
    ├── former_nav2_run.sh      # Nav2 백그라운드 구동 래퍼
    ├── former_nav2_rviz.sh     # Nav2 RViz 모니터링
    ├── former_slam_run.sh      # SLAM 백그라운드 구동 래퍼
    ├── former_slam_rviz.sh     # SLAM RViz 모니터링
    ├── run_former_docker.sh    # RoboClaw 에이전트 구동 런처
    ├── run_nav_stable.sh       # 실기용 Nav2 기동 (노드 분리 + cmd_vel 리맵 + 스캔 재스탬프)
    ├── restart_nav.sh          # nav2 컨테이너만 정리 후 재기동
    └── README.md
```

## 2. Former 로봇 환경 차이점 및 특징

| 구분 | former0045 | former0047 |
| --- | --- | --- |
| **플랫폼** | 표준 x86 PC | Jetson (aarch64) + NVIDIA GPU |
| **GPU 가속** | `nvidia-smi` 감지 시 가속 활성화 | `--runtime nvidia` 자동 적용 (Tegra 라이브러리 연동) |
| **RViz 시각화** | 기본 호스트 X11 연동 | SSH 포워딩 시 로컬 `:0` 자동 전환 (GLX 가속 및 SIGBUS 방지) |
| **Fontconfig** | 정상 | `fonts-urw-base35` 손상(0바이트) 파일 자동 정리 |
| **카메라 기본 토픽** | `/former0045/camera/color/image_raw` | `/former0047/camera/color/image_raw` |
| **컨테이너 접두사** | `former0045_*` / `robo_claw_former0045` | `former0047_*` / `robo_claw_former0047` |

## 3. 공통 경로 자동 탐색 (Path Resolution)

각 폴더의 실행 스크립트는 자체 완결형(Self-contained)으로 설계되어 상위 스크립트 유무에 의존하지 않으며, 다음 경로들을 안전하게 순차 탐색합니다:

- **파라미터 디렉토리 (`params`)**: 로컬 `former004X/params` 우선 -> 없으면 공유 `former/params` 자동 마운트
- **설정 디렉토리 (`config`)**: 로컬 `former004X/config` 우선 -> 없으면 공유 `former/config` 자동 마운트
- **지도 파일 (`maps`)**: 호스트 실기기 경로(`/home/former/workspace/nav2/maps`) -> 로컬 `maps/` -> 공유 `former/maps/` 순서로 탐색
- **환경 변수 (`.env`)**: 로컬 디렉토리 -> 상위 `former/` -> 저장소 루트 `.env` 자동 로드
- **CycloneDDS (`cyclonedds_conf`)**: 호스트에 디렉토리가 실제로 존재할 때만 안전하게 마운트 (임의 디렉토리 생성 방지)

## 4. 단축 실행 명령어 (Quick Start)

### former0045 구동

```bash
# Navigation2 실행 (데몬 모드)
./former/former0045/former_nav2_run.sh

# Nav2 RViz 모니터링
./former/former0045/former_nav2_rviz.sh

# SLAM 실행 (데몬 모드)
./former/former0045/former_slam_run.sh

# SLAM RViz 모니터링
./former/former0045/former_slam_rviz.sh

# RoboClaw 에이전트 실행
./former/former0045/run_former_docker.sh
```

### former0047 구동 (Jetson 특화)

```bash
# Navigation2 실행 (데몬 모드)
./former/former0047/former_nav2_run.sh

# Nav2 RViz 모니터링 (SSH 환경에서도 :0 디스플레이 자동 감지)
./former/former0047/former_nav2_rviz.sh

# SLAM 실행 (데몬 모드)
./former/former0047/former_slam_run.sh

# SLAM RViz 모니터링
./former/former0047/former_slam_rviz.sh

# RoboClaw 에이전트 실행
./former/former0047/run_former_docker.sh
```

---

## 5. 넓은 공간(30m x 30m) 지도 작성을 위한 최적화 설정

넓은 영역(예: 30m X 30m 이상)에서 SLAM 구동 시 발생하는 포즈 래깅 및 누적 오차를 최소화하기 위해 최적화된 설정 파일인 [mapper_params_wide.yaml](config/mapper_params_wide.yaml)이 제공됩니다.

### 주요 튜닝 포인트
- **라이다 유효 감지 범위 확장 (`max_laser_range: 25.0`)**: 30m 공간의 벽면까지 라이다가 인지할 수 있도록 범위를 대폭 확대(기존 12m)하여 매칭 실패율을 줄였습니다.
- **포즈 그래프 노드 수 및 연산 완화**:
  - `resolution`을 `0.05`(5cm)로 완화하여 해상도 대비 최적화 메모리를 보존합니다.
  - `minimum_travel_distance`를 `0.1`(10cm)로 설정하여 불필요한 스캔 매칭 주기 및 그래프 크기를 줄였습니다.
- **루프 폐쇄(Loop Closure) 서치 영역 확장**: 
  - `loop_search_maximum_distance`를 `10.0`m로 확장하고, `loop_search_space_dimension`을 `15.0`m로 늘려 큰 반경을 회전하고 돌아왔을 때 누적 오차 보정을 더 원활히 처리합니다.
- **오도메트리 의존성 및 루프 클로징 신뢰성 조율**:
  - `distance_variance_penalty: 0.3` 등으로 오도메트리의 가중치를 조율하여 스캔 매칭 비중을 상향했습니다.
  - `loop_match_minimum_chain_size: 15` 및 임계값 상향으로 거짓 매칭 루프 결합을 미연에 방지합니다.

### 실행 방법
호스트의 `former/config` 디렉토리는 컨테이너의 `/ws/config`에 자동 마운트되므로 아래 명령으로 간단하게 넓은 영역 최적화 설정을 주입해 기동할 수 있습니다.

```bash
# former0045
./former/former0045/former_slam_run.sh slam_params_file:=/ws/config/mapper_params_wide.yaml

# former0047
./former/former0047/former_slam_run.sh slam_params_file:=/ws/config/mapper_params_wide.yaml
```

---

## 6. 좁은 공간(Narrow Space/Corridor) 통과를 위한 내비게이션 최적화 설정

좁은 복도, 좁은 출입문 등을 지날 때 코스트맵 장애물 영역(인플레이션)이 너무 크게 부풀어 올라 로봇이 주행 가능 경로가 없다고 판단해 굳어버리는(Freeze) 현상을 극복하기 위한 최적화 설정 파일인 [nav2_params_narrow.yaml](params/nav2_params_narrow.yaml)이 제공됩니다.

### 주요 튜닝 포인트
- **안전 거리 반경 축소 (`inflation_radius: 0.30`)**: 장애물 주변의 인플레이션 영역을 기존 `0.45m`에서 `0.30m`로 대폭 축소하여 좁은 틈새도 통과 대상 경로로 인식되도록 합니다.
- **인플레이션 회피 비용 감쇄율 증가 (`cost_scaling_factor: 10.0`)**: 장애물에서 멀어질수록 감쇄되는 비용 기울기를 가파르게 변경(기존 `3.0`)하여 벽면 바로 옆을 아슬아슬하게 통과할 수 있게 유도합니다.
- **속도 제약 및 가속도 제어**:
  - `max_vel_x`를 `0.3 m/s`(기존 `0.6 m/s`)로 감속하여 협소 환경에서 서행하며 안전을 확보합니다.
  - 급격한 가속으로 스캔 노이즈가 마킹되어 경로가 막히는 것을 방지하고자 가속도 임계값(`acc_lim_x: 1.0`, `acc_lim_theta: 1.6`)을 줄였습니다.
- **경로 추종 정밀도 향상 (`PathAlign.scale` / `PathDist.scale: 45.0`)**: 로컬 플래너 평가 지표 가중치를 상향하여 벽에 부딪치지 않고 복도 중앙의 전역 경로선을 더 칼같이 따라가도록 제어합니다.
- **장애물 회피 가중치 하향 (`BaseObstacle.scale: 0.01`)**: 벽면과 아주 조금만 가까워져도 무조건 주행을 회피하고 멈추던 가중치 비율을 절반으로 완화했습니다.

### 실행 방법
호스트의 `former/params` 디렉토리는 컨테이너의 `/ws/params`에 자동 마운트되므로 아래 명령어를 실행하여 좁은 구역 튜닝 파일을 런치 파라미터(`params_file`)로 오버라이드할 수 있습니다.

```bash
# former0045
./former/former0045/former_nav2_run.sh params_file:=/ws/params/nav2_params_narrow.yaml

# former0047
./former/former0047/former_nav2_run.sh params_file:=/ws/params/nav2_params_narrow.yaml
```

---

## 7. 맵 파일 (`maps/`)

`maps/`에는 사전 작성된 지도 세트(`w2_2f`, `w2_2f_2`, `w2_5f`, `map_konjiam_L_107-1`, 각각 `.pgm` + `.yaml`)가 포함되어 있습니다. 스크립트 실행 시 `-m/--map <파일명>` 옵션으로 자유롭게 지정할 수 있습니다.

| 맵 | 해상도 | 크기 | 비고 |
| --- | --- | --- | --- |
| `w2_2f`, `w2_2f_2`, `w2_5f` | 0.025 m/px | 12~16 m | W2 빌딩 구역 |
| `map_konjiam_L_107-1` | 0.05 m/px | 16.2 × 15.35 m | RTAB-Map(3D SLAM) grid map 기반. 미관측 영역은 unknown(205)으로 남습니다. |

`map_konjiam_L_107-1` 은 RTAB-Map 결과(`grid_map.pgm`)를 그대로 사용하며, 이전 SLAM 맵과 **원점·해상도가 다릅니다**.

초기 자세는 각 로봇의 `initpose.md`(예: `~/konjiam/former/initpose.md`)로 주입합니다. 맵 프레임이 바뀌었거나 다른 지점에서 출발하면 RViz의 `2D Pose Estimate`로 새 위치를 지정하고, 그 값을 `initpose.md`에 갱신하세요. `set_initial_pose: true`(자동 주입) + `always_reset_initial_pose: false`(수동 지정값 유지) 조합을 사용합니다.

기동 후 위치를 바꾸려면 RViz 대신 에이전트에 요청해도 됩니다. RoboClaw의 `set_initial_pose` 스킬은 `/initialpose`로 AMCL 초기 위치를 발행하며, 좌표 대신 기억된 장소명을 받습니다.

```text
사용자: 안방 위치를 초기 위치로 지정해줘
에이전트: set_initial_pose(target_name="안방")  → 시맨틱 맵/RAG 좌표로 /initialpose 발행
```

- 장소명은 시맨틱 맵 또는 RAG에 좌표로 등록되어 있어야 합니다(`get_location`으로 조회 가능한 이름). 등록되지 않은 이름은 좌표를 추측하지 않고 실패로 보고합니다.
- 네비게이션을 재기동해 AMCL 초기 위치를 잃었을 때는 "마지막 위치를 초기 위치로 해줘"로 복구합니다. 에이전트는 TF `map → base_link`가 유효한 동안 마지막 pose(x, y, yaw)를 기억하고(`last_pose` RAG 레코드 1건, 이동 시에만 갱신), 요청 시 그 값을 `/initialpose`로 발행합니다. 복원값은 스냅숏 지연을 감안해 공분산을 넓게(0.25) 넣어 AMCL이 라이다로 보정하게 합니다.
  - 마지막 위치 기억은 RAG 활성(`enable_rag=true`)일 때만 재기동 후에도 남습니다. RAG가 꺼져 있으면 에이전트 프로세스가 살아 있는 동안에만 유효합니다.
  - `/odom` 폴백 좌표는 프레임이 달라 마지막 위치로 기록하지 않습니다.
- 방향은 장소에 저장되지 않으므로 `yaw`를 생략하면 마지막 위치의 yaw(복원 시) 또는 0rad(장소명)로 설정됩니다. 특정 방향이 필요하면 `yaw`(라디안)를 함께 지정합니다.
- 이 요청은 실행 중인 AMCL 상태만 바꾸며 `initpose.md`를 갱신하지 않습니다. 다음 재기동에도 유지하려면 `initpose.md`를 직접 갱신하세요.

## 8. 알려진 문제: nav2 스택 간헐 종료 (`Magick: abort due to signal 11`)

증상은 "주행을 시작하면 네비게이션이 멈춘다"로 보이지만, 실제로는 `former00XX_nav` 컨테이너가 **기동 2~3초 내에 죽어** Nav2 노드가 모두 사라진 상태입니다. 크래시 인스턴스 로그에는 골/조이스틱 관련 기록이 전혀 없습니다(기동 로그 254줄, 수명 2.6초).

```bash
docker ps -a --format '{{.Names}}\t{{.Status}}' | grep former00XX_nav   # Exited 상태 확인
docker logs --tail 20 former00XX_nav | grep -iE "Magick|died"
```

```
[lifecycle_manager_navigation] Managed nodes are active
[lifecycle_manager_navigation] Creating bond timer...
[component_container...] Magick: abort due to signal 11 (SIGSEGV) "Segmentation Fault"...
[ERROR] process has died [pid ..., exit code -6, ...]
```

원인: 기본 `bringup_launch.py` 는 모든 Nav2 노드를 **하나의 component container 프로세스**에 올립니다. 이 프로세스가 SIGSEGV(메시지는 ImageMagick 의 전역 시그널 핸들러가 출력)로 죽으면 네비게이션 스택 전체가 사라집니다. `component_container_isolated` → `component_container` 로 바꾸는 것만으로는 재발했습니다(실측).

### 권장 우회: 노드를 개별 프로세스로 기동

```bash
cd ~/konjiam/former/former00XX
./run_nav_stable.sh          # = run_former_env.sh nav2 --daemon -m <맵> use_composition:=False
```

- 장애가 국소화됩니다(노드 하나가 죽어도 나머지는 살아있음).
- 크래시가 나면 launch 로그에 **어떤 노드가 죽었는지** 그대로 남습니다.
  ```
  [ERROR] [launch]: process has died [pid ..., cmd '/opt/ros/humble/lib/nav2_xxx/xxx ...']
  ```
- 확인: `docker exec former00XX_nav bash -lc 'ps -eo args | grep -cE "[n]av2_|[a]mcl"'` → 12개 수준이면 적용됨. 컨테이너 로그에 `component_container-1` 이 보이면 미적용(`component_container_isolated-1` 은 기본값).

### 대안 (이미지 재빌드 없이 바인드마운트)

`former_navigation2` 의 launch 사본을 바인드마운트할 수 있습니다. 두 로봇 스크립트는 `RC_EXTRA_VOLUMES` 로 추가 마운트를 받습니다.

```bash
mkdir -p patch
sed 's/component_container_isolated/component_container/' \
  ~/dev_ws/src/former_robot/former_navigation2/launch/bringup_launch.py > patch/bringup_launch.py

RC_EXTRA_VOLUMES="-v $PWD/patch/bringup_launch.py:/root/dev_ws/install/former_navigation2/share/former_navigation2/launch/bringup_launch.py:ro" \
  ./former_nav2_run.sh -m map_konjiam_L_107-1.yaml
```

### 근본 대책

- 로봇 개발사 소스/이미지 Dockerfile 이 없어도, 현재 이미지를 베이스로 한 파생 이미지(`FROM former_docker:1.0` + `COPY` 로 launch/파라미터 교체)로 수정본을 구울 수 있습니다.
- 또는 `map_server` 를 컴포넌트에서 분리해 별도 프로세스로 띄워 크래시 영향을 국소화합니다.
- 운영 중에는 컨테이너 생존을 주기적으로 확인해 죽으면 재기동하는 감시(systemd 등)를 두는 것이 안전합니다.
- 참고: 이 이미지 안에서는 `apt-get update` 가 실패합니다(dpkg DB 비어 있음, ROS apt 키 만료). 분석 전용 파생 이미지에 gdb 를 넣고 core dump 로 백트레이스를 얻는 절차는 저장소 루트의 `NAVI_ISSUE.md` 6절을 참고합니다.

## 9. Nav2 기동/구동 이슈 (실기 적용값)

### 9.1 노드 분리 기동

`./run_nav_stable.sh` 는 다음 두 가지를 적용해 기동합니다.

1. `use_composition:=False` — 모든 노드를 개별 프로세스로 실행(AMCL 크래시가 전체 스택을 죽이지 않게, 그리고 크래시 노드를 로그에서 식별)
2. `navigation_launch.py` 사본 바인드마운트 — vendor launch 는 `velocity_smoother` 최종 출력을 `/cmd_vel` 로 보내지만, 이 로봇 베이스는 `/base_controller/cmd_vel_unstamped` 만 구독합니다. 원본에는 `('/cmd_vel', '/base_controller/cmd_vel_unstamped')` 리맵이 **주석 처리**되어 있어, `cmd_vel_smoothed` 를 베이스 토픽으로 직접 리맵합니다.

```bash
cd ~/konjiam/former/former00XX
./run_nav_stable.sh          # 또는 ./restart_nav.sh (기존 컨테이너 정리 후 재기동)
```

확인:
```bash
docker exec former00XX_nav bash -lc 'source /opt/ros/humble/setup.bash; \
  ros2 topic info /base_controller/cmd_vel_unstamped -v | grep -A1 "Node name"'
# velocity_smoother 가 publisher 목록에 있어야 정상
```

### 9.2 RTAB-Map 지도 사용 시 costmap 설정

RTAB-Map grid map 은 미관측 영역이 unknown(205)으로 남아 52% 이상이 미지입니다. `track_unknown_space: true` 이면 그 영역이 통행 불가로 처리되어 `GridBased: failed to create plan` 이 반복됩니다.

```yaml
global_costmap:
  global_costmap:
    ros__parameters:
      track_unknown_space: false   # RTAB 맵의 미지 영역을 통행 가능으로 처리
```

인플레이션 값은 9.6절을 따른다(0.35 / csf 3.0 은 좁은 문에서 계단 코스트장이 된다).

### 9.3 남은 항목

- `controller_server.progress_checker.movement_time_allowance` 는 nav2 에 없는 파라미터명입니다(정식: `movement_timeout`). 현재는 기본값(10 s)로 동작하므로, 장시간 정지 허용이 필요하면 이름을 바로잡아야 합니다.
- `robo_claw_container`(에이전트)가 0047 에서 `/former0045/camera/...` 토픽을 구독하는 것이 확인되었습니다. 로봇 프로필 설정을 확인하세요.

### 9.4 스캔 재스탬프 (AMCL 크래시 대응)

SICK 스캐너는 자체 시계로 `header.stamp` 를 채우기 때문에 호스트 시계와 **+0.4~0.7초** 어긋나고, 이 오차가 커지면 tf2 메시지 필터가 스캔을 드롭합니다. AMCL SIGSEGV 의 실제 원인은 9.5절의 `recovery_alpha` 설정입니다.

`run_nav_stable.sh` 는 `scan_restamp` 노드를 함께 띄워 `/scan` 을 ROS 시간으로 다시 찍어 `/scan_restamped` 로 발행하고, AMCL·costmap 이 그 토픽을 사용하도록 파라미터를 맞춥니다.

```yaml
amcl:
  ros__parameters:
    scan_topic: scan_restamped
global_costmap:
  global_costmap:
    ros__parameters:
      obstacle_layer:
        scan:
          topic: /scan_restamped
local_costmap:
  local_costmap:
    ros__parameters:
      voxel_layer:
        scan:
          topic: /scan_restamped
```

효과(실측, 재기동 직후 55초): `/scan_restamped` 14.98 Hz, `Message Filter dropping` **0건**(이전 수백 건), AMCL 사망 0건.

### 9.5 AMCL SIGSEGV 근본 원인 — `recovery_alpha`

core dump 백트레이스로 `uniformPoseGenerator()` ← `pf_update_resample()` 에서 NULL map 포인터를 역참조해 죽는 것을 확인했습니다. AMCL 은 맵 수신 전에 파티클 필터를 만들어 map 포인터가 NULL 로 고정되고, `recovery_alpha_fast/slow` 가 0 이 아니면 스캔 매칭 점수가 떨어질 때 무작위 파티클을 주입하면서 그 포인터를 사용합니다. Nav2 기본값인 `0.0` 으로 두면 이 경로가 실행되지 않습니다.

```yaml
amcl:
  ros__parameters:
    recovery_alpha_fast: 0.0
    recovery_alpha_slow: 0.0
```

위치를 크게 잃은 경우의 자동 복구는 꺼지므로 RViz 에서 initial pose 를 다시 지정합니다. 상세 분석은 저장소 루트의 `NAVI_ISSUE.md` 3.5절을 참고합니다.

### 9.6 좁은 문 통과 실패 — costmap 기하/인플레이션 (실기 적용값)

증상은 "골은 받고 경로도 그리는데 문 앞에서 멈추거나 회피 기동을 반복" 입니다. 원인은 로봇 실측 기하보다 넓게 잡힌 costmap 입니다.

실측 기준값은 URDF collision mesh 입니다. `former_description/meshes/collision/base_link.stl` 은 **0.404 x 0.404 m** 이고 `wheel_separation` 은 0.305 m 이므로 half-width 는 0.202 m, 모서리 반경은 0.286 m 입니다.

| 항목 | 이전 | 현재 | 근거 |
| --- | --- | --- | --- |
| local footprint | ±0.21 | **±0.205** | 실측 0.404 |
| global 형상 | `robot_radius: 0.26` | **local 과 같은 footprint** | 실제 half-width 보다 29% 컸다 |
| `inflation_radius` | 0.35 | **0.30** | 0.70 m 문의 저비용 밴드가 0 이었다 |
| `cost_scaling_factor` | 3.0 | **10.0** | 경계 cost 167 → 98 로 절벽 완화 |
| global `resolution` | 0.025 | **0.05** | 지도 해상도와 동일(셀 4배 낭비 제거) |
| `BaseObstacle.scale` | 0.02 | **0.10** | PathAlign(32)와 균형, 벽 긁힘 억제 |
| `angular_dist_threshold` | 0.485 | **0.60** | 문 앞 제자리 회전 회피 |

nav2 `InflationLayer` 의 비용은 `252 * exp(-csf * (d - inscribed))` 이고 `d > inflation_radius` 이면 0 입니다. `csf 3.0` 은 감쇠가 느린데 반경이 0.35 로 짧아 경계에서 cost 167 이 0 으로 끊기는 계단이 되고, 그 결과 0.70 m 문은 내부 전체가 고비용입니다. 좁은 실내에서는 짧고 가파른 감쇠(`csf 10`, 반경 0.30)가 맞습니다.

`robot_radius` 를 0.26 으로 두면 문 폭별 전역 경로 여유가 0.70 m 문 ±9 cm, 0.60 m 문 ±4 cm 로 줄어듭니다. footprint 를 실측값으로 맞추면 ±14 cm, ±10 cm 가 됩니다.

검증은 계약 검사 스크립트로 합니다.

```bash
python3 former/scripts/check_nav_params.py
# 결과: PASS (2/2 통과)
```

이 스크립트는 `recovery_alpha` 0.0, footprint/inscribed 범위, global·local inscribed 일치, inflation 절벽(경계 cost ≤ 140), 지도 해상도 일치, `scan_restamped` 토픽을 검사합니다.

주의할 점:

- 회복 기동(`spin`, `backup`)은 `FootprintCollisionChecker` 로 footprint 전체를 검사하므로 **1.0 m 보다 좁은 통로에서는 구조적으로 실패**합니다. 문 앞에서 `spin failed` 가 나오는 것은 설정 오류가 아니며, 복구는 재계획 또는 `wait` 로 유도해야 합니다.
- `FollowPath.critics` 에 `ObstacleFootprint` 가 없어 실제 충돌 판정은 중심점 기준입니다. 코스트맵은 넓게 부풀리는데 접촉 검사는 점 하나인 비대칭 상태이므로, 좁은 문에서 벽을 긁으면 이 크리틱 추가를 검토합니다. 추가하면 문 통과가 더 어려워지므로 9.6 값과 함께 시험합니다.

### 9.7 지도 정리 (고립 점/벽 구멍)

RTAB-Map grid map 을 PGM 으로 변환한 지도는 벽이 5~10 cm 로 얇고 잡음 점이 많습니다. `map_konjiam_L_107-1` 기준 occupied 2,968 셀 중 고립 점(이웃 1개 이하)이 216 개였고, 고립 점 하나가 `inflation_radius` 만큼 부풀면 문을 닫습니다.

```bash
# 진단: 잡음 통계, 병목 폭, clearance 임계값별 계획 가능 면적
python3 former/scripts/nav_map_tools.py analyze former/maps/map_konjiam_L_107-1.pgm

# 정리: 고립 점 제거 + 벽 1셀 구멍 폐쇄 (원본은 유지)
python3 former/scripts/nav_map_tools.py clean \
    former/maps/map_konjiam_L_107-1.pgm \
    former/maps/map_konjiam_L_107-1_clean.pgm
```

정리 결과(`map_konjiam_L_107-1_clean.pgm`, 원본과 동일한 0.05 m/origin)는 다음과 같습니다.

| 항목 | 원본 | 정리 후 |
| --- | --- | --- |
| occupied 셀 | 2,968 | 2,697 |
| 고립 점 | 216 | 25 |
| 계획 가능 면적 (clearance > 0.205 m) | 79,317 | 82,598 |
| 최대 연결 영역 / 컴포넌트 수 | 77,445 / 8 | 82,592 / 3 |

경로별 최소 통과 폭도 함께 넓어집니다(`route` 하위 명령, 시작 위치 `(-0.731, 4.521)` 기준).

| 목적지 | 원본 | 정리 후 |
| --- | --- | --- |
| `(6.59, -2.28)` | 0.70 m | **0.80 m** |
| `(9.55, 0.45)` | 0.70 m | **0.73 m** |
| `(-2.80, -1.05)` | 0.70 m | **0.80 m** |
| `(-1.90, 3.50)` | 0.20 m | **0.60 m** |
| `(-4.90, 3.00)` | 0.36 m | **0.45 m** |

```bash
python3 former/scripts/nav_map_tools.py route former/maps/map_konjiam_L_107-1_clean.pgm \
    -0.731,4.521 6.59,-2.28
```

`route` 는 clearance 임계값을 높여가며 연결성을 이분 탐색해 "이 두 지점을 지나려면 최소 몇 m 문을 통과해야 하는가" 를 계산합니다. 병목 폭 표보다 안정적인 지표이므로 지도·파라미터 변경 전후 비교에 사용합니다.

정리한 지도로 기동하려면 지도 파일을 지정합니다.

```bash
RC_MAP_FILE=map_konjiam_L_107-1_clean.yaml ./restart_nav.sh
```

주의할 점:

- 지도 origin 과 해상도가 같으므로 `initpose.md` 의 초기 위치는 그대로 쓸 수 있습니다.
- 로봇 폭보다 좁은 통로를 지도에서 막는 `--min-passage=0.42` 옵션도 있습니다. 이 맵에서는 1,171 셀이 추가로 막혀 지도 형상이 크게 바뀌므로 기본적으로 쓰지 않습니다. 0.41 m 보다 좁은 틈은 `inscribed 0.205` 때문에 계획 단계에서 이미 통행 불가로 처리됩니다.

## 10. 부팅 시 자동 시작 (베이스, nav2, RoboClaw 에이전트)

두 로봇(`former0045`, `former0047`)은 로봇 베이스(`former_bringup.service`)와 카메라
(`realsense_bringup.service`)를 systemd unit 으로 부팅 시 자동 기동합니다. 여기에 더해
Navigation2, RoboClaw 에이전트, 베이스 health watchdog, CycloneDDS 설정도 같은 방식으로
관리합니다. unit 템플릿과 설치 스크립트는 두 로봇이 공유하는 `former/autostart/` 에
있습니다(실기기에서는 `/home/former/konjiam/former/autostart/`).

### 10.1 unit 구성

| 대상 | unit | 실행 명령 | 재기동 | 로그 |
| --- | --- | --- | --- | --- |
| 베이스 | `former_bringup.service` | `docker run --rm -i ... bringup_robot.launch.py` (전경) | `Restart=always`, 10초 | `journalctl -u former_bringup` |
| 베이스 감시 | `former_bringup_watchdog.timer` → `.service` | `former_bringup_watchdog.sh` (60초 주기) | 30분 3회 한도 | `journalctl -u former_bringup_watchdog` |
| nav2 | `former_nav_bringup.service` | `run_nav_stable.sh` (`RC_FOREGROUND=1`) | `Restart=on-failure`, 15초 | `journalctl -u former_nav_bringup` |
| 에이전트 | `roboclaw_bringup.service` | `run_roboclaw.sh` → `./rclaw launch former kon4X --docker` | `Restart=on-failure`, 20초 | `journalctl -u roboclaw_bringup` |

- 베이스 unit 은 원래 로봇에만 있고 두 로봇의 내용이 서로 달랐습니다. 2026-10-29 부터
  `former/autostart/former_bringup.service.in` 템플릿으로 통일했습니다(로봇별 launch
  인자만 `use_namespace` 로 갈립니다).
- nav2·에이전트 unit 은 `docker.service`, `network-online.target`, `former_bringup.service`,
  `realsense_bringup.service`, `cyclonedds_tunes.service` 이후에 시작합니다(`After` 만
  사용). 베이스 장애로 nav2·에이전트가 함께 내려가지 않게 하기 위해서입니다.
- 종료는 `docker stop --time 30` 으로 lifecycle 종료를 기다립니다.
- 로봇별 컨테이너는 `former0045_nav` / `former0047_nav` 이고, 에이전트는 두 로봇 모두
  `robo_claw_container` 입니다.

### 10.2 베이스 준비와 DDS 설정

베이스·nav2·에이전트는 모두 컨테이너 안에서 같은 `cyclonedds_conf/cyclonedds.xml` 로 DDS
참가자를 만듭니다. 그래서 준비 확인과 감시를 unit 에 넣었습니다.

| 장치 | 하는 일 | 없을 때 증상 |
| --- | --- | --- |
| `ExecStartPre` `wait_for_net.sh` (base) | `cyclonedds.xml` 의 인터페이스(eth0)에 IPv4 주소가 붙을 때까지 최대 30초 대기 | 주소가 없는데 참가자를 만들면 `rmw_create_node: failed to create domain` 으로 `ros2_control_node`·`robot_state_publisher` 가 죽어 `/odom`·TF 가 사라짐 |
| `ExecStartPre` `wait_for_base.sh` (nav) | `/base_controller/odom` publisher 가 나타날 때까지 최대 120초 대기 | nav2 가 TF 없이 올라와 amcl 이 모든 스캔을 버리며 조용히 멈춤 |
| `former_bringup_watchdog.timer` | 60초마다 `ros2 control list_controllers` 로 `base_controller active` 확인, 아니면 `systemctl restart former_bringup` | launch 프로세스는 살아 있고 자식 노드만 죽은 경우를 systemd 가 알 수 없음 |

두 대기 스크립트는 **시간 초과를 실패로 처리하지 않습니다.** 링크가 늦게 올라오거나
일시적으로 없어도 기동을 막지 않고 경고만 남기며, 실제 복구는 watchdog 이 맡습니다.
watchdog 은 30분에 3회까지만 재기동하고 그 뒤에는 `failed` 로 남겨 수동 점검 대상이
됩니다. 관리자가 `systemctl stop former_bringup` 으로 내려 둔 경우에는 아무것도 하지
않습니다.

DDS 인터페이스를 바꾸려면 `former/autostart/cyclonedds.xml` 을 고치고
`sudo ./install_autostart.sh <robot> base` 로 배포합니다. USB 이더넷처럼 부팅 후에
나타나는 인터페이스나, 다른 ROS 배포판(jazzy) 노드가 있는 링크를 바인딩하면 베이스가
통째로 죽습니다(2026-10-29 former0045 사례, `docs/NAVI_ISSUE.md` 3.9).

### 10.3 전경 실행이 필요한 이유

- nav2: `run_former_env.sh` 는 TTY 가 없는 환경에서 `docker run -t` 를 붙이지 않습니다
  (stdin 이 TTY 가 아니면 `-i` 만 사용). `RC_FOREGROUND=1` 이면 `docker run` 이 붙은 채로
  남아 systemd 가 프로세스 수명으로 컨테이너 생존을 감시합니다. 직접 실행할 때는 기존과
  동일하게 백그라운드(`-itd`)로 뜹니다.
- 에이전트: `rclaw launch ... --docker` 는 내부에서 `docker run -it` 을 실행합니다.
  systemd 에는 TTY 가 없어 그대로 부팅 기동하면
  `the input device is not a TTY`(exit 1) 로 즉시 실패합니다. `run_roboclaw.sh` 가
  `script -q -e -c ...` 로 pty 를 붙여 실행하므로 docker 가 TTY 를 얻습니다.
- 참고: `rclaw` 는 `--device /dev/ttyUSB0 --device /dev/ttyACM0` 을 항상 넘기지만
  컨테이너가 `--privileged` 로 뜨기 때문에 두 노드 중 없는 것이 있어도 기동합니다
  (실측: `--privileged` 없이는 실패, 있으면 통과).

### 10.4 설치/해제

```bash
cd /home/former/konjiam/former/autostart

# unit 등록 + 즉시 기동 (root 권한 필요). 대상은 base, nav, claw, all
sudo ./install_autostart.sh former0045 all
sudo ./install_autostart.sh former0047 all

# 베이스 + DDS 설정 + watchdog 만 다시 설치
sudo ./install_autostart.sh former0045 base

# 상태 확인 (systemctl status + 컨테이너 상태)
sudo ./install_autostart.sh former0047 status
journalctl -u former_bringup -f
journalctl -u former_bringup_watchdog -f
journalctl -u former_nav_bringup -f
journalctl -u roboclaw_bringup -f

# 지금만 정지 / 자동 시작 해제
sudo ./install_autostart.sh former0047 stop
sudo ./install_autostart.sh former0047 disable
```

다른 맵으로 자동 기동하려면 설치 전에 `RC_MAP_FILE` 을 지정합니다.

```bash
sudo RC_MAP_FILE=map_konjiam_L_107-1_clean.yaml ./install_autostart.sh former0047 nav
```

### 10.5 주의 사항

- `install` 은 이미 떠 있는 동명 컨테이너(수동 기동분 포함)를 정리하고 다시 기동합니다.
- `base` 설치는 베이스를 **전경 실행**(`docker run --rm -i`)으로 바꿉니다. launch 가
  죽으면 unit 도 종료되어 systemd 가 재기동하므로, 상태는 `docker ps` 대신
  `systemctl status former_bringup` 으로 봅니다.
- `base` 설치는 기존 `cyclonedds_conf/cyclonedds.xml` 을 타임스탬프 백업
  (`cyclonedds.xml.bak.YYYYMMDD_HHMMSS`)으로 남기고 교체합니다. 이미 떠 있는 컨테이너는
  재기동해야 새 설정을 읽으므로, 설정만 바꿨다면 `all` 또는 `base` 뒤에 nav2 도
  재기동합니다.
- `install` 은 교체 전 기존 unit 을 `/etc/systemd/system/<unit>.bak.YYYYMMDD_HHMMSS` 로
  백업합니다. 새 unit 이 문제를 일으키면 백업을 되돌리고 `systemctl daemon-reload` 하면
  됩니다.
- 자동 시작 상태에서는 부팅 직후부터 주행 명령(`/base_controller/cmd_vel_unstamped`)을 받을 수 있습니다. 이동 명령을 스스로 내리지는 않지만, 실기 시험 중에는 nav unit 을 `stop` 으로 내려 두는 편이 안전합니다.
- 초기 위치는 각 로봇의 `initpose.md` 를 매 기동마다 반영하며, 파라미터는 임시 디렉터리(`/tmp/former_initpose_<robot>`)에 생성해 컨테이너 `/ws/params` 로 마운트합니다. 이 경로는 재부팅 시 사라지지만 기동 시 다시 생성됩니다.
- 에이전트는 기동 직후 원격 설정 서버에서 활성 설정을 가져오고, 실패하면
  `~/.robo_claw/config_cache/former_kon4X/` 캐시로 폴백합니다. 캐시가 없는 상태에서
  네트워크도 끊기면 기동에 실패하므로, 새 로봇에서는 먼저 한 번 수동 실행해 캐시를 만듭니다.
- 도커 restart policy(`unless-stopped`) 대신 systemd 를 쓰는 이유는 nav2 의 임시 파라미터
  생성과 장애 시 재기동 정책을 unit 에서 관리하기 위해서입니다.
