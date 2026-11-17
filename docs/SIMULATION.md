# 시뮬레이션

RoboClaw를 Gazebo/실기 없이 폐루프로 검증하기 위한 시뮬레이션 구성을 설명합니다.
전용 시뮬레이터(`sim_engine:=stub`)의 시나리오 작성법, 제공 인터페이스와 한계를
다룹니다. Gazebo 경로(Harmonic/Turtlebot4, Classic/Turtlebot3)는 제거되었습니다.

## 1. 구성

| 계층 | 엔진 | 용도 | 필요한 외부 패키지 |
| --- | --- | --- | --- |
| T1 전용 시뮬레이터 | `robo_claw_sim` (자체 구현) | 커밋 게이트·시나리오 회귀, 결정론적 판정 | `nav2_msgs`, `vision_msgs`, `tf2_ros` 및 ROS 메시지 패키지 |
| T1 백엔드 `kinematic` | 2D 운동학 세계 + ray marching | 기본값. 외부 의존성 없음 | 없음 |
| T1 백엔드 `mujoco` | MuJoCo 물리 + ray casting | 접촉·파지 판정, 후속 카메라 검증 기반 | `mujoco`(선택, `sim-mujoco` extra) |

Gazebo 경로는 유지 비용과 런치 분기 오류 때문에 제거되었습니다. `sim_engine`은
`stub` 하나이며, 다른 값을 주면 런치가 즉시 오류로 중단합니다.

`robo_claw_core`는 `ros2_control`을 사용하지 않고 `/joint_states`를 구독하며
`cmd_vel`을 발행합니다. 따라서 시뮬레이터가 재현해야 하는 것은 Twist 적분,
센서 합성, 내비게이션 액션 의미론까지이며 물리 엔진이 필요하지 않습니다.

T1은 자체 `NavigateToPose`/`Spin`/`FollowWaypoints` 액션 서버를 제공하고,
T2는 같은 세계 모델에 실제 Nav2를 붙여 사용합니다.

## 2. 실행

```bash
# 의존성 점검 (Gazebo 패키지 / Gazebo 없는 stub)
task sim-check   # Gazebo 대신 nav2_msgs/vision_msgs/tf2_ros/robo_claw_sim 점검
./scripts/check_sim_deps.sh

# 시나리오 회귀 (기본: scenarios/navigate_basic.yaml)
task sim-test
task sim-test -- src/robo_claw_sim/scenarios/my_scenario.yaml

# 시뮬레이터 노드만 실행
ros2 run robo_claw_sim sim_node --ros-args -p scenario_file:=/path/to/scenario.yaml

# 시나리오 실행 + 결과 JSON 저장
ros2 run robo_claw_sim sim_runner scenarios/navigate_basic.yaml --output /tmp/outcome.json
```

`rclaw sim`/`rclaw launch ... --sim`은 같은 런치 파일을 사용하며, 엔진은 `stub`
하나입니다(`--engine auto`/`stub` 만 허용, `gz`/`classic` 은 거부).

### 2.1 CLI 실행

`rclaw`에서 시뮬레이션 엔진과 시나리오를 직접 선택할 수 있습니다.

```bash
# Gazebo 없이 전용 시뮬레이터로 전체 스택 기동 (권장)
./rclaw sim --engine stub \
  --scenario src/robo_claw_sim/scenarios/navigate_basic.yaml

# 로봇/환경 설정을 적용해 기동 (launch 경로)
./rclaw launch former w2_2f_2 --sim --engine stub \
  --scenario src/robo_claw_sim/scenarios/navigate_basic.yaml

./rclaw sim               # 기본 엔진은 stub
```

| 플래그 | 값 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `--engine` | `auto` · `stub` | `auto` | 시뮬레이션 엔진. `auto`=stub. 제거된 `gz`/`classic`/`gazebo` 값은 오류로 거부합니다 |
| `--scenario` | YAML 경로 | 없음 | `stub` 엔진이 사용할 시나리오. 미지정 시 기본 방(8×6m)을 사용합니다 |

