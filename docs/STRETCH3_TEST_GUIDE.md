# Stretch 3 실기 테스트 가이드

본 문서는 Hello Robot Stretch 3 실기에서 robo_claw manipulation 기능(joint 기반 + pose 기반 IK)을 점검하는 절차를 다룹니다. MuJoCo 시뮬레이션(`stretch_mujoco_driver`)도 동일 인터페이스를 사용하므로 실기 절차를 그대로 적용할 수 있습니다.

---

## 1. 개요

- **백엔드**: `StretchDriverBackend`(`manipulation_backend: "stretch"`). MoveIt `move_group` 없이 Stretch 자체 driver로 직접 제어.
- **제어 인터페이스**(실기/시뮬 공통):
  - Action: `/stretch_controller/follow_joint_trajectory` (`control_msgs/FollowJointTrajectory`, position 모드 단일 point)
  - Service: `/switch_to_position_mode`, `/home_the_robot`, `/stow_the_robot` (모두 `std_srvs/Trigger`)
- **EE 프레임**: `link_grasp_center`, base_frame `base_link`
- **2단계 IK**: position은 정밀(수치 Newton IK, 오차 < 0.01), orientation은 근사 매핑(스케일/오프셋 상수 `_WRIST_*`로 분리 → 본 문서 6절에서 보정).

---

## 2. 사전 준비

### 2.1 실기 하드웨어 / 네트워크

- Stretch 3 전원 ON, 무선 네트워크 연결(`hello-XXXX` 또는 설정된 SSID)
- 로봇 PC의 ROS 2 도메인이 robo_claw 실행 PC와 일치 (`export ROS_DOMAIN_ID=<동일값>`)
- 로봇 PC에서 `stretch_driver`가 기동 중이어야 한다(아래 3절).
- 충분한 작업 공간 확보, 팔이 휴지/물체에 부딪히지 않도록 주의.

### 2.2 의존 (robo_claw 실행 PC)

```bash
# ROS 2 Jazzy + 파이썬 의존
sudo apt install ros-jazzy-control-msgs ros-jazzy-trajectory-msgs \
                 ros-jazzy-std-srvs python3-numpy
# stretch_ros2 (Hello Robot) — 실기 driver 제공
# 설치 가이드: https://github.com/hello-robot/stretch_ros2
```

### 2.3 robo_claw 빌드

```bash
cd ~/robo_claw_ws   # robo_claw 소스가 있는 colcon 워크스페이스
colcon build --packages-select robo_claw_msgs robo_claw_agent \
                       robo_claw_bringup
source install/setup.bash
```

---

## 3. Stretch driver 기동

### 3.1 실기

로봇 PC(또는 동일 네트워크 PC)에서:

```bash
# position 모드로 driver 기동
ros2 launch stretch_core stretch_driver.launch.py mode:=position
```

확인:

```bash
ros2 action list | grep follow_joint_trajectory   # /stretch_controller/follow_joint_trajectory
ros2 service list | grep -E "switch_to_position|home_the_robot|stow_the_robot"
ros2 topic echo /stretch/joint_states --once       # 현재 joint 상태
```

### 3.2 시뮬(MuJoCo) — 실기 절차를 먼저 시뮬에서 점검 권장

```bash
ros2 launch stretch_simulation stretch_mujoco_driver.launch.py mode:=position
```

> 시뮬은 이전 goal abort 미지원, 0.2s 내 재접수 거부 → robo_claw는 직렬 동기 대기로 처리. 실기와 동일 명령 사용.

---

## 4. robo_claw 실행

```bash
# manipulator_node(MoveIt용 C++) 비활성, stretch 백엔드 활성
ros2 launch robo_claw_bringup robo_claw.launch.py \
    robot_config:=stretch3 run_manipulator:=false
```

- `stretch3_config.yaml`의 `manipulation_backend: "stretch"`가 적용됩니다.
- `robot_description_file`(SE3 URDF)가 robot_state_publisher로 TF 발행 → 2단계 pose 검증에 사용.
- 초기화 시 `/switch_to_position_mode` 호출(실패해도 경고 로그만, 치명 아님).

기동 확인:

```bash
ros2 node list | grep robo_claw
ros2 service list | grep execute_skill
```

---

## 5. 검증 — 1단계 joint 기반 제어

`ExecuteSkill` 서비스(또는 HTTP `/skill`)로 순차 검증. 매 단계 `ros2 topic echo /stretch/joint_states`로 도달값 확인.

