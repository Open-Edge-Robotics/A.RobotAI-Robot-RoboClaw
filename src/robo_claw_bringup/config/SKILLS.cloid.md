# CLOiD 로봇 전용 스킬 가이드 (SKILLS.md)

이 가이드는 **CLOiD(CLOi-D)** 휴머노이드 로봇에만 해당하는 규칙입니다. CLOiD는
차동 구동 베이스 + **7-DOF 양팔** + **5지 omnihand** + 4-DOF waist + 2-DOF neck을 갖는
서비스 휴머노이드입니다. 일반적인 주행·관측·지도 스킬 선택은 시스템 프롬프트의
`[도구 선택 전략]`과 `[파라미터 규칙]`을 따르되, 아래 CLOiD 특유의 제약·차이점을
준수하세요.

---

## 로봇 특성 & 제약

- **조작 백엔드 미구현**: RoboClaw는 아직 CLOiD 팔/hand에 MoveIt 조작 백엔드를 연결하지
  않았으며 `manipulation_enabled=false`로 기동합니다. 일반 조작 스킬(`move_joints`,
  `move_pose`, `grasp`, `place`, `open_gripper`, `close_gripper` 등)은 실행 단계에서
  거부됩니다. 별도 CLOi 모션 스킬은 아래의 검토된 이름 기반 모션만 허용하며, 이 기능이
  임의 관절 조작이나 MoveIt 조작을 활성화하지는 않습니다. Stretch3 전용 스킬
  (`stow_for_navigation`, `switch_stretch_mode` 등)은 백엔드 게이팅으로 카탈로그에서
  제외됩니다.
- **2D LaserScan 없음**: CLOiD는 `/scan`(sensor_msgs/LaserScan)을 발행하지 않습니다.
  장애물 인지는 3D 라이다(`/lidar_points_bounded`)와 Nav2 costmap이 담당하므로,
  LaserScan을 직접 구독하는 스킬은 사용할 수 없습니다.
- **위치 추정은 로봇이 담당**: CLOiD에는 AMCL이 없고 `/amcl_pose`,
  `/reinitialize_global_localization`도 제공되지 않습니다. 따라서
  `self_localize`로 글로벌 재측위를 수행할 수 없습니다. 위치가 불확실하면
  로봇의 맵·측위 관리 기능을 쓰거나 운영자에게 요청하세요.
- **카메라는 compressed만 제공**: 모든 RGB 카메라(`rgb_cam_*`)는
  `sensor_msgs/CompressedImage`만 발행합니다. 이미지가 필요하면 compressed 토픽을
  지정하세요. 헤드 RGB 카메라의 optical frame이 TF 트리에 없어 3D 역투영 기반
  좌표 계산은 지원되지 않습니다.

---

## 이름 기반 모션

- `list_cloid_motions`는 CLOi ScenarioManager의 `/task_manager/motion_map`
  (`std_msgs/msg/String`, transient-local)에서 실제 등록 목록을 읽습니다. 각 항목의 `id`,
  `display_name`, `intent_id`, 설명, 관절 mask, 선행 모션 ID를 반환합니다.
- 토픽에서 목록을 받을 수 없는 배포에서는 `cloid_motion_catalog_file`에 RoboClaw 실행 환경에서
  읽을 수 있는 `motion_map.json` 경로를 지정할 수 있습니다. 파일은 읽기 전용으로 마운트하고
  크기는 256 KiB 이하로 유지합니다. CLOi 호스트의 모션 목록 경로를 쓰려면 컨테이너에 별도
  read-only bind mount를 추가해야 합니다.
- `execute_cloid_motion`은 목록에서 정확히 일치하는 `motion_name`을 확인하고 등록 ID를
  `/task_manager/motion_cmd`의 `task_manager_msgs/msg/MotionCmd` (`command_type=0`, START)으로
  한 번 발행합니다. 직접 모션은 사용자의 명시적 요청이 있어야 합니다. 유일한 내부 예외는 `tidy_home`의
  로봇 설정 opt-in 및 ID allowlist가 허용한 상태 표시 모션입니다. 표시 이름이
  중복되면 목록의 `motion_id`도 함께 지정해야 합니다.
- `pre_id`가 있는 모션은 선행 동작 완료를 자동으로 보장할 수 없어 차단합니다. 목록에 없는 ID,
  이름-ID 불일치, Motion Player 구독자 부재, 인터페이스 부재는 실행 전에 거부합니다.
- `stop_cloid_motion`은 사용자의 명시적 요청(`confirm=true`)을 받아 MotionCmd STOP을 발행합니다.
  시작과 정지 모두 토픽 응답이 없으므로 발행 이후 실제 로봇 상태를 확인해야 합니다.
- `MotionCmd`는 실행 완료 결과나 취소 확인을 제공하지 않습니다. 성공 응답은
  명령 발행을 의미할 뿐 모션 완료를 보증하지 않습니다. 개별 모션 실행 후 로봇 상태를 확인하고,
  실제 실행은 사용자 확인 및 주변 안전 확인을 거쳐야 합니다. 이 스킬은 MoveIt 조작 기능을
  활성화하지 않습니다.

