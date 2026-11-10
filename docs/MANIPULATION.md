# MoveIt 기반 Manipulation 설정

manipulation 스킬은 MoveIt 설정이 준비된 로봇에서 공통 인터페이스로 동작합니다.
현재 저장소에는 MoveIt bringup 패키지가 포함되어 있지 않으므로, 실제 하드웨어/시뮬레이터 연동 시에는 별도 `moveit_config` 패키지와 환경 source가 필요합니다.

`src/robo_claw_bringup/config/agent.yaml`

```yaml
robo_claw_agent_node:
  ros__parameters:
    manipulation_arm_group: "arm"
    manipulation_gripper_group: "gripper"
    manipulation_end_effector_link: "tool0"
    manipulation_base_frame: "base_link"
    manipulation_named_poses_json: '{"home":"home","inspect":"inspect"}'
    manipulation_gripper_presets_json: '{"open":{"finger_joint":0.04},"close":{"finger_joint":0.0}}'
```

핵심 포인트:

- `arm_pose`: 설정된 named pose 실행
- `move_joints`: 조인트 맵 직접 실행
- `move_pose`: end-effector 목표 pose 실행
- `grasp`, `place`: 설정 기반 접근 → 실행 → 후퇴 시퀀스
- MoveIt 미설치 환경에서는 import 시점이 아니라 실행 시점에 명확히 실패합니다

## 실제 로봇 쪽 MoveIt 설정 조건

현재 구현이 실제 로봇과 연동되려면, 로봇 쪽에는 최소한 아래 구성이 준비되어 있어야 합니다.

1. **MoveIt config 패키지**
   - `robot_description`
   - `robot_description_semantic`(SRDF)
   - `kinematics.yaml`
   - planning pipeline 설정(`ompl`, 필요 시 `pilz` 등)
   - MoveIt controller 설정
2. **planning group / end-effector 정의**
   - `manipulation_arm_group` 에 대응하는 arm group
   - `manipulation_gripper_group` 에 대응하는 gripper group
   - `manipulation_end_effector_link` 또는 `manipulation_tool_frame` 에 대응하는 tool link
3. **named pose**
   - `arm_pose` 에서 쓸 pose 이름이 SRDF 또는 로봇 MoveIt 설정에 존재해야 함
   - 예: `home`, `ready`, `stow`, `inspect`
4. **gripper 조인트 preset**
   - `manipulation_gripper_presets_json` 에 넣는 joint 이름이 실제 gripper joint 와 일치해야 함
   - 예: `{"open":{"finger_joint":0.04},"close":{"finger_joint":0.0}}`
5. **MoveIt 실행 런타임**
   - `move_group` 또는 이에 준하는 MoveIt 런타임이 올라와 있어야 함
   - `joint_states`, TF, robot state publisher 가 정상적으로 연결되어 있어야 현재 상태 기반 planning 이 가능함
6. **좌표계 일관성**
   - `manipulation_base_frame` 과 pose 입력의 `frame_id` 가 실제 TF 트리와 맞아야 함
   - 현재 `grasp` / `place` 는 기본적으로 접근/후퇴를 같은 좌표계 기준 직선 오프셋으로 계산함

## 권장 연결 방식

- 로봇별 `*_moveit_config` 패키지에서 MoveIt bringup 실행
- RoboClaw agent 는 같은 ROS graph 에 붙어서 manipulation 파라미터만 맞춤 설정
- 처음에는 `arm_pose`, `move_joints`, `open_gripper`, `close_gripper` 부터 검증
- 이후 `move_pose`, 마지막으로 `grasp` / `place` 순으로 확장

## AgileX Nero 팔(agx_arm_ros) 연동

Former0045 에 AgileX Nero(7-DOF) 팔과 AgileX 그리퍼(`agx_gripper`)가 장착되어 있고, 팔 제어 PC 는 `192.168.50.202`
(`nero-arm`, Ubuntu 24.04)입니다. 이 조합은 `former_arm` 프로필로 기동합니다.

```bash
# RoboClaw 컨테이너(ROS 2 humble, ROS_DOMAIN_ID=4, rmw_cyclonedds_cpp) 기준
./rclaw run robot_config:=former_arm
```

