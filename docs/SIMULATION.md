# 시뮬레이션

RoboClaw를 Gazebo/실기 없이 폐루프로 검증하기 위한 시뮬레이션 구성을 설명합니다.
Gazebo 경로(`sim_engine:=gz`, ROS 2 Jazzy 전용)와 전용 시뮬레이터(`sim_engine:=stub`)의
차이, 시나리오 작성법, 제공 인터페이스와 한계를 다룹니다.

## 1. 구성

| 계층 | 엔진 | 용도 | 필요한 외부 패키지 |
| --- | --- | --- | --- |
| T1 전용 시뮬레이터 | `robo_claw_sim` (자체 구현) | 커밋 게이트·시나리오 회귀, 결정론적 판정 | `nav2_msgs`, `vision_msgs`, `tf2_ros` 및 ROS 메시지 패키지 |
| T1 백엔드 `kinematic` | 2D 운동학 세계 + ray marching | 기본값. 외부 의존성 없음 | 없음 |
| T1 백엔드 `mujoco` | MuJoCo 물리 + ray casting | 접촉·파지 판정, 후속 카메라 검증 기반 | `mujoco`(선택, `sim-mujoco` extra) |
| T2 Gazebo Harmonic | `turtlebot4_gz_bringup` | 3D 시각화, Nav2 파라미터 실검증 | `ros_gz_sim`, `turtlebot4_*` |

Gazebo 시뮬레이션은 **ROS 2 Jazzy(Harmonic)** 만 지원합니다. Humble의 Gazebo
Classic(`gazebo_ros`, `turtlebot3_*`) 경로는 유지 비용과 런치 분기 오류 때문에
제거되었고, humble 환경에서는 런치·CLI·스크립트가 모두 즉시 오류로 중단합니다.

`robo_claw_core`는 `ros2_control`을 사용하지 않고 `/joint_states`를 구독하며
`cmd_vel`을 발행합니다. 따라서 시뮬레이터가 재현해야 하는 것은 Twist 적분,
센서 합성, 내비게이션 액션 의미론까지이며 물리 엔진이 필요하지 않습니다.

T1은 자체 `NavigateToPose`/`Spin`/`FollowWaypoints` 액션 서버를 제공하고,
T2는 같은 세계 모델에 실제 Nav2를 붙여 사용합니다.

## 2. 실행

```bash
# 의존성 점검 (Gazebo 패키지 / Gazebo 없는 stub)
task sim-check
./scripts/check_sim_deps.sh --engine stub

# 시나리오 회귀 (기본: scenarios/navigate_basic.yaml)
task sim-test
task sim-test -- src/robo_claw_sim/scenarios/my_scenario.yaml

# 시뮬레이터 노드만 실행
ros2 run robo_claw_sim sim_node --ros-args -p scenario_file:=/path/to/scenario.yaml

# 시나리오 실행 + 결과 JSON 저장
ros2 run robo_claw_sim sim_runner scenarios/navigate_basic.yaml --output /tmp/outcome.json
```

`rclaw sim`/`rclaw launch ... --sim`/`sim/run_sim.sh`는 모두 같은 런치 파일을
사용하며, `sim_engine`은 Gazebo Harmonic(`gz`)이 기본값입니다. Gazebo 없이 전체
스택을 띄우려면 `--engine stub`을 지정합니다.

### 2.1 CLI 실행

`rclaw`에서 시뮬레이션 엔진과 시나리오를 직접 선택할 수 있습니다.

```bash
# Gazebo 없이 전용 시뮬레이터로 전체 스택 기동 (권장)
./rclaw sim --engine stub \
  --scenario src/robo_claw_sim/scenarios/navigate_basic.yaml

# 로봇/환경 설정을 적용해 기동 (launch 경로)
./rclaw launch former w2_2f_2 --sim --engine stub \
  --scenario src/robo_claw_sim/scenarios/navigate_basic.yaml

# 기존 Gazebo 경로 (Jazzy/Harmonic)
./rclaw sim               # 기본 엔진은 gz
./rclaw sim --engine gz   # Gazebo Harmonic 명시
```

| 플래그 | 값 | 기본값 | 설명 |
| --- | --- | --- | --- |
| `--engine` | `auto` · `stub` · `gz` | `auto` | 시뮬레이션 엔진. `auto`는 Gazebo Harmonic(`gz`)입니다(`gz` 별칭: `harmonic`). 제거된 `classic`/`gazebo` 값은 오류로 거부합니다 |
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
task sim-test-mujoco                                 # 시나리오를 MuJoCo 로 실행
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