| 순서 | 스킬            | 인자 예시                               | 기대                                        |
| :--- | :-------------- | :-------------------------------------- | :------------------------------------------ |
| 1    | `open_gripper`  | preset=open                             | `gripper_aperture` → 0.09                   |
| 2    | `close_gripper` | preset=close                            | `gripper_aperture` → 0.0                    |
| 3    | `move_joints`   | `{joint_lift:0.5, wrist_extension:0.2}` | 두 joint 이동                               |
| 4    | `arm_pose`      | named=home                              | `/home_the_robot` 서비스 호출, 자세 복귀    |
| 5    | `arm_pose`      | named=stow                              | `/stow_the_robot` 서비스 호출               |
| 6    | `arm_pose`      | named=ready                             | config `named_pose_joint_values.ready` 적용 |

joint 단위는 raw(m / rad). deg 혼용 금지.

---

## 6. 검증 — 2단계 pose 기반 제어 + orientation 보정

### 6.1 position IK 검증

`move_pose`로 EE 위치 지정 → `link_grasp_center` TF로 확인.

```bash
# 터미널 분리 — TF 모니터링
ros2 run tf2_ros tf2_echo base_link link_grasp_center
```

```bash
# move_pose 스킬 호출 (예: x=0.10, y=-0.50, z=0.45)
# target_pose: {position:{x:0.10,y:-0.50,z:0.45}, orientation:{x:0,y:0,z:0,w:1}}
```

- TF echo의 position과 목표 position 차이가 < 1~2cm이면 position IK 정상.
- 차이가 크면 `wrist_extension`/`joint_lift` 가동 범위 밖(도달 불가 → `ManipulationError`). 목표 위치를 arm 가동 범위 안으로 조정.

### 6.2 orientation 근사 매핑 보정 (필수)

현재 `stretch_backend.py`의 `_WRIST_PITCH_SCALE/OFFSET`, `_WRIST_ROLL_SCALE/OFFSET`은 **초기값 1.0/0.0(근사)**입니다. 실기/시뮬에서 TF로 측정해 보정합니다.

절차:

1. `move_pose`로 알려진 orientation 목표 전송(예: pitch만 `quaternion_from_rpy(0, 0.5, 0)`).
2. `tf2_echo`의 출력 quaternion → RPY 역산:
   ```bash
   python3 -c "from scipy.spatial.transform import Rotation as R;\
   import numpy as np; print(R.from_quat([x,y,z,w]).as_euler('xyz'))"
   # 또는 robo_claw 헬퍼:
   python3 -c "import sys; sys.path.insert(0,'src/robo_claw_agent'); \
   from robo_claw_agent.manipulation_runtime.math import rpy_from_quaternion; \
   print(rpy_from_quaternion({'x':..,'y':..,'z':..,'w':..}))"
   ```
3. 측정 pitch / 목표 pitch = 실제 스케일. 부호가 반대면 `_WRIST_PITCH_SCALE` 부호 반전.
4. `stretch_backend.py` 상수 갱신:
   ```python
   _WRIST_PITCH_SCALE = <측정 스케일>   # 예: -1.05
   _WRIST_PITCH_OFFSET = <잔여 offset>  # 0에 가까우면 0.0
   _WRIST_ROLL_SCALE = <측정 스케일>
   _WRIST_ROLL_OFFSET = <잔여 offset>
   ```
5. pitch(±0.5), roll(±0.5) 각각 3~5점 측정 후 최소제곱으로 스케일/오프셋 산출. 재빌드 후 재검증.
6. position yaw는 position IK가 결정하므로 target orientation의 yaw는 무시됨(EE yaw는 위치에 종속). yaw 제어가 필요하면 joint 기반(`move_joints`의 `joint_wrist_yaw`)을 병용.

> 보정이 끝나기 전까지는 orientation이 어긋날 수 있습니다. 임무(파지 등)에서 orientation 정밀도가 중요하면 보정 완료 후 사용.

---

## 7. grasp / place 시퀀스

orientation 보정 완료 후 통합 검증.

```bash
# grasp: (필요 시 베이스 회전) → approach → move_pose(물체 위) → (그리퍼 카메라 최종 확인) → close_gripper → retreat
# place: approach → move_pose(목표) → (target_object 지정 시 그리퍼 카메라 확인) → open_gripper → retreat
```