| 파일 | 내용 |
| :--- | :--- |
| `src/robo_claw_bringup/config/former_arm_config.yaml` | 로봇·토픽 매핑 + `manipulation_*` 파라미터 (그리퍼 프리셋 open: 0.05, close: 0.0) |
| `src/robo_claw_bringup/config/ROBOT_LIMITS.former_arm.json` | `joint1`..`joint7` raw(rad) 하드 한계 및 `gripper` [0.0, 0.1] 한계 |
| `src/robo_claw_bringup/config/SKILLS.former_arm.md` | 팔 스킬 사용 규칙(이동 전 `home`, 그리퍼 장착 사양 반영) |

### 1. 팔 제어 PC (nero-arm) 드라이버 기동 가이드

팔 PC(Ubuntu 24.04)는 호스트에 직접 설치하지 않고 **ROS 2 Humble 기반 도커 컨테이너(`nero_humble:1.0`)** 로 팔 드라이버를 기동하여 RoboClaw 에이전트와 완벽히 동일한 ROS 환경(`humble`, `ROS_DOMAIN_ID=4`)을 유지합니다.

#### 1) CAN 버스 활성화
`can0`(1 Mbit/s)는 nero-arm 의 systemd 유닛 `can0-activate.service`(`~/can0-activate.service`,
설치는 `~/install-can0-activate.sh`)가 부팅 시 자동으로 올립니다. 상태만 확인합니다.

```bash
# nero-arm 호스트에서 실행
systemctl status can0-activate.service --no-pager
ip -details -statistics link show can0   # can state ERROR-ACTIVE, bitrate 1000000 이어야 함
```

#### 2) 드라이버 도커 컨테이너 기동
컨테이너 실행 래퍼는 nero-arm 의 `~/humble_docker/`(`run.sh`, `start_arm.sh`, `Dockerfile`)에 있습니다.
`run.sh` 는 `--net=host --privileged`로 실행하며 다음을 마운트합니다.

| 호스트 | 컨테이너 | 용도 |
| :--- | :--- | :--- |
| `~/agx_arm_ws/src` | `/ws/src` | `agx_arm_ros` 소스(호스트와 공유) |
| `~/agx_arm_ws_humble/{build,install,log}` | `/ws/{build,install,log}` | Humble 전용 빌드 결과 |
| `~/cyclonedds_wired.xml` | `/root/cyclonedds_wired.xml` (읽기 전용) | 유선 NIC(`enp86s0`) 고정 DDS 설정 |

```bash
# nero-arm 호스트에서 실행
~/humble_docker/start_arm.sh agx_gripper                                      # 모터 자동 활성화 (기본값)
~/humble_docker/start_arm.sh agx_gripper auto_enable:=false control_enabled:=false  # 읽기 전용 (모터 꺼짐, 명령 무시)
```

현재 운영 중인 컨테이너 `nero_arm_driver`(`--restart unless-stopped`)는 다음 인자로 `ros2 launch`를 실행합니다.

```text
ros2 launch agx_arm_ctrl start_single_agx_arm.launch.py can_port:=can0 arm_type:=nero \
  effector_type:=agx_gripper speed_percent:=20 fw_version:=v121
```

- `speed_percent:=20`: 안전을 위해 최고 속도의 20%로 제한합니다.
- `effector_type:=agx_gripper`: 장착된 그리퍼를 활성화합니다.
- `fw_version:=v121`: 펌웨어 자동 감지를 건너뜁니다.
- `auto_enable`, `control_enabled`는 기본값 `true`입니다. 컨테이너가 재시작될 때마다 모터를 활성화하고
  `/control/*` 게이트를 엽니다. 같은 DDS 도메인의 어떤 노드든 `/control/*`로 팔을 움직일 수 있으므로,
  실기기 검증 전에는 읽기 전용 모드로 기동하고 `agx_backend`가 `/enable_agx_arm`, `/control_enable`을 호출하게 합니다.

#### 3) 통신 환경 주의
- 팔 PC 호스트의 손목 카메라(`wrist-camera.service`)는 ROS 2 Jazzy 이고 드라이버는 Humble 입니다. 같은 도메인(4)에
  배포판이 섞이면 `ros2 node list`가 `empty node name returned by the RMW layer`로 실패할 수 있습니다.
  토픽 통신은 유지되므로 점검은 `ros2 topic info <topic> --no-daemon`, `ros2 service list --no-daemon`을 사용합니다.
