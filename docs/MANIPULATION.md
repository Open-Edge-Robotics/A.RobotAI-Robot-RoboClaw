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

Former0045 에 AgileX Nero(7-DOF) 팔이 장착되어 있고, 팔 제어 PC 는 `192.168.50.202`
(`nero-arm`, Ubuntu 24.04 / ROS 2 jazzy)입니다. 이 조합은 `former_arm` 프로필로 기동합니다.

```bash
# RoboClaw 컨테이너(ROS 2 humble, ROS_DOMAIN_ID=4, rmw_cyclonedds_cpp) 기준
./rclaw run --robot-config former_arm
```

| 파일 | 내용 |
| :--- | :--- |
| `src/robo_claw_bringup/config/former_arm_config.yaml` | 로봇·토픽 매핑 + `manipulation_*` 파라미터 |
| `src/robo_claw_bringup/config/ROBOT_LIMITS.former_arm.json` | `joint1`..`joint7` raw(rad) 하드 한계 |
| `src/robo_claw_bringup/config/SKILLS.former_arm.md` | 팔 스킬 사용 규칙(이동 전 `home`, 파지 미검증) |

팔 PC 는 다음 계약을 제공합니다(`agx_arm_ros`).

| 구분 | 값 |
| :--- | :--- |
| MoveIt 그룹 | `arm` (`joint1`..`joint7`) |
| SRDF group_state | `home` |
| 팔 끝 링크 | `link7` (플랜지 조립품은 `flange_link`) |
| 드라이버 제어 토픽 | `control/move_j`(JointState), `control/move_p`(PoseStamped), `control/move_js` |
| 드라이버 피드백 | `feedback/joint_states`(JointState), `feedback/tcp_pose`(PoseStamped) |
| 서비스 | `enable_agx_arm`, `control_enable`, `move_home`, `emergency_stop` |

연동 전제 조건입니다.

1. 팔 PC 의 CAN 인터페이스가 올라와 있어야 합니다(`can0` UP, bitrate 1000000).
   부팅 자동 활성화는 구성되어 있지 않으므로 `agx_arm_ros/scripts/can_activate.sh` 를 실행합니다.
2. 양쪽 ROS 그래프의 `ROS_DOMAIN_ID` 와 `RMW_IMPLEMENTATION` 을 일치시켜야 합니다.
   RoboClaw 컨테이너는 `ROS_DOMAIN_ID=4`, `rmw_cyclonedds_cpp` 이므로 팔 PC 도 동일하게 맞춥니다.
3. 팔 URDF 루트 링크 이름(`base_link`)이 로봇 본체의 `base_link` 와 충돌합니다.
   로봇 `base_link` → 팔 마운트 프레임 static transform 을 추가하고, 팔 URDF 의
   `world_to_base_link` 체인은 사용하지 않습니다. 프레임 이름을 바꾸면
   `manipulation_base_frame` 도 함께 수정합니다.
4. 엔드이펙터(`effector_type`)가 확정되지 않으면 `manipulation_gripper_group` 을 비워 두고
   `open_gripper`/`close_gripper` 를 사용하지 않습니다.

남은 리스크는 다음과 같습니다.

- RoboClaw 컨테이너(humble)와 팔 PC(jazzy)가 서로 다른 ROS 배포판입니다. MoveIt 서비스/액션
  (`moveit_msgs`)의 배포판 간 호환은 보장되지 않으므로, MoveIt 경유 경로는 실기 검증이 필요합니다.
- 팔 드라이버의 `control/*` 토픽은 `sensor_msgs`/`geometry_msgs`/`std_srvs` 만 사용하므로
  배포판 간 안전합니다. MoveIt 경로가 불안정하면 아래 `agx` 백엔드를 사용합니다.
- 드라이버 기본값은 `auto_enable=true` 입니다. 부팅 시 자동 제어를 피하려면
  `auto_enable:=false`, `control_enabled:=false` 로 기동하고 `control_enable` 서비스로
  제어를 여는 순서를 사용합니다.

### `agx` 드라이버 토픽 백엔드

MoveIt을 거치지 않고 `agx_arm_ctrl` 드라이버의 표준 메시지 토픽만 사용하는 백엔드입니다
(`src/robo_claw_agent/robo_claw_agent/manipulation_runtime/agx_backend.py`).

```yaml
robo_claw_agent_node:
  ros__parameters:
    manipulation_enabled: true
    manipulation_backend: "agx"
    manipulation_base_frame: "base_link"
    # named pose 는 SRDF 이름이 아니라 조인트 맵으로 정의한다.
    manipulation_named_poses_json: '{"home":{"joint1":0.0,"joint2":0.0,"joint3":0.0,"joint4":0.0,"joint5":0.0,"joint6":0.0,"joint7":0.0}}'
```

| 항목 | 기본값 | 설명 |
| :--- | :--- | :--- |
| `manipulation_agx_move_joint_topic` | `control/move_j` | 조인트 목표(`JointState`) |
| `manipulation_agx_move_pose_topic` | `control/move_p` | 포즈 목표(`PoseStamped`) |
| `manipulation_agx_move_linear_topic` | `control/move_l` | 카테시안(직선) 포즈 목표 |
| `manipulation_agx_joint_state_topic` | `feedback/joint_states` | 완료 판정용 관절 피드백 |
| `manipulation_agx_tcp_pose_topic` | `feedback/tcp_pose` | 완료 판정용 TCP 피드백 |
| `manipulation_agx_joint_tolerance_rad` | `0.02` | 관절 도달 판정 허용 오차 |
| `manipulation_agx_pose_tolerance_m` | `0.01` | 포즈 도달 판정 허용 오차 |
| `manipulation_agx_motion_timeout_sec` | `30.0` | 목표 도달 대기 한계 |
| `manipulation_agx_feedback_timeout_sec` | `5.0` | 피드백 수신 대기 한계(넘으면 즉시 실패) |
| `manipulation_agx_home_service` | `move_home` | `home` 자세 요청 시 호출할 드라이버 서비스(`std_srvs/Empty`) |

동작 제약은 다음과 같습니다.

- 완료 판정은 피드백 수렴으로만 합니다. 피드백이 없으면 `ManipulationError` 로 실패합니다.
- 포즈 목표는 `manipulation_base_frame` 과 같은 프레임만 허용합니다(드라이버가 base 기준으로 해석).
- 경로 계획과 충돌 검사가 없으므로 `ROBOT_LIMITS` 조인트 한계와 속도 스케일 검증에 의존합니다.
- `home` 자세는 SRDF group_state 대신 드라이버의 `move_home` 서비스를 호출합니다. 조인트 값을
  임의로 만들지 않으므로 실기 자세가 벗어나지 않습니다.
- 그리퍼는 드라이버 `effector_type` 설정과 `control/hand` 토픽 영역이므로 이 백엔드에 포함하지 않습니다.

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
