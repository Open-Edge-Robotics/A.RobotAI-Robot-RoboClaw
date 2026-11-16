# 시뮬레이션

RoboClaw를 Gazebo/실기 없이 폐루프로 검증하기 위한 시뮬레이션 구성을 설명합니다.
Gazebo 경로(`sim_engine:=classic|gz`)와 전용 시뮬레이터(`sim_engine:=stub`)의
차이, 시나리오 작성법, 제공 인터페이스와 한계를 다룹니다.

## 1. 구성

| 계층 | 엔진 | 용도 | 필요한 외부 패키지 |
| --- | --- | --- | --- |
| T1 전용 시뮬레이터 | `robo_claw_sim` (자체 구현) | 커밋 게이트·시나리오 회귀, 결정론적 판정 | 표준 메시지 패키지 |
| T2 Gazebo Harmonic | `turtlebot4_gz_bringup` | 3D 시각화, Nav2 파라미터 실검증 | `ros_gz_sim`, `turtlebot4_*` |
| T2 Gazebo Classic | `turtlebot3_gazebo` | 위와 동일 (humble 한정) | `gazebo_ros`, `turtlebot3_*` |

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
사용하며, `sim_engine`은 배포판에서 자동 결정됩니다(`jazzy`→`gz`,
그 외 `classic`). Gazebo 없이 전체 스택을 띄우려면 다음처럼 실행합니다.

```bash
ros2 launch robo_claw_bringup robo_claw_sim.launch.py \
  sim_engine:=stub \
  scenario_file:=$PWD/src/robo_claw_sim/scenarios/navigate_basic.yaml \
  use_channel:=false
```

`sim_engine:=stub`에서는 `robo_claw_sim` 노드가 지도·센서·액션 서버를 모두
제공합니다. `run_core:=true`를 주면 `robo_claw_core`까지 함께 기동해 속도 제한과
watchdog 동작을 검증할 수 있습니다.

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
| 발행 | `/odom`, `/scan`, `/imu`, `/map`, `/battery_state`, `/joint_states`, `/amcl_pose`, `/detections` | `/map`은 `TRANSIENT_LOCAL` |
| TF | `map`→`odom`(정적), `odom`→`base_link`(동적), `base_link`→`base_scan`·`camera_link`(정적) | 위치추정을 대체 |
| 액션 | `/navigate_to_pose`, `/spin`, `/follow_waypoints` | `nav2_msgs` 동일 타입 |

카메라 영상은 생성하지 않고 정답 기반 `Detection2DArray`만 발행합니다. 영상
렌더링이 필요한 검증은 T2(Gazebo)를 사용합니다.

## 5. 한계

- 파지 역학·마찰·접촉력은 재현하지 않습니다. 파지 성공/실패는 시나리오가
  선언하고 조작 액션 서버가 그대로 반환합니다.
- 장애물 회피는 제어 법칙 기반이며 Nav2의 costmap/플래너 동작과 같지 않습니다.
  Nav2 파라미터나 BT 동작을 검증하려면 T2를 사용합니다.
- 카메라 영상·점유 격자 갱신(SLAM)은 제공하지 않습니다.
- `use_sim_time`은 기본 `false`입니다. `/clock` 발행(`publish_clock:=true`)은
  옵션이며, 켜는 경우 소비 노드도 `use_sim_time:=true`로 맞춰야 합니다.

## 6. 검증

```bash
task sim-test        # 시나리오 회귀 (Gazebo 불필요)
task unit-test       # 순수 모듈 단위 테스트 포함
task test            # ROS 그래프 테스트 + colcon test 포함
```

`tests/test_ros_integration.py`는 실제 노드를 띄워 `/scan`·`/joint_states` 발행과
`NavigateToPose` 목표 도달을 확인합니다. Gazebo나 GPU가 없어도 실행됩니다.
