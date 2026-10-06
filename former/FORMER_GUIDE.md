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

`maps/`에는 사전 작성된 지도 3세트(`w2_2f`, `w2_2f_2`, `w2_5f`, 각각 `.pgm` + `.yaml`)가 포함되어 있습니다. 스크립트 실행 시 `-m/--map <파일명>` 옵션으로 자유롭게 지정할 수 있습니다.