한계: 실제 매니퓰레이터의 관절 구성·IK를 재현하지 않고, 마찰 기반 파지력도
튜닝하지 않았습니다. `control_msgs/FollowJointTrajectory` 액션 서버와 시나리오
`expect.grasped_objects` 판정은 다음 단계에서 이 백엔드 위에 올립니다.

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
task sim-test-mujoco # 같은 시나리오를 MuJoCo 백엔드로 회귀
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

시나리오를 추가하면 `tests/test_scenario.py::test_shipped_scenario_is_valid`가 스키마와
목표 도달 가능성(점유 위치 아님)을 검사합니다. 반응형 제어기는 전역 계획이 없으므로,
목표 사이 직선 경로가 장애물을 스치지 않도록 배치해야 두 백엔드 판정이 갈라지지
않습니다(스치는 경로에서는 MuJoCo 접촉이 미끄러져 통과하고 운동학은 정지합니다).

`tests/test_ros_integration.py`는 실제 노드를 띄워 `/scan`·`/joint_states` 발행과
`NavigateToPose` 목표 도달을 확인합니다. Gazebo나 GPU가 없어도 실행됩니다.

## 7. 트러블슈팅

Gazebo 경로(`sim_engine:=gz`)에서 실제로 관측된 실패와 조치입니다.

### 7.1 `Failed to find a free participant index`

```text
[parameter_bridge-9] Failed to find a free participant index for domain 0
[ERROR] [rmw_cyclonedds_cpp]: rmw_create_node: failed to create domain, error Error
terminate called after throwing an instance of 'rclcpp::exceptions::RCLError'
```

TurtleBot4 Gazebo는 브리지·컨트롤러·툴박스까지 50개 이상의 프로세스를 띄웁니다.
CycloneDDS의 참여자 자동 인덱스 범위를 넘으면 노드가 기동 직후 `-6`으로 abort 하고,
`clock_bridge`까지 함께 죽어 `use_sim_time` 노드가 전부 멈춥니다(그 결과
`controller_manager: No clock received`가 반복됩니다).

`CYCLONEDDS_URI`에 `Discovery/MaxAutoParticipantIndex`를 넣어 상한을 올립니다
(`robo_claw_cli/launcher/dds.go`가 인라인 URI를 생성하며, `Dockerfile`·
`sim/jazzy/cyclonedds_config.xml`도 같은 값을 씁니다).

| 설정 | 한 도메인 동시 참여자(실측) |
| --- | --- |
| 기본(상한 미지정) | 32개 |
| `MaxAutoParticipantIndex=200` | 48개 시험에서 실패 0 |

`rclaw`가 만든 URI가 적용됐는지는 `RC_DEBUG` 로그의 실행 명령과
`echo "$CYCLONEDDS_URI"`로 확인합니다.

### 7.2 `Agent heartbeat missing for 30.0s`

```text
Agent heartbeat missing for 30.0s; attempting executor recovery
Agent heartbeat missing for 30.0s after recovery attempt; terminating so the service can restart
```

에이전트 상태 하트비트는 ROS 타이머(기본 1Hz)로 발행되는데, 워치독이 **벽시계**로
30초를 재면 Gazebo 실시간 배율이 낮은 구간(센서 렌더 초기화, 헤비 월드)에서 정상
동작을 무음으로 오판해 에이전트를 종료합니다. `use_sim_time`이 참이면
`clock_for_node`(`agent_node/heartbeat_watchdog.py`)가 노드 시계(시뮬레이터 시간)를
사용합니다. 실기기에서는 두 시계가 같으므로 동작이 변하지 않습니다. 시뮬레이션에서
executor가 실제로 멈추면 `/clock` 구독도 멈춰 sim 시간이 정지하므로 이 워치독은
침묵합니다(시뮬레이션에서는 런치 로그와 액션 타임아웃으로 판단).

### 7.3 `create` 가 `Requesting list of world names` 만 반복하며 멈춤

```text
[create-7] [INFO] [ros_gz_sim]: Requesting list of world names.
[create-7] [INFO] [ros_gz_sim]: Requesting list of world names.   ← 5초마다 무한 반복
[spawner-32] waiting for service /controller_manager/list_controllers to become available...
```

