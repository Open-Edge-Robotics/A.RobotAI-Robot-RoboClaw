# CLOiD 1호 로봇 전용 스킬 가이드 (SKILLS.md)

이 가이드는 **CLOiD(CLOi-D) 1호** 개체에만 해당하는 규칙입니다. 1호는 이동과 CLOi 등록
모션 실행이 모두 가능한 정상 구성입니다. 공통 주행·관측·지도 스킬 선택은 시스템 프롬프트의
`[도구 선택 전략]`과 `[파라미터 규칙]`을 따르고, 아래 CLOiD 공통 제약과 1호 상태를 함께
적용합니다.

---

## 1호 상태

- **이동과 상체 모션 모두 사용 가능**: 1호는 CLOi ScenarioManager에 등록된 이름 기반 모션을
  실행할 수 있으며, 여기에는 팔·waist·neck을 움직이는 모션이 포함됩니다.
- **일반 조작 백엔드는 공통으로 미구현**: RoboClaw는 CLOiD 팔/hand에 MoveIt 조작 백엔드를
  연결하지 않았고 `manipulation_enabled=false`로 기동합니다. `move_joints`, `move_pose`,
  `grasp`, `place`, `open_gripper`, `close_gripper` 같은 일반 조작 스킬은 실행 단계에서
  거부됩니다. 상체 동작은 아래의 이름 기반 모션 경로로만 수행합니다.
- **Stretch3 전용 스킬 제외**: `stow_for_navigation`, `switch_stretch_mode` 등은 백엔드
  게이팅으로 카탈로그에서 제외됩니다. `[사용 가능한 스킬]`에 없는 스킬은 이 로봇에 없는 것이
  정상이므로 이름을 추측해서 호출하지 않습니다.

## CLOiD 공통 제약

- **2D LaserScan 없음**: CLOiD는 `/scan`(sensor_msgs/LaserScan)을 발행하지 않습니다.
  장애물 인지는 3D 라이다(`/lidar_points_bounded`)와 Nav2 costmap이 담당하므로,
  LaserScan을 직접 구독하는 스킬은 사용할 수 없습니다.
- **위치 추정은 로봇이 담당**: AMCL이 없고 `/amcl_pose`,
  `/reinitialize_global_localization`도 제공되지 않습니다. 따라서 `self_localize`로 글로벌
  재측위를 수행할 수 없습니다. 위치가 불확실하면 로봇의 맵·측위 관리 기능을 쓰거나
  운영자에게 요청합니다.
- **카메라는 compressed만 제공**: 모든 RGB 카메라(`rgb_cam_*`)는
  `sensor_msgs/CompressedImage`만 발행합니다. 이미지가 필요하면 compressed 토픽을
  지정합니다. 헤드 RGB 카메라의 optical frame이 TF 트리에 없어 3D 역투영 기반 좌표 계산은
  지원되지 않습니다.

---

## 이름 기반 모션

- `list_cloid_motions`는 CLOi ScenarioManager의 `/task_manager/motion_map`
  (`std_msgs/msg/String`, transient-local)에서 실제 등록 목록을 읽습니다. 각 항목의 `id`,
  `display_name`, `intent_id`, 설명, 관절 mask, 선행 모션 ID를 반환합니다.
- 토픽에서 목록을 받을 수 없는 배포에서는 `cloid_motion_catalog_file`에 RoboClaw 실행 환경에서
  읽을 수 있는 `motion_map.json` 경로를 지정할 수 있습니다. 파일은 읽기 전용으로 마운트하고
  크기는 256 KiB 이하로 유지합니다. CLOi 호스트의 모션 목록 경로를 쓰려면 컨테이너에 별도
  read-only bind mount를 추가합니다.
- `execute_cloid_motion`은 목록에서 정확히 일치하는 `motion_name`을 확인하고 등록 ID를
  `/task_manager/motion_cmd`의 `task_manager_msgs/msg/MotionCmd` (`command_type=0`, START)으로
  발행합니다. 직접 모션은 사용자의 명시적 요청이 있어야 합니다. 내부 예외는 기본 비활성화된
  `tidy_home` 상태 표시와 1호 전용 정리 시연 기능이며, 각각 별도의 ID allowlist로 제한합니다. 표시 이름이
  중복되면 목록의 `motion_id`도 함께 지정합니다.