`rclaw` 는 시뮬레이션을 전용 ROS 도메인(기본 42, `RC_SIM_DOMAIN_ID`)으로 격리해
실기기·사무실 LAN 의 DDS 참여자와 섞이지 않게 합니다. 같은 터미널에서
`ros2 topic list` 로 시뮬레이션을 보려면 `export ROS_DOMAIN_ID=42` 를 먼저 실행합니다.
`ROS_DOMAIN_ID`/`CYCLONEDDS_URI` 를 직접 지정하면 그 값을 그대로 사용합니다.

`--engine`/`--scenario`는 `--sim`과 함께 씁니다(`rclaw launch`는 `--sim` 없이 쓰면
사용 오류를 반환합니다). 종료는 `Ctrl+C`이며, `rclaw kill`은 시뮬레이터 노드
(`sim_node`)까지 정리합니다.

`stub` 엔진은 벽시계 시간(20Hz)으로 동작하고 `/clock`을 발행하지 않으므로 하위
스택에는 `use_sim_time:=false`가 전달됩니다. 시나리오에서 `use_sim_time:=true`를
직접 주면 노드 타이머가 멈춰 heartbeat watchdog이 에이전트를 종료하므로 주지
않습니다.

`sim_engine:=stub`에서는 `robo_claw_sim` 노드가 지도·센서·액션 서버를 모두
제공합니다. `run_core:=true`를 주면 `robo_claw_core`까지 함께 기동해 속도 제한과
watchdog 동작을 검증할 수 있습니다.

```bash
# 런치 파일 직접 실행 (CLI 없이)
ros2 launch robo_claw_bringup robo_claw_sim.launch.py \
  sim_engine:=stub \
  scenario_file:=$PWD/src/robo_claw_sim/scenarios/navigate_basic.yaml \
  use_channel:=false
```

### 2.2 시뮬레이터 백엔드 (`kinematic` | `mujoco`)

T1 시뮬레이터는 물리/센서 백엔드를 교체할 수 있습니다. 시나리오 YAML·판정·ROS
인터페이스는 두 백엔드가 동일하므로, 같은 시나리오를 양쪽에서 돌려 결과를 비교할 수
있습니다.

| 백엔드 | 상태 | 특징 |
| --- | --- | --- |
| `kinematic`(기본) | 안정 | 2D 그리드 적분 + ray marching. 외부 의존성 없음 |
| `mujoco` | 실험적(Phase 0) | MuJoCo 물리. 벽·장애물 접촉과 MuJoCo ray casting 사용 |

```bash
# 의존성 설치 (선택 extra)
uv sync --extra sim-mujoco      # 개발 환경
pip install mujoco              # 이미지/호스트

# 백엔드 선택
task sim-test -- --backend mujoco                    # 같은 시나리오를 MuJoCo 로 실행
task sim-compare                                     # 두 백엔드 판정 비교
RC_SIM_RUN_TIMEOUT=180 task sim-compare              # 실행당 제한 시간(초) 조정
ros2 run robo_claw_sim sim_runner --backend mujoco \
  src/robo_claw_sim/scenarios/navigate_two_rooms.yaml
./rclaw sim --engine stub sim_backend:=mujoco        # launch 인자로 지정
```

MuJoCo 백엔드의 설계와 한계는 다음과 같습니다.

- 세계는 시나리오 YAML에서 MJCF로 매 실행 생성합니다(별도 자산 없음,
  `robo_claw_sim/backends/scene.py`). 두 백엔드가 같은 시나리오를 공유합니다.
- 로봇은 free joint + 원기둥 베이스이며, `cmd_vel`을 매 물리 스텝의 자유 관절
  속도로 주입합니다. 바퀴 마찰·서스펜션 파라미터 튜닝 없이도 재현성이 유지되고,
  접촉은 벽·장애물에서만 발생합니다.
- 충돌 규칙은 운동학 백엔드와 같습니다. 접촉이 감지되면 충돌 카운트를 올리고 그
  tick의 남은 이동을 버립니다.
- LaserScan은 `mj_ray`로 합성하며 로봇 body는 광선 대상에서 제외합니다.
- 아직 하지 않는 것: 카메라 렌더, 관절/파지 동역학, 물체 질량·마찰. 이들은
  후속 단계(조작·인지 검증)에서 추가합니다.

### 2.3 조작·파지 판정 (실험적, `mujoco` 백엔드)