- `ros2 daemon`은 호스트(Jazzy)와 컨테이너(Humble)가 공유하면 타입 해시 오류가 나므로 컨테이너 안에서는 `--no-daemon`을 붙입니다.
- 이 설정은 `former/autostart/cyclonedds.xml`의 배포판 혼재 경고와 같은 원인입니다.

### 2. 제어 인터페이스 계약 (`agx_backend`)

RoboClaw는 MoveIt을 거치지 않고 드라이버가 노출하는 표준 ROS 2 토픽/서비스를 직접 제어하는 `agx` 백엔드를 사용합니다(`agx_backend.py`).

| 구분 | 이름 / 토픽 | 메시지 / 설명 |
| :--- | :--- | :--- |
| 관절 및 그리퍼 제어 | `/control/move_j` | `sensor_msgs/msg/JointState` |
| 포즈 제어 | `/control/move_p` | `geometry_msgs/msg/PoseStamped` |
| 직선 제어 | `/control/move_l` | `geometry_msgs/msg/PoseStamped` |
| 관절/그리퍼 피드백 | `/feedback/joint_states` | `sensor_msgs/msg/JointState` (`joint1`..`joint7`, `gripper`) |
| 제어 게이트 활성화 | `/control_enable` | `std_srvs/srv/SetBool` (데이터 `true`) |
| 모터 활성화 | `/enable_agx_arm` | `std_srvs/srv/SetBool` (데이터 `true`) |
| 홈 위치 이동 | `/move_home` | `std_srvs/srv/Empty` |
| 팔 상태 진단 | `/feedback/arm_status` | `diagnostic_msgs/msg/DiagnosticArray` |
| 모터별 진단 | `/feedback/motor_status` | `diagnostic_msgs/msg/DiagnosticArray` |

#### 그리퍼 및 팔 관절 제어 특성
- **그리퍼 단독 제어**: `agx_arm_ctrl` 드라이버는 `move_j` 메시지에 팔 관절이 포함되어 있으면 팔 동작 모터를 구동합니다. 따라서 그리퍼만 움직일 때는 `name: ['gripper']`, `position: [width]` 만 발행하여 팔 관절 모터 간섭 없이 빠르고 안전하게 동작합니다.
- **팔 관절 제어**: 드라이버가 생략 관절을 0.0으로 채우므로 지정하지 않은 관절은 최신 피드백 값으로 보존합니다. `ROBOT_LIMITS`가 제공되면 요청한 관절뿐 아니라 보존 관절을 포함한 **실제 전체 명령**을 발행 전에 검증합니다. 현재 피드백이 한계 밖인 관절을 그대로 보존해야 한다면 실행을 거부하며, 임의 클램프나 영점 변경으로 보정하지 않습니다.
- **물리 검증 완료**: 실기 그리퍼 열기(`open_gripper`, 0.05m 도달) 및 닫기(`close_gripper`, 0.00038m 도달) 물리적 동작이 검증되었습니다.

#### 결과 검증과 실패 보고 (`agx_backend`)
드라이버 서비스 응답만으로는 실제 동작 여부를 알 수 없으므로 다음을 코드에서 강제합니다.