- 접근(approach)/후퇴(retreat) 거리는 스킬 인자 또는 config에서 조정.
- 시뮬에서 먼저 전 시퀀스 돌린 뒤 실기 적용 권장.
- `tf2_echo`로 `link_grasp_center`가 물체/목표 위치에 근접하는지 확인 후 그리퍼 동작.
- `grasp(object_name=...)`가 IK로 도달 불가능하면 자동으로 베이스를 회전한 뒤 재시도한다(`base_rotation_applied_deg` 결과 필드로 확인). 회전 전 `wrist_extension`이 뻗어 있으면 안전을 위해 회전을 거부하므로, 먼저 `stow_for_navigation`/`prepare_right_side_pick`으로 접었는지 확인할 것.
- `grasp`/`place`는 기본적으로 그리퍼 카메라로 최종 확인 후 close/open을 실행한다(`require_gripper_confirmation=False`로 끌 수 있음). `gripper_camera_confirmed` 결과 필드로 시각 확인이 실제로 수행됐는지 확인할 것.

### 7.1 파지 성공 검증(`grasp_verified`) — 실측 전 임시값 주의

`grasp`, `pick_from_right_side_zone`, `vla_pick_front_object`, `vla_pick_gripper_object`는 close 직후 `/joint_states`(또는 `/stretch/joint_states`)의 `gripper_aperture` 실제값과 명령값의 차이(gap)로 파지 성공 여부(`grasp_verified`)를 판정하고, 실패 시 자동 재시도한다(`retry_on_empty_grasp`, `max_grasp_retries`).

**중요**: 이 판정에 쓰이는 `_EMPTY_CLOSE_GAP_M`(기본 0.01m, `sequence.py`)은 실기/시뮬 확인 없이 정한 **임시값**입니다. 실기/MuJoCo 접근이 가능해지면 다음을 먼저 확인할 것:

- `/joint_states`에 실제로 `gripper_aperture` 조인트명이 그대로 나오는지, 혹은 다른 이름(`joint_gripper_finger_left/right`, `stretch_gripper` 등)으로 나오는지.
- `effort` 필드가 유의미하게 채워지는지(채워진다면 position deadband보다 신뢰도 높은 힘 기반 판정 도입 검토).
- 위 확인 결과에 따라 `_EMPTY_CLOSE_GAP_M`과 조인트명 가정을 재조정.

### 7.2 컵 색상 폴백(`cup_fallback_*`) — 현장별 재조정 필요

그리퍼 카메라의 YOLO가 컵을 놓치거나(예: ArUco 마커를 다른 클래스로 오인식) 대상을 못 찾으면, `_observe_gripper_target`/`servo_gripper_to_object`는 `gripper_vision._fallback_cup_center_from_gripper_image`로 색상/형상 기반 컵 중심 추정으로 폴백합니다.

**중요**: 기본 HSV 범위·ROI·면적/종횡비 값은 개발 현장에서 쓰인 **연한 녹색 컵 기준**입니다. 다른 색 컵/현장에서는 아래 파라미터로 반드시 재조정할 것 (미지정 시 기존 하드코딩 값 그대로 동작):

- `cup_fallback_hsv_lower`, `cup_fallback_hsv_upper` — HSV 하한/상한 (기본 `[35,18,70]` / `[95,220,255]`, OpenCV HSV 스케일)
- `cup_fallback_roi_x_range`, `cup_fallback_roi_y_range` — 이미지 폭/높이 대비 탐색 영역 비율 (기본 `[0.18,0.82]` / `[0.2,0.85]`)
- `cup_fallback_area_range`, `cup_fallback_aspect_range` — 후보 블롭의 픽셀 면적/가로세로비 필터 (기본 `[350,20000]` / `[0.35,1.8]`)

실기에서 다른 색 컵으로 테스트할 때는 `cv2.cvtColor(..., COLOR_BGR2HSV)`로 실제 컵 색의 H/S/V 범위를 먼저 샘플링한 뒤 위 파라미터로 전달할 것.

### 7.3 `adaptive_pick_object`의 `max_attempts` — 전체 재시도(백트랙), 기본은 끔

`adaptive_pick_object`는 탐색→정렬→준비→그리퍼 확인→파지 중 어느 단계에서 실패하든 **기본값(`max_attempts=1`)에서는 기존과 동일하게 즉시 실패를 반환**합니다. `max_attempts`를 2 이상으로 명시하면, 실패 시점과 무관하게 처음(탐색)부터 전체를 다시 시도합니다 — 실패 원인이 1회성 스냅샷 위치 추정 오류일 수 있는 경우(예: 파지 순간 물체가 살짝 움직였거나 depth 노이즈로 헛짚음)를 감안한 백트랙입니다.