`mujoco` 백엔드는 로봇에 3축 직교 슬라이더 팔(`arm_x`/`arm_y`/`arm_z`)과 평행 조
그리퍼(`gripper_joint`)를 붙이고, 시나리오의 `objects`를 free body로 장면에 넣습니다.
파지 성공 여부는 **양쪽 조가 물체에 접촉하고 조 간격이 물체 지름 이하**일 때로
판정합니다(운동학 백엔드는 조작을 지원하지 않으며, 요청하면 명확한 오류로 중단).

| 항목 | 구현 |
| --- | --- |
| 관절 목표 | `set_joint_targets()` — 목표값으로 스텝당 0.02m씩 이동(위치 서보 근사) |
| 파지 판정 | `grasp_state()` — 접촉 수·조 간격·말단-물체 거리 조합, 성립 후 조가 열릴 때까지 유지 |
| 운반 | 파지 중 물체 자세를 말단에 맞추는 부착 근사(kinematic attach) |
| 물체 자세 | `object_pose(name)` — 시나리오 객체의 (x, y, yaw) |

```python
from robo_claw_sim.backends import build_backend
from robo_claw_sim.backends.manipulation import ManipulationConfig

backend = build_backend(
    "mujoco", world=scenario.world, manipulation=ManipulationConfig()
)
backend.set_joint_targets({"arm_x": 0.35, "arm_z": -0.07, "gripper_joint": 0.10})
backend.step(Twist2D(), 0.3)          # 접근
backend.set_joint_targets({"gripper_joint": -0.02})
backend.step(Twist2D(), 0.3)          # 폐합
assert backend.grasp_state().grasped_object == "cup_1"
```

`serve_manipulation:=true` 로 노드를 띄우면 `control_msgs/FollowJointTrajectory`
액션 서버(기본 `/sim_arm_controller/follow_joint_trajectory`)가 함께 뜹니다. 조작
스택이 쓰는 것과 같은 메시지이므로 스킬 코드를 수정하지 않고 시뮬레이터를 붙일 수
있습니다. 시나리오는 궤적과 기대 파지를 함께 선언합니다.

```yaml
manipulation:
  points:
    - {sec: 2.0, joints: {arm_x: 0.35, arm_z: -0.07, gripper_joint: 0.10}}  # 접근
    - {sec: 3.5, joints: {arm_x: 0.35, arm_z: -0.07, gripper_joint: -0.02}} # 폐합
    - {sec: 5.5, joints: {arm_x: 0.35, arm_z: 0.15, gripper_joint: -0.02}}  # 들어올리기
expect:
  min_goals_reached: 0
  grasped_objects: [cup_1]
```

```bash
ros2 run robo_claw_sim sim_runner --backend mujoco \
  src/robo_claw_sim/scenarios/grasp_basic.yaml
```

한계: 실제 매니퓰레이터의 관절 구성·IK를 재현하지 않고, 마찰 기반 파지력도
튜닝하지 않았습니다. `task sim-compare` 는 조작 시나리오를 기본 비교에서 제외합니다
(kinematic 백엔드가 조작을 지원하지 않음).

### 2.5 시각화

| 방법 | 보이는 것 | 사용 |
| --- | --- | --- |
| RViz2 + 마커 | 지도·`/scan`·TF·장애물/객체 마커·카메라 영상 | `task sim-view` (kinematic/mujoco 공통) |
| MuJoCo 대화형 뷰어 | 실제 지오메트리·접촉·팔/그리퍼 상태 | `sim_backend:=mujoco sim_viewer:=true` (DISPLAY 필요) |
| Foxglove Studio | 같은 토픽을 브라우저/원격에서, mcap 기록 | `ros2 run foxglove_bridge foxglove_bridge` 후 웹 접속 |

```bash
task sim-view                          # 시뮬레이터(sim_viz:=true) + RViz2
./scripts/sim_view.sh mujoco           # MuJoCo 백엔드로 RViz2
./rclaw sim --viz                      # CLI 로 마커 발행(도메인 42)
./rclaw sim --sim-backend mujoco --viewer   # CLI 로 MuJoCo 뷰어
ros2 launch robo_claw_bringup robo_claw_sim.launch.py \
  sim_engine:=stub sim_backend:=mujoco sim_viewer:=true   # MuJoCo 뷰어
```