| 항목 | 동작 |
| :--- | :--- |
| `/move_home` 결과 | 서비스 호출 후 `/feedback/joint_states` 가 드라이버 home 자세(모든 팔 조인트 0 rad)에 수렴하는지 확인하고, 수렴하지 않으면 `ManipulationError` 로 실패 보고합니다. |
| 이미 그 자세 | 명령 시점에 이미 목표 자세였으면 `already_at_target=true` 로 결과를 돌려주고, 스킬 메시지는 "이동 완료" 대신 "이미 그 자세입니다" 로 보고합니다. |
| 팔 오류 상태 | 진단 항목별로 `arm_status`와 `err_status`를 검사합니다. `NO_SOLUTION`(0x02)만 있고 다른 오류가 없는 경우 조인트 목표에서 경고로 처리합니다. 특이점(0x03), 목표 한계 초과(0x04), 비상정지, 통신·브레이크·충돌·과열 오류는 차단합니다. 다른 진단 항목의 오류를 `NO_SOLUTION`으로 덮어 무시하지 않습니다. |
| 미도달과 구동 오류 | 프로토콜 Byte4에서 `motion_status=0`은 도달, `1`은 아직 미도달입니다. SDK의 `FAILED`라는 이름만으로 정상 이동 중에 실패 처리하지 않습니다. 실제 조인트/TCP 수렴을 기다리고, 하드웨어 오류 또는 제한 시간 내 미수렴일 때 실패로 보고합니다. |
| 명령 발행 | 제어 토픽 구독자가 정확히 하나인지 확인한 후 목표당 한 번만 발행합니다. 대기 중 같은 목표를 주기적으로 재전송하지 않습니다. 구독자 미발견·중복 드라이버는 발행 전에 차단합니다. |
| 모터 및 피드백 | 수신된 모터 진단의 충돌·스톨·과전류·비활성 상태를 검사합니다. 조인트/TCP 피드백이 `manipulation_agx_feedback_timeout_sec`보다 오래되거나 비유한 값이면 목표 도달로 인정하지 않습니다. 모터 진단 미수신 상태에서의 호환 동작은 안전성 검증 근거로 삼지 않습니다. |
| 위치만 지정한 pose 목표 | `orientation`·`roll/pitch/yaw`·쿼터니언을 주지 않으면 identity 자세가 아니라 **현재 end-effector 자세를 유지**해 발행합니다. identity 자세로 고정하면 IK 불가(`NO_SOLUTION`)로 목표에 도달하지 못하고 팔이 최대 신장에서 멈춥니다. |

> **주의 — 드라이버 home 은 "접은 자세" 가 아닙니다.** `agx_arm_ctrl` 의 `/move_home` 은 `move_j([0]*N)` 이며 Nero 기준 모든 조인트가 0 rad 인 **수직으로 뻗은 자세** 입니다. 접은 자세나 주행 전 안전 자세가 필요하면 `manipulation_named_poses_json` 에 `stow`/`fold` 같은 이름으로 조인트 맵을 정의해 `arm_pose` 로 호출해야 합니다. 정의 없이 `home` 으로 접기를 시도하면 "이미 그 자세" 로 끝나고 팔은 움직이지 않습니다.

### 3. Nero 팔 조인트 한계와 동작 프리셋

표는 `pyAgxArm` SDK의 `ROBOT_JOINT_LIMIT_PRESET_RAD["nero"]` 기준이며 `ROBOT_LIMITS.former_arm.json`에 저장합니다. URDF와 SDK 사이에는 일부 반올림 차이가 있습니다. 2026-11-10 컨트롤러 조회에서는 joint6 상한이 55°로 SDK의 50°와 달랐습니다. 설정을 확대하지 않고 더 좁은 SDK 범위를 유지합니다. 각도 범위 안이라는 사실만으로 몸체와의 충돌 안전성이 보장되지는 않습니다.

| 조인트 | 하한 (rad) | 상한 (rad) | 하한 (deg) | 상한 (deg) |
| :--- | ---: | ---: | ---: | ---: |
| joint1 | -2.705261 | 2.705261 | -155.0 | 155.0 |
| joint2 | -1.745330 | 1.745330 | -100.0 | 100.0 |
| joint3 | -2.757621 | 2.757621 | -158.0 | 158.0 |
| joint4 | -1.012291 | 2.146755 | -58.0 | 123.0 |
| joint5 | -2.757621 | 2.757621 | -158.0 | 158.0 |
| joint6 | -0.733039 | 0.872665 | -42.0 | 50.0 |
| joint7 | -1.570797 | 1.570797 | -90.0 | 90.0 |

동작 프리셋은 `former_arm_config.yaml` 의 `manipulation_named_poses_json` 에 있고, `arm_pose(pose_name=...)` 로 호출합니다. 아래 TCP 값은 SDK MDH 순기구학(`pyAgxArm.utiles.mdh_kinematics`) 계산값입니다(arm base frame, 미터). 2026-11-10 실기에서 순기구학이 `/feedback/tcp_pose` 와 1 mm 이내로 일치함을 확인했지만, **아래 표의 프리셋 조합 자체는 좌표계 수정 후 실기 재검증 전입니다.**

