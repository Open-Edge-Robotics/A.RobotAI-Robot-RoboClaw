# Former 로봇 구동 및 에이전트 연동 가이드

`former` 폴더에는 Former 로봇의 ROS 2 환경(SLAM, Navigation2 등)을 구동하기 위한 Docker 런처와, RoboClaw 에이전트를 Former 로봇 설정에 맞춰 실행하기 위한 단축 스크립트들이 포함되어 있습니다.

로봇 하드웨어 환경 및 플랫폼 사양(PC vs Jetson, 카메라 토픽 등)에 따라 **`former0045/`** 와 **`former0047/`** 전용 폴더로 분리되어 제공됩니다.

## 1. 디렉토리 구조

```text
former/
├── config/                     # 공용 SLAM 파라미터 (mapper_params.yaml 등)
├── params/                     # 공용 Nav2 파라미터 (nav2_params.yaml 등)
├── maps/                       # 사전 작성된 맵 (w2_2f, w2_2f_2, w2_5f 등)
├── former0045/                 # former-0045 로봇 전용 스크립트
│   ├── run_former_env.sh       # ROS 2 환경 통합 컨트롤러
│   ├── former_nav2_run.sh      # Nav2 백그라운드 구동 래퍼
│   ├── former_nav2_rviz.sh     # Nav2 RViz 모니터링
│   ├── former_slam_run.sh      # SLAM 백그라운드 구동 래퍼
│   ├── former_slam_rviz.sh     # SLAM RViz 모니터링
│   ├── run_former_docker.sh    # RoboClaw 에이전트 구동 런처
│   └── README.md
└── former0047/                 # former-0047 로봇 전용 스크립트 (Jetson/GPU 특화)
    ├── run_former_env.sh       # ROS 2 환경 통합 컨트롤러 (--runtime nvidia, :0 디스플레이 등)
    ├── former_nav2_run.sh      # Nav2 백그라운드 구동 래퍼
    ├── former_nav2_rviz.sh     # Nav2 RViz 모니터링
    ├── former_slam_run.sh      # SLAM 백그라운드 구동 래퍼
    ├── former_slam_rviz.sh     # SLAM RViz 모니터링
    ├── run_former_docker.sh    # RoboClaw 에이전트 구동 런처
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
      inflation_layer:
        inflation_radius: 0.35     # 0.45 에서 완화(통로 inscribed 최소화)
```

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