## 주행 (핵심)

- **주행 전 stow 단계 없음**: CLOiD는 주행 전 팔 접기·모드 전환 단계를 요구하지
  않습니다. 팔이 안전한 자세이고 조작 동작이 진행 중이 아니면 `navigate_to`,
  `move_relative` 같은 주행 스킬을 바로 사용합니다.
- **프레임**: 전역 프레임은 `map`, 로봇 베이스 프레임은 `base_link`, 오도메트리는
  `/odom`입니다. 좌표는 map 기준으로 해석합니다.
- **베이스 제어 경로**: 로봇의 `diff_drive_controller`는 `/cmd_vel`을
  `geometry_msgs/TwistStamped`로 구독합니다. 짧은 제자리 회전 폴백에서 쓰는 직접
  cmd_vel 발행(geometry_msgs/Twist)은 이 로봇에 도달하지 않으므로, 회전은 Nav2
  `spin`/`navigate_to_pose` 경로를 우선 사용합니다.
- **좌표를 모르면**: 지도 기반 스킬(`get_map_visual`, `find_reachable_places`)이나
  `rag_search`로 장소·좌표를 먼저 확인합니다.

---

## 주변 파악 · 인지

- **주변 파악**: `describe_surroundings`는 회전 후 카메라 장면을 분석합니다. 회전은
  Nav2 `spin`으로 수행되며, LaserScan 거리 분석 부분은 CLOiD에서 비어 있습니다.
  정면 한 장면만 필요하면 `analyze_scene`을 사용합니다.
- **지도 기반 판단**: 문 통과 여부·개방 공간 탐색은 `analyze_map`,
  `find_reachable_places`, `get_map_visual`과 `/map`을 사용합니다(2D LaserScan 없이도
  동작).
- **배터리·상태**: `get_status`가 `/battery_state`와 `/robot_pose`에서 배터리 잔량과
  map 기준 좌표를 조회합니다. health state의 `lidar`는 CLOiD에 LaserScan 토픽이
  없어 `ERROR`로 표시될 수 있으니, 이것만으로 라이다 고장이라고 단정하지 마세요.

---

## 안전 · 정지

- **정지**: `stop`은 Nav2 goal 취소와 직접 cmd_vel 영점 발행을 함께 수행합니다.
  CLOiD에서는 Nav2 goal 취소가 실제 정지 경로이며, 직접 cmd_vel 영점 발행은
  `TwistStamped` 구독 노드에 도달하지 않습니다.
- **긴급 정지**: CLOiD는 자체 safety machine(`safety_machine_manager`, `/safety/estop`,
  `/emergency/state`)이 모터 안전을 관리합니다. 비상 상황에서는 로봇의 안전 체계가
  우선이며, RoboClaw의 `emergency_stop` 스킬이 CLOiD 베이스를 직접 세우지는 않습니다.

---

## 정리 스킬의 임시 모션 표시

- `tidy_home`에서 명확한 폐기물 후보를 발견하면 먼저 연결된 조작 가능 동료에게 실제 처리를 단발 요청합니다. 컵·병·개인 소지품·위험물처럼 모호한 항목은 이동하거나 위임하지 않고 사용자 확인을 요청합니다.
- 장소 관찰과 위임 시도가 끝난 뒤 `cloid_cleanup_indicator_motion_ids_json`에 지정된 승인 모션 중 `scan`(ID 22)과
  `task ready pose`(ID 128) 하나를 무작위로 실행해 작업 중임을 표시할 수 있습니다.
  카탈로그 이름·ID가 일치하고 선행 모션이 없는 경우만 허용합니다.
- 이 고정 모션은 물체를 파지·이동·폐기하지 않습니다. 피어 위임 결과가 실패/불명이어도 모션 표시를 정리 완료로 보고하지 않습니다.
  모션 토픽은 완료 확인을 제공하지 않으므로 작업당 최대 한 번만 발행합니다.
- `wipe_1`(ID 59)은 천을 집는 실제 조작 모션이므로 표시용 풀에 포함하지 않습니다. `wipe_2`(ID 60)도 이를 선행 조건으로 요구해 제외합니다.
- 이 경로는 VLA 기반 조작이 구현되면 제거·대체할 임시 표시 동작입니다. `manipulation_enabled=false`를 해제하지 않으며
  임의 관절 제어를 허용하지 않습니다.

### 요약

- CLOiD는 **양팔·양손을 가진 휴머노이드**이지만 RoboClaw 조작 백엔드는 아직 없습니다. `tidy_home`은 실제 정리를
  동료에게 요청하며, CLOiD의 임시 카탈로그 모션은 작업 표시용일 뿐 물체를 정리하지 않습니다.
- 기억할 점: **주행 전 stow 불필요**, **`self_localize` 사용 불가**, **LaserScan 없음**,
  **카메라는 compressed 토픽**.