| 프리셋 | 용도 | joint1 | joint2 | joint3 | joint4 | joint5 | joint6 | joint7 | TCP (x, y, z) |
| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :--- |
| `home` | 수직 zero (드라이버 `/move_home`) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.000, -0.024, 0.718 |
| `ready` | 앞 30 cm / 높이 50 cm, 팔꿈치 접힘 | 0 | +0.116 | 0 | +1.252 | 0 | 0 | 0 | -0.300, -0.023, 0.500 |
| `carry` | `ready` 별칭 | 0 | +0.116 | 0 | +1.252 | 0 | 0 | 0 | -0.300, -0.023, 0.500 |
| `stow` / `fold` | 앞 27 cm / 높이 41 cm 접힘(보관) | 0 | 0.000 | 0 | +1.726 | 0 | 0 | 0 | -0.267, -0.023, 0.406 |
| `table` | 앞 45 cm / 높이 26 cm | 0 | +0.721 | 0 | +1.273 | 0 | 0 | 0 | -0.451, -0.023, 0.260 |
| `reach` | 앞 55 cm / 높이 30 cm | 0 | +1.431 | 0 | -0.317 | 0 | 0 | 0 | -0.549, -0.024, 0.300 |
| `side_left` | base 요크 시험(+) | +0.6 | +0.116 | 0 | +1.252 | 0 | 0 | 0 | -0.235, -0.189, 0.500 |
| `side_right` | base 요크 시험(-) | -0.6 | +0.116 | 0 | +1.252 | 0 | 0 | 0 | -0.261, +0.150, 0.500 |
| `wrist_down` | joint6 하한(-0.733) | 0 | +0.116 | 0 | +1.252 | 0 | -0.733 | 0 | -0.285, -0.017, 0.497 |
| `wrist_up` | joint6 상한 근처(+0.87) | 0 | +0.116 | 0 | +1.252 | 0 | +0.87 | 0 | -0.318, -0.015, 0.504 |
| `roll_ccw` | joint7 +1.55 | 0 | +0.116 | 0 | +1.252 | 0 | 0 | +1.55 | -0.300, -0.023, 0.500 |
| `roll_cw` | joint7 -1.55 | 0 | +0.116 | 0 | +1.252 | 0 | 0 | -1.55 | -0.300, -0.023, 0.500 |

> **장착 좌표계와 안전성은 미확정입니다.** 사진과 피드백에서 뒤쪽 접힘을 확인했지만, 이를 근거로 모든 자세의 앞뒤 방향을 `joint2` 부호 하나로 단정하지 않습니다. 위 프리셋의 이름과 TCP 계산값은 충돌 검증이나 보관 자세 승인 결과가 아닙니다. 자동 접기·주행 전 적용을 보류하고 장착된 로봇에서 전체 이동 경로를 검증합니다.

좌표계 및 점검 기준:

- 팔 드라이버 좌표는 `nero_arm_base_link`, 로봇 섀시는 `base_link`로 구분합니다. `base_link`/`map` 좌표를 사용할 때는 `frame_id`를 명시하고 실측 장착 TF로 변환합니다. 변환이 없으면 실행을 거부하며 두 프레임을 같은 이름으로 합치거나 변환값을 추측하지 않습니다.
- 사진 기준 체인은 어깨 → `NERO` 글자가 있는 상완 → 팔꿈치 → 전완 → 손목 → 그리퍼입니다. 단일 TCP 높이나 `>`/`Γ` 모양만으로 안전성을 판단하지 않습니다.
- `arm_status=NORMAL`, 모터 `enabled`라도 구동 성공은 아닙니다. 2026-11-10 작은 팔 목표는 실제로 미수렴 상태였으나 `motion_status=1` 자체는 미도달 표시이며 영구 실패나 래치를 뜻하지 않습니다. 별도 그리퍼 단독 개방은 3 mm 목표의 0.5 mm 허용 오차 안에 도달하고 팔 관절 변화가 없음을 확인했습니다. 따라서 공통 ROS/CAN 경로 전체의 장애로 단정하지 않고 팔 궤적 제어·시작 자세·컨트롤러 설정을 따로 조사합니다. 재부팅·보호 해제·큰 각도 이동은 반복하지 않습니다.
- 설치된 SDK는 기본 `local_loopback=False`이므로 별도 `candump`에 로컬 송신 프레임이 안 보일 수 있습니다. 송신 카운터와 오프라인 패킹을 함께 대조하며, 덤프에 없다는 이유만으로 명령 미전송을 단정하지 않습니다.
- `move_js`는 벤더 문서에서 궤적 계획·평활화가 없는 고위험 모드로 경고합니다. 구동 실패의 우회 수단으로 사용하지 않습니다. 영점·관절 한계·충돌 보호도 임의 변경하지 않습니다.