- `pre_id`가 있는 직접 모션은 `confirm=true`로 실행을 명시 승인하면 선행 모션부터 요청하고 10초 뒤
  대상 모션을 요청합니다. 선행 모션이 누락·순환하거나 중첩 `pre_id`가 있으면 실행하지 않습니다.
  이 간격은 선행 모션의 완료 확인을 대체하지 않습니다. 정리 시연 선택기는 별도 allowlist 안전 경계를 유지하며 `pre_id`가 있는
  모션과 선행 동작을 실행하지 않습니다. 잘못된 선행 체인, 이름-ID 불일치, Motion Player 구독자 부재,
  인터페이스 부재는 실행 전에 거부합니다.
- `stop_cloid_motion`은 사용자의 명시적 요청(`confirm=true`)을 받아 MotionCmd STOP을 발행합니다. 대기 중
  STOP 요청이 들어오면 남은 선행 체인을 보내지 않습니다. 시작과 정지 모두 토픽 응답이 없으므로 발행 이후
  실제 로봇 상태와 이미 시작된 동작의 정지 여부를 확인해야 합니다.
- `MotionCmd`는 실행 완료 결과나 취소 확인을 제공하지 않습니다. 성공 응답은 명령 발행을
  의미할 뿐 모션 완료를 보증하지 않습니다. 10초 대기 후 다음 명령을 보냈더라도 선행 동작의 완료를 뜻하지 않습니다.
  개별 모션 실행 후 로봇 상태를 확인하고, 실제 실행은
  사용자 확인 및 주변 안전 확인을 거쳐야 합니다. 이 스킬은 MoveIt 조작 기능을 활성화하지
  않습니다.

## 주행 (핵심)

- **주행 전 stow 단계 없음**: CLOiD는 주행 전 팔 접기·모드 전환 단계를 요구하지 않습니다. 팔이
  안전한 자세이고 조작 동작이 진행 중이 아니면 `navigate_to`, `move_relative` 같은 주행 스킬을
  바로 사용합니다.
- **프레임**: 전역 프레임은 `map`, 로봇 베이스 프레임은 `base_link`, 오도메트리는
  `/odom`입니다. 좌표는 map 기준으로 해석합니다.
- **베이스 제어 경로**: 로봇의 `diff_drive_controller`는 `/cmd_vel`을
  `geometry_msgs/TwistStamped`로 구독합니다. 짧은 제자리 회전 폴백에서 쓰는 직접 cmd_vel
  발행(geometry_msgs/Twist)은 이 로봇에 도달하지 않으므로, 회전은 Nav2
  `spin`/`navigate_to_pose` 경로를 우선 사용합니다.
- **좌표를 모르면**: 지도 기반 스킬(`get_map_visual`, `find_reachable_places`)이나
  `rag_search`로 장소·좌표를 먼저 확인합니다.

---

## 주변 파악 · 인지

- **주변 파악**: `describe_surroundings`는 회전 후 카메라 장면을 분석합니다. 회전은 Nav2
  `spin`으로 수행되며, LaserScan 거리 분석 부분은 CLOiD에서 비어 있습니다. 정면 한 장면만
  필요하면 `analyze_scene`을 사용합니다.
- **지도 기반 판단**: 문 통과 여부·개방 공간 탐색은 `analyze_map`,
  `find_reachable_places`, `get_map_visual`과 `/map`을 사용합니다(2D LaserScan 없이도 동작).
- **배터리·상태**: `get_status`가 `/battery_state`와 `/robot_pose`에서 배터리 잔량과 map 기준
  좌표를 조회합니다. health state의 `lidar`는 CLOiD에 LaserScan 토픽이 없어 `ERROR`로 표시될
  수 있으니, 이것만으로 라이다 고장이라고 단정하지 않습니다.

---

## 안전 · 정지

- **정지**: `stop`은 Nav2 goal 취소와 직접 cmd_vel 영점 발행을 함께 수행합니다. CLOiD에서는
  Nav2 goal 취소가 실제 정지 경로이며, 직접 cmd_vel 영점 발행은 `TwistStamped` 구독 노드에
  도달하지 않습니다.
- **긴급 정지**: CLOiD는 자체 safety machine(`safety_machine_manager`, `/safety/estop`,
  `/emergency/state`)이 모터 안전을 관리합니다. 비상 상황에서는 로봇의 안전 체계가 우선이며,
  RoboClaw의 `emergency_stop` 스킬이 CLOiD 베이스를 직접 세우지는 않습니다.

---

## 포장(최소 부피) 자세