이전 실행이 남긴 `gz sim server` 가 살아 있어 새 실행의 서버와 같은 gz-transport
파티션에서 충돌한 상태입니다. `gz sim` 은 GUI/server 를 **각자 새 프로세스 그룹**으로
띄우므로 런치 프로세스 그룹을 정리해도 회수되지 않고, 부모가 systemd 로 바뀝니다
(`ps -o pid,ppid,args -p <pid>` 로 확인). 두 서버가 있으면 `ros_gz_sim create` 가
월드 목록을 받지 못해 로봇이 스폰되지 않고, 이후 단계(컨트롤러 스폰·토픽)가 모두
멈춥니다.

`rclaw` 는 시뮬레이션 실행 전후와 `rclaw kill` 에서 이 프로세스(`gz sim`)를
회수합니다. `gz` 는 프로세스 타이틀을 `"gz sim server"` 처럼 한 덩어리로
바꿔치기하므로 회수 로직은 argv[0] 을 공백으로 다시 나눠 비교합니다. 수동 정리가
필요하면 다음을 실행합니다.

```bash
./rclaw kill                 # RoboClaw 노드 + gz 시뮬레이터 프로세스 정리
ps -eo pid,args | grep "gz sim"
```

### 7.4 `ddsi_udp_conn_write ... failed with retcode`

```text
[parameter_bridge-29] ddsi_udp_conn_write to udp/10.177.17.218:7497 failed with retcode -58
[gazebo-1] [WARN] [diffdrive_controller]: Velocity command timed out. Braking.
```

시뮬레이션이 실기기와 같은 DDS 도메인(기본 0)을 쓰면 사무실 LAN 의 다른 참여자와
섞이고, 참여자 인덱스가 다른 인터페이스 바인딩으로 중복되면 한쪽 locator 로의 쓰기가
계속 실패한다. 실패 포트는 `7400 + 250*domain + 2*index + 1` 이므로 어느 참여자인지
역산할 수 있다(예: 도메인 0 포트 7497 → index 48, 도메인 42 포트 17997 → index 48).

`rclaw` 는 시뮬레이션을 **전용 ROS 도메인(기본 42)** 으로 격리해 실기기·LAN 참여자와
분리한다. 호스트에 `ROS_DOMAIN_ID` 를 지정했거나 `CYCLONEDDS_URI` 를 직접 지정했다면
그 값을 존중한다.

| 변수 | 기본값 | 의미 |
| --- | --- | --- |
| `RC_SIM_DOMAIN_ID` | `42` | 시뮬레이션 전용 도메인 |
| `ROS_DOMAIN_ID` | (미설정) | 지정하면 시뮬레이션도 그 도메인 사용 |
| `CYCLONEDDS_URI` | (미설정) | 지정하면 기본 DDS 설정 대신 사용(다중 NIC 등) |

인터페이스를 임의로 좁히지 않는다. 시뮬레이터와 `ros2` CLI·대시보드 어댑터가 같은
바인딩 집합을 써야 참여자 인덱스가 충돌하지 않는다(loopback 전용으로 제한하면 외부
도구가 시뮬레이션 그래프를 보지 못하고 실패 로그가 오히려 늘어난다 — 실측).

시뮬레이션 그래프를 다른 호스트에서 봐야 하면 `ROS_DOMAIN_ID=42` 로 맞추고
`CYCLONEDDS_URI` 에 인터페이스/피어를 지정한다.

### 7.5 무해한 경고

| 메시지 | 의미 |
| --- | --- |
| `Desired controller update period (0.001 s) is faster than the gazebo simulation period (0.003 s)` | TurtleBot4 설정상 제어 주기가 물리 스텝보다 짧습니다. 컨트롤러는 정상 로드·활성화됩니다 |
| `turtlebot4_node: Service stop_motor/oakd_stop_camera unavailable` | 기동 초기 서비스 준비 전 호출입니다. 이후 사용 가능 |
| `kdl_parser: root link ... has an inertia` | URDF 구조 경고이며 동작에 영향이 없습니다 |
| `diffdrive_controller: Velocity command timed out. Braking.` | 기동 시 한 번 들어온 `cmd_vel`(정지 명령) 이후 새 명령이 없어 0.5초마다 남는 경고입니다. T2 경로에는 상시 주행 명령원이 없으므로 정상입니다 |