#### Nero PC의 SDK 문서와 예제

별도 공급업체 문서 없이도 PC 안의 다음 자료를 참조할 수 있습니다. 파일 열람과 예제 실행은 구분하며, 현재 자세에서 예제를 그대로 실행하지 않습니다.

| 호스트 경로 | 내용 |
| :--- | :--- |
| `/home/nero/pyAgxArm/docs/nero/nero_api.md` | 초기화, 구동 모드, 조인트 목표, 상태 및 한계 API |
| `/home/nero/pyAgxArm/docs/nero/firmware_reference.md` | 펌웨어별 드라이버 상속 및 기능 차이 |
| `/home/nero/pyAgxArm/pyAgxArm/demos/nero/test1.py` | SDK 설정 및 단일 목표·도달 대기 예시 |
| `/home/nero/agx_arm_ws/src/agx_arm_ros/test/cmd.md` | ROS 토픽의 단발 발행 예시 |
| `/home/nero/agx_arm_ws/src/agx_arm_ros/test/nero/test_move_j.yaml` | 전 관절 0 rad 목표 예시이며 안전한 접힘 자세가 아님 |
| `/home/nero/humble_docker/start_arm.sh` | Humble 컨테이너의 CAN 활성화 및 드라이버 기동 |

컨테이너의 SDK 문서는 `/opt/pyAgxArm/docs/nero/`에 있습니다. 공식 `move_j` 예시는 연결·활성화·속도 설정 후 목표를 한 번 보내고 `motion_status=0` 또는 타임아웃까지 대기합니다. 펌웨어 1.12 이상에서 `set_normal_mode()`는 효과가 없고 기본 모드는 follower입니다. 실제 모드를 확인하지 않고 leader/follower 전환을 복구 수단으로 사용하지 않습니다.

2026-11-10 코드 대조에서는 설치 SDK와 세 호스트 checkout이 같은 버전이었고, 실제 CAN 제어 모드와 MOVE_J 피드백도 확인했습니다. 부분 목표에 보존된 범위 밖 관절이 섞이는 검증 누락은 대역으로 재현했지만, 모든 목표를 정상 범위로 명시한 실기 시험도 미수렴했으므로 이것만을 전체 미구동의 원인으로 단정하지 않습니다. 확인한 PC 자료에서는 해당 시작 자세에서 안전하게 복귀하는 기구별 절차가 명시되어 있지 않습니다. `nero_api.md`의 `calibrate_joint()` 절에는 비활성화 → 수동 영점 정렬 → 교정 → 재활성화 예제가 있습니다. 이는 현재 접힌 자세를 임의의 영점으로 삼는 복구 명령이 아닙니다. `v111/driver.py`는 단일 관절과 전체 관절(255)의 오프셋 취급도 구분하므로, 기준 자세와 오프셋을 확인하지 않고 전체 교정을 실행하지 않습니다.

### 4. 그리퍼 도달 피드백 타임아웃 진단

목표에 실제 도달했는데 타임아웃을 반환하면 피드백 수신과 실행 스레드를 함께 확인합니다.
현재 `AgxDriverBackend`는 별도의 `ReentrantCallbackGroup`을 생성해 피드백 구독과
서비스 클라이언트에 적용합니다. 아래 증상만으로 현재 배포에 콜백 그룹이 없다고
판단하지 않습니다.

- **증상**: 
  - `curl -X POST http://localhost:8080/skill -d '{"skill_name": "open_gripper", "params": {}}'` 호출 시 물리 하드웨어는 정상 작동하여 `gripper: 0.0498m`에 도달함.
  - 그러나 에이전트 응답은 `30.0초 내에 목표에 도달하지 못했습니다` 타임아웃 반환.
- **원인 분석**:
  - 터미널에서 `ros2 topic echo /feedback/joint_states --once` 를 실행하면 최신 그리퍼 피드백(`0.0498`)이 즉시 수신되므로 네트워크/DDS 통신 문제는 아님.
  - `AgentNode` 내부에서 스킬 실행 스레드가 `_wait_until` 루프를 도는 동안, `_on_joint_state` 콜백이 최신 값을 갱신하지 못하고 오래된 캐시값(`0.000384`)에 갇혀 있음(콜백 starvation 현상).