- 출하 포장처럼 로봇 부피를 최소로 줄여야 할 때 `set_cloid_packing_pose`로 boxing 자세를
  요청합니다. 이 자세는 허리를 한계까지 숙이고 양팔을 앞으로 접어 전고를 낮춥니다.
- 정지 상태에서 사용자의 명시적 요청이 있을 때만 `confirm=true`로 실행합니다. 허리를 크게
  숙인 자세는 무게 중심이 앞으로 쏠려 주행에 부적합하므로 주행 중에는 실행하지 않습니다.
- 이 스킬은 tofu `script_runner`에 `cloid.move_boxing` 실행을 요청하고 스크립트 정상 종료만
  확인합니다. 실제 자세 도달 여부는 확인하지 않으므로 결과를 "자세 완료"로 단정하지 않습니다.
- 자세 복원은 이 스킬에 포함되지 않습니다. 되돌리기가 필요하면 운영자에게 요청합니다.

---

## 상황별 표현 모션

- 개체 설정에서 `RC_CLOID_EXPRESSION_ENABLED` 를 켜면 태스크를 마친 뒤 인사·대화·탐색·실패
  상황에 맞는 등록 모션을 한 번 자동으로 재생합니다. 별도로 스킬을 호출하지 않습니다.
- 이동(주행)이 포함된 태스크에서는 모션을 재생하지 않습니다. 이동 명령에 모션을 붙여
  요청하지 않습니다.

---

## 동료 정리 시연

- `tidy_home`은 명확한 폐기물뿐 아니라 수건, 쿠션/방석, 명확한 빨래/세탁물도 정리 후보로 인식합니다. CLOi 1호에는 VLA/MoveIt 집기 기능이 없으므로 생활용품을 집거나 보관 장소로 옮길 수 없습니다.
- CLOi 1호는 `manipulation_enabled=false`를 유지합니다. 승인된 등록 모션은 실제 집기 능력과 구분하며, 동료 탐색에는 `cleanup_demo` 능력으로만 광고합니다. `cleanup_demo`는 폐기물 후보에 대한 고정 시연 모션 요청일 뿐, 수건·쿠션·빨래를 이동시키지 않습니다.
- 2호가 명확한 폐기물 후보를 관찰하면 기억된 장소 이름, 후보 이름, 장면 요약을 포함한 단일 시연
  요청을 보냅니다. 물체 3D 좌표나 집기 궤적은 전달하지 않으며 1호는 자기 기억 장소에서 위치를
  해석하고 현장을 다시 관찰해야 합니다.
- 요청을 받은 1호는 장소 도착과 대상 재확인에 성공한 경우에만 `cloid_cleanup_demo_motion_ids_json`
  allowlist에서 모션을 한 번 요청합니다. `RC_AGENT_ID`가 `cloid01` 또는 `cloi01`일 때 기본 allowlist는
  `[22, 60]`이며, 각각 좌우 탐색(`scan`)과 등록된 `wipe_2` 모션입니다. 1호 배포의
  `RC_CLOID_CLEANUP_DEMO_MOTION_IDS_JSON`으로 덮어쓸 수 있고, `[]`는 시연 스킬 등록을 차단합니다.
- ID 59도 명시적 allowlist에 넣을 수 있습니다. 시연 선택기는 현재 모션 카탈로그의 `pre_id`가 비어
  있는 항목만 선택하고 선행 모션을 자동 실행하지 않습니다. 따라서 ID 60의 카탈로그 `pre_id`가 59로
  설정되어 있으면 ID 60은 실행하지 않습니다. MotionCmd는 완료 응답을 제공하지 않으므로 결과는
  “임시 모션 실행 요청, 실제 정리·모션 완료 미확인”으로 보고합니다.
- 2호는 상체 오류로 이 시연 기능을 활성화하지 않습니다. 공통 CLOi 프로필의 상태 표시 모션도
  기본 비활성화이며, 일반 관절 제어를 허용하거나 `manipulation_enabled`를 변경하지 않습니다.

### 요약

- CLOiD 1호는 **이동과 CLOi 등록 상체 모션을 모두 실행할 수 있는 정상 개체**입니다. RoboClaw
  MoveIt 조작 백엔드는 아직 없으므로 일반 조작 스킬은 거부되고, 상체 동작은 이름 기반 모션으로만
  수행합니다.
- 기억할 점: **주행 전 stow 불필요**, **`self_localize` 사용 불가**, **LaserScan 없음**,
  **카메라는 compressed 토픽**.