`sim_viz:=true` 는 `/sim/world_markers`(`visualization_msgs/MarkerArray`)로 로봇
footprint·장애물·객체를 발행합니다. RViz 설정은
`src/robo_claw_bringup/config/sim.rviz`이며 Map/LaserScan/TF/MarkerArray/Image 표시가
켜져 있습니다. 원격 리뷰가 필요하면 `foxglove_bridge`를 같은 토픽에 붙이면 됩니다
(별도 설치: `sudo apt install ros-jazzy-foxglove-bridge`).

### 2.4 카메라 RGB-D 렌더 (실험적, `mujoco` 백엔드)

`mujoco` 백엔드는 로봇 헤드 카메라(`head_cam`, 정적 TF `base_link→camera_link`와 같은
위치/자세)로 오프스크린 렌더를 수행합니다. `publish_camera:=true` 로 켜면
`camera_publish_every_n_ticks`(기본 10 = 20Hz에서 2fps)마다 RGB와 depth를 발행합니다.

| 파라미터 | 기본값 | 설명 |
| --- | --- | --- |
| `publish_camera` | `false` | 카메라 렌더/발행 활성화(kinematic 백엔드는 경고 후 무시) |
| `camera_topic` | `/camera/color/image_raw` | RGB(`rgb8`) |
| `camera_depth_topic` | `/camera/depth/image_raw` | depth(`32FC1`, m) |
| `camera_publish_every_n_ticks` | `10` | 발행 주기(tick 배수) |

오프스크린 렌더에는 GL 백엔드가 필요합니다. GPU 호스트는 `MUJOCO_GL=egl`, GL이 없는
CI는 `MUJOCO_GL=osmesa`(CPU) 또는 미설정(렌더 테스트 skip)으로 둡니다. 렌더러 생성에
실패하면 노드는 한 번 경고하고 카메라 발행을 끄며, 나머지 시뮬레이션은 계속 돕니다.

```bash
MUJOCO_GL=egl ros2 run robo_claw_sim sim_node --ros-args \
  -p backend:=mujoco -p publish_camera:=true
```

## 3. 시나리오 스키마

```yaml
name: navigate_basic
timeout_sec: 60
world:
  width: 8.0
  height: 6.0
  resolution: 0.05
  robot_radius: 0.22
  robot: {x: 1.0, y: 1.0, yaw: 0.0}
  obstacles:
    - {type: box, name: wall, x: 4.0, y: 3.6, width: 0.3, height: 2.4}
    - {type: circle, name: pillar, x: 6.2, y: 1.6, radius: 0.35}
  objects:
    - {name: cup_1, class_id: cup, x: 2.2, y: 1.2, radius: 0.06}
goals:
  - {x: 2.0, y: 2.4, tolerance: 0.3, yaw: 1.57, label: near_cup}
expect:
  max_collisions: 0
  min_goals_reached: 1
  max_elapsed_sec: 60
  require_no_abort: true
injections:
  - {kind: scan_drop, at_sec: 5.0, duration_sec: 2.0}
```

`objects`의 `class_id`가 검출 정답으로 사용됩니다. 좌표 단위는 미터, 각도는
라디안입니다. `expect`는 `robo_claw_sim.scenario.evaluate`가 그대로 판정에
사용하므로 시나리오 파일만으로 통과 조건이 결정됩니다.

### 실패 주입

| `kind` | 의미 | 주요 필드 |
| --- | --- | --- |
| `scan_drop` | `/scan` 발행 중단 | `at_sec`, `duration_sec` |
| `imu_drop` | `/imu` 발행 중단 | `at_sec`, `duration_sec` |
| `battery_drain` | 배터리 잔량 즉시 변경 | `battery_percent` |
| `nav_reject` | 내비게이션 목표 즉시 거부 | `goal_index` |
| `nav_abort` | 목표 수행 중 abort | `goal_index`, `at_sec` |

## 4. 제공 인터페이스