- **해결 가이드**:
  1. `AgentNode`의 `MultiThreadedExecutor`에서 manipulation 백엔드의 콜백 그룹(`cb_group = ReentrantCallbackGroup()`)이 정상 스케줄링되는지 확인합니다.
  2. 현재 피드백 구독은 depth 10의 기본 QoS를 사용합니다. `ros2 topic info -v /feedback/joint_states`로 드라이버와 QoS 호환성을 확인한 뒤 필요할 때만 변경합니다.
  3. `_wait_until` 루프 내에서 불필요한 락이나 sleep 차단이 없는지 점검합니다.

## 현재 구현의 한계

- `grasp` / `place` 는 일반화된 자동 grasp synthesis 가 아니라 **설정 기반 접근-실행-후퇴 시퀀스**입니다.
- `object_name` 만으로 파지하려면 메모리에 해당 객체의 3D pose metadata 가 저장되어 있어야 합니다.
- 충돌 객체 attach/detach, scene update, grasp quality 판단은 아직 로봇별 확장 포인트로 남겨두었습니다.

## Stretch3 백엔드 및 VLA 스타일 폐루프 집기

`src/robo_claw_agent/robo_claw_agent/manipulation_runtime/`에는 Stretch3 전용 IK/드라이버 백엔드(`stretch_backend.py`, `stretch_kinematics.py`)가 있고, `skills/manipulation_skill/sequence/`에는 `vla_pick_skills.py`, `align_skill.py`, `grasp_place_skills.py`, `right_side_pick_skills.py` 등 카메라 depth 기반으로 대상에 정렬하며 폐루프로 접근하는 확장 시퀀스 스킬들이 있습니다. bbox+depth를 3D 좌표로 역투영하는 로직은 `skills/perception_skill/depth3d.py`에 있습니다. Stretch3 실기 테스트 절차는 [STRETCH3_TEST_GUIDE.md](STRETCH3_TEST_GUIDE.md), 헤드 카메라 회전 보정 이슈는 [HEAD_CAMERA_UPRIGHT_ROTATION.md](HEAD_CAMERA_UPRIGHT_ROTATION.md)를 참고하세요.

## 물체 탐지 개선 (오픈어휘 폴백, 탐색/필터 강화)

YOLO(COCO 80클래스)는 어휘에 없는 물체(예: **물통**, 볼펜 등)를 절대 검출하지 못합니다. 모델을 `yolov8s→m`으로 올려도 새 클래스는 추가되지 않으므로, COCO 어휘 밖 물체는 아래 오픈어휘 경로로 해결합니다.

- **VLM 오픈어휘 로컬라이즈** (`skills/perception_skill/vlm_localize.py`): `find_object`가 YOLO로 매칭되지 않고 대상이 COCO 어휘 밖일 때, 기존 LLM `analyze_image`로 대상의 정규화 bbox 중심을 얻어 depth/TF로 3D 좌표를 계산합니다. `head_vision._observe_head_target`(파지 전 관측)과 `find_object`(탐색)에 폴백으로 연결됩니다. 관련 파라미터: `use_vlm_fallback`(기본 True), `force_vlm_fallback`(COCO 대상이라도 강제).
- **한글→COCO 별칭 확장** (`perception_skill/core.py`): 책/시계/휴대폰/노트북/리모컨/화분/그릇/숟가락 등 다수 매핑 추가. 미매칭 대상은 `target_is_coco()==False`로 구분되어 VLM 경로로 라우팅됩니다.
- **C++ 필터 완화** (`robo_claw_vision`, `robo_claw.launch.py`): `min_bbox_area_ratio` 0.001→0.0002(원거리/소형 물체 보존), 종횡비 0.1~10.0, temporal 확인 2-of-3 → 2-of-5(탐색 중 모션 블러 강인).
- **능동 탐색 강화** (`search_skill.py`): pan 뷰당 `view_retries`(기본 2)회 재관측, 전체 스윕 실패 시 `approach_search_distance_m`(기본 0.5m)만큼 접근 후 재스윕(`max_approach_scans`).

이 변경들은 실제 로봇 동작 전에 simulation/mock backend에서 파지 경로가 정상 확인되어야 하며, 새 파라미터는 실기 요청 파라미터로 override할 수 있습니다.
