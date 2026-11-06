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

#### 그리퍼 및 팔 관절 제어 특성
- **그리퍼 단독 제어**: `agx_arm_ctrl` 드라이버는 `move_j` 메시지에 팔 관절이 포함되어 있으면 팔 동작 모터를 구동합니다. 따라서 그리퍼만 움직일 때는 `name: ['gripper']`, `position: [width]` 만 발행하여 팔 관절 모터 간섭 없이 빠르고 안전하게 동작합니다.
- **팔 관절 제어**: 드라이버의 생략 관절 0.0 보정(리셋) 버그를 방지하기 위해, 지정되지 않은 관절은 최신 수신된 피드백(`_latest_joint_state`) 값으로 자동 보존하여 발행합니다.
- **물리 검증 완료**: 실기 그리퍼 열기(`open_gripper`, 0.05m 도달) 및 닫기(`close_gripper`, 0.00038m 도달) 물리적 동작이 검증되었습니다.

### 3. 그리퍼 도달 피드백 타임아웃 진단

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
