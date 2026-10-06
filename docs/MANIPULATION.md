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