| 구분 | 이름 | 비고 |
| --- | --- | --- |
| 구독 | `/cmd_vel`, `/initialpose` | `cmd_vel_topic` 등으로 변경 가능 |
| 발행 | `/odom`, `/scan`, `/imu`, `/map`, `/battery_state`, `/joint_states`, `/amcl_pose`, `/object_detector_node/detections` | `/map`은 `TRANSIENT_LOCAL`; detection 토픽은 파라미터로 변경 가능 |
| TF | `map`→`odom`(정적), `odom`→`base_link`(동적), `base_link`→`base_scan`·`camera_link`(정적) | 위치추정을 대체 |
| 액션 | `/navigate_to_pose`, `/spin`, `/follow_waypoints` | `nav2_msgs` 동일 타입 |

카메라 영상은 생성하지 않고 정답 기반 `Detection2DArray`만 발행합니다. 영상
렌더링이 필요한 검증은 T2(Gazebo)를 사용합니다.

## 5. 한계

- 파지 역학·마찰·접촉력은 재현하지 않습니다. 파지 성공/실패는 시나리오가
  선언하고 조작 액션 서버가 그대로 반환합니다. (`mujoco` 백엔드는 접촉 해석 기반을
  제공하지만, 파지용 관절/그리퍼 모델은 아직 넣지 않았습니다.)
- 장애물 회피는 제어 법칙 기반이며 Nav2의 costmap/플래너 동작과 같지 않습니다.
  Nav2 파라미터나 BT 동작을 검증하려면 T2를 사용합니다.
- 카메라 영상·점유 격자 갱신(SLAM)은 제공하지 않습니다.
- `use_sim_time`은 기본 `false`입니다. `/clock` 발행(`publish_clock:=true`)은
  옵션이며, 켜는 경우 소비 노드도 `use_sim_time:=true`로 맞춰야 합니다.

## 6. 검증

```bash
task sim-test        # 시나리오 회귀 (Gazebo 불필요, kinematic 백엔드)
task sim-test -- --backend mujoco   # 같은 시나리오를 MuJoCo 백엔드로 회귀
task sim-compare     # 두 백엔드의 판정/계측 비교
task unit-test       # 순수 모듈 단위 테스트 포함
task test            # ROS 그래프 테스트 + colcon test 포함
```

`sim-compare`는 시나리오별로 두 백엔드의 `passed`·도달 goal 수·주행 거리를 나란히
출력하고, 실행 오류나 판정 불일치가 있으면 exit 1로 끝납니다. 백엔드 동작을 바꿀 때
이 표의 주행 거리 차이가 커지면 물리 모델이 어긋난 신호로 봅니다.

2026-11-17 기준 저장소 시나리오 7개가 모두 두 백엔드에서 같은 판정을 냈습니다
(판정 불일치 0건, 최대 주행거리 차이 0.5%, 평균 0.3%, 전체 실행 약 5분).

| 시나리오 | 확인하는 것 |
| --- | --- |
| `navigate_basic` | 기본 회귀(목표 2개, 정답 객체 2개) |
| `navigate_two_rooms` | 벽+문 구조를 지나는 경로, 근접 ray 판정 |
| `navigate_three_goals` | 목표 3개 연속 수행, goal 순회 |
| `navigate_corridor` | 좁은 복도와 중간 턱, 벽 근접 주행 |
| `navigate_obstacle_field` | 장애물이 흩어진 넓은 방에서 회피 주행 |
| `navigate_small_room` | 좁은 방에서 벽에 붙은 목표 왕복(근접 충돌 판정) |
| `navigate_scan_drop` | `/scan` 발행 중단 주입 하에서 판정 유지 |
| `grasp_basic` | (mujoco 전용) 접근→폐합→들어올리기 후 파지 성공 판정 |

시나리오를 추가하면 `tests/test_scenario.py::test_shipped_scenario_is_valid`가 스키마와
목표 도달 가능성(점유 위치 아님)을 검사합니다. 반응형 제어기는 전역 계획이 없으므로,
목표 사이 직선 경로가 장애물을 스치지 않도록 배치해야 두 백엔드 판정이 갈라지지
않습니다(스치는 경로에서는 MuJoCo 접촉이 미끄러져 통과하고 운동학은 정지합니다).

`tests/test_ros_integration.py`는 실제 노드를 띄워 `/scan`·`/joint_states` 발행과
`NavigateToPose` 목표 도달을 확인합니다. Gazebo나 GPU가 없어도 실행됩니다.