**주의**: 이 옵션은 실패 시 로봇이 자동으로 탐색 스윕(헤드 pan/tilt + 몸통 회전)과 팔 동작을 처음부터 반복한다는 뜻입니다. 기본값을 그대로 두면 아무 영향이 없지만, `max_attempts`를 올려 사용할 때는:

- 실기에서 먼저 `max_attempts=2`로 안전한 공간에서 검증할 것 (반복 동작 중 충돌 가능성 확인).
- 결과의 `steps`에서 `skill: "retry_pending"` 항목으로 몇 번째 시도에서 왜 실패해 재시도했는지 확인 가능.
- 최종 실패 시 `attempts` 필드로 총 시도 횟수를 확인할 것.

---

## 8. 안전 주의사항

- **position 모드 필수**: 초기화 시 자동 전환되지만, 로봇을 수동 조작 후엔 재전환 필요(`ros2 service call /switch_to_position_mode std_srvs/srv/Trigger`).
- **home/stow 우선**: 테스트 시작/종료 시 `arm_pose named=stow`로 안전 자세 복귀. 비상 시 `/stop_the_robot` 서비스 호출.
- **단계별 낮은 속도**: 처음엔 작은 joint 값(lift 0.3, ext 0.1)으로 검증 후 확대.
- **충돌 감시**: 팔 궤적 주변 물체 제거. 시뮬에서 궤적 사전 확인.
- **배타 joint 규칙**: 한 goal에 `wrist_extension`+`joint_arm*` 동시 불가, gripper 3종 중 1개만 — 위반 시 백엔드가 `ManipulationError`로 사전 차단.

---

## 9. 문제 해결

| 증상                                    | 원인 / 조치                                                                       |
| :-------------------------------------- | :-------------------------------------------------------------------------------- |
| `action 서버 ... 활성화되지 않았습니다` | stretch_driver 미기동. 3절 확인, `mode:=position`                                 |
| `도달 불가능한 pose 목표`               | target이 arm 가동 범위 밖. x/y/z 조정(lift 0~~1.1m, ext 0~~0.52m)                 |
| position TF 오차 큼                     | wrist_extension 한계 초과 또는 수치 IK 미수렴 → 목표를 가동 범위 안쪽으로         |
| orientation 어긋남                      | 6.2절 orientation 보정 미수행. `_WRIST_*` 상수 조정                               |
| home/stow 서비스 실패                   | position 모드 아님 → `/switch_to_position_mode` 후 재시도                         |
| goal 거부(0.2s 내 재접수)               | 시뮬 제약. 직렬 호출 간격 확보(스킬은 동기 대기로 이미 처리)                      |
| `frame_id ... 미지원`                   | pose의 `frame_id`가 `base_link` 아님. TF 변환은 미구현 → base_link 기준 pose 사용 |

---

## 10. 검증 체크리스트

- [ ] stretch_driver position 모드 기동, action/service 확인
- [ ] robo_claw `robot_config:=stretch3 run_manipulator:=false` 기동
- [ ] 1단계: open/close gripper, move_joints, home/stow/ready 동작
- [ ] 2단계: move_pose position TF 오차 < 1~2cm (3점 이상)
- [ ] 2단계: orientation `_WRIST_*` 상수 보정(pitch/roll 각 3~5점)
- [ ] 통합: grasp / place 시퀀스 정상
- [ ] 회전 자동 정렬: 정면 물체에 `grasp(object_name=...)` 또는 `adaptive_pick_object` 호출 시 베이스가 자동 회전 후 파지 성공(`base_rotation_applied_deg`/`reachability` 필드 확인)
- [ ] 그리퍼 카메라 확인: `grasp`/`place` 결과의 `gripper_camera_confirmed`가 기대대로 true/false인지 확인
- [ ] 파지 검증: `/joint_states`의 `gripper_aperture` 조인트명·effort 유무 확인 후 `_EMPTY_CLOSE_GAP_M` 실측 보정(7.1절)
- [ ] 컵이 아닌 다른 색이거나 다른 현장이면: `cup_fallback_*` 파라미터로 HSV/ROI/면적 재조정(7.2절)
- [ ] 전체 재시도 사용 전: 안전한 공간에서 `adaptive_pick_object(max_attempts=2)`로 반복 동작 시 충돌 없는지 확인(7.3절)
- [ ] 종료: `arm_pose named=stow` 안전 자세 복귀
