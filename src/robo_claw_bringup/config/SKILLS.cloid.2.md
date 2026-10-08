# CLOiD 2호 로봇 전용 스킬 가이드 (SKILLS.md)

이 가이드는 **CLOiD(CLOi-D) 2호** 개체에만 해당하는 규칙입니다. 2호는 팔·waist·neck 구동
오류로 **상체를 움직이는 모든 경로를 사용할 수 없는 상태**입니다. 2호에서는 이동·인지·조회·정지
계열 스킬만 사용하고, 상체 동작이 필요한 요청은 수행하지 않은 채 사유와 대안을 안내합니다.
공통 주행·관측·지도 스킬 선택은 시스템 프롬프트의 `[도구 선택 전략]`과 `[파라미터 규칙]`을
따르고, 아래 2호 제약과 CLOiD 공통 제약을 함께 적용합니다.

---

## 2호 상태: 상체 구동 오류

- **사용할 수 없는 동작**: 팔·손·waist·neck을 움직이는 모든 실행 경로입니다.
  - `execute_cloid_motion` — CLOi 등록 모션에는 팔·waist·neck 모션이 포함됩니다.
  - `tidy_home`의 정리 상태 표시 모션 — `scan`(ID 22), `task ready pose`(ID 128)도 상체를
    움직이므로 2호에서는 실행하지 않습니다.
  - 일반 조작 스킬 — `move_joints`, `move_pose`, `grasp`, `place`, `open_gripper`,
    `close_gripper`, `*-pick` 계열은 팔/hand를 요구합니다.
- **사용 가능한 동작**: `navigate_to`, `move_relative`, `spin` 기반 `describe_surroundings`,
  `analyze_scene`, `analyze_map`, `find_reachable_places`, `get_map_visual`, `rag_search`,
  `get_status`, `stop`, `emergency_stop`, `list_cloid_motions`(조회 전용),
  `stop_cloid_motion`(정지 목적 한정).
- **조회와 실행을 구분**: `list_cloid_motions`는 목록 조회만 하므로 2호에서도 사용할 수
  있습니다. 조회 결과를 근거로 `execute_cloid_motion`을 실행하지 않습니다.
- **오류 복구 시도 금지**: 2호의 상체 오류는 운영자 조치 대상입니다. 에이전트가 원인 진단,
  펌웨어/모션 재시도, 반복 실행으로 복구를 시도하지 않습니다. 오류 메시지는 사용자에게 그대로
  전달하고 운영자 확인을 요청합니다.
- **요청 처리**: 상체 동작·조작·모션 실행 요청을 받으면 2호의 팔·상체 구동 오류로 실행할 수
  없음을 알리고, 가능한 대안(이동해서 관측하기, 조작 가능한 동료 로봇에 위임, 운영자 확인)을
  제시합니다. 실행하지 않은 동작을 완료로 보고하지 않습니다.
- **배포 설정**: 2호에는 `cloid_cleanup_indicator_enabled=false`를 설정하고
  `cloid_cleanup_indicator_motion_ids_json`의 allowlist를 비웁니다. 이 설정이 켜져 있으면
  정리 작업 마지막에 상체 표시 모션이 발행됩니다.

## CLOiD 공통 제약

- **일반 조작 백엔드 미구현**: RoboClaw는 CLOiD 팔/hand에 MoveIt 조작 백엔드를 연결하지
  않았고 `manipulation_enabled=false`로 기동합니다. 위 상체 오류와 별개로, 조작 계열 스킬은
  2호에서도 실행 단계에서 거부됩니다.
- **2D LaserScan 없음**: `/scan`(sensor_msgs/LaserScan)을 발행하지 않습니다. 장애물 인지는
  3D 라이다(`/lidar_points_bounded`)와 Nav2 costmap이 담당합니다.
- **위치 추정은 로봇이 담당**: AMCL이 없고 `/amcl_pose`,
  `/reinitialize_global_localization`도 제공되지 않습니다. `self_localize`로 글로벌 재측위를
  수행할 수 없습니다. 위치가 불확실하면 로봇의 맵·측위 관리 기능을 쓰거나 운영자에게
  요청합니다.
- **카메라는 compressed만 제공**: 모든 RGB 카메라(`rgb_cam_*`)는
  `sensor_msgs/CompressedImage`만 발행합니다. 헤드 RGB 카메라의 optical frame이 TF 트리에
  없어 3D 역투영 기반 좌표 계산은 지원되지 않습니다.

---

## 주행 (2호의 주 사용 경로)

- **주행 전 stow 단계 없음**: CLOiD는 주행 전 팔 접기·모드 전환 단계를 요구하지 않습니다.
  상체가 고정된 2호에서도 `navigate_to`, `move_relative`를 바로 사용합니다.
- **프레임**: 전역 프레임은 `map`, 로봇 베이스 프레임은 `base_link`, 오도메트리는
  `/odom`입니다. 좌표는 map 기준으로 해석합니다.
- **베이스 제어 경로**: 로봇의 `diff_drive_controller`는 `/cmd_vel`을
  `geometry_msgs/TwistStamped`로 구독합니다. 직접 cmd_vel 발행(geometry_msgs/Twist)은 이
  로봇에 도달하지 않으므로, 회전과 이동은 Nav2 `spin`/`navigate_to_pose` 경로를 사용합니다.
- **좌표를 모르면**: 지도 기반 스킬(`get_map_visual`, `find_reachable_places`)이나
  `rag_search`로 장소·좌표를 먼저 확인합니다.

---

## 주변 파악 · 인지

- **주변 파악**: `describe_surroundings`는 회전 후 카메라 장면을 분석합니다. 회전은 Nav2
  `spin`으로 수행되므로 2호에서도 동작합니다. LaserScan 거리 분석 부분은 CLOiD에서 비어
  있습니다. 정면 한 장면만 필요하면 `analyze_scene`을 사용합니다.
- **지도 기반 판단**: 문 통과 여부·개방 공간 탐색은 `analyze_map`,
  `find_reachable_places`, `get_map_visual`과 `/map`을 사용합니다(2D LaserScan 없이도 동작).
- **배터리·상태**: `get_status`가 `/battery_state`와 `/robot_pose`에서 배터리 잔량과 map 기준
  좌표를 조회합니다. health state의 `lidar`는 CLOiD에 LaserScan 토픽이 없어 `ERROR`로 표시될
  수 있으니, 이것만으로 라이다 고장이라고 단정하지 않습니다.

---

## 안전 · 정지

- **정지**: `stop`은 Nav2 goal 취소와 직접 cmd_vel 영점 발행을 함께 수행합니다. CLOiD에서는
  Nav2 goal 취소가 실제 정지 경로입니다. 2호에서 상체 동작이 잘못 요청된 경우에도 `stop`으로
  진행 중인 주행을 멈춥니다.
- **등록 모션 정지**: `stop_cloid_motion`은 상체가 아닌 Motion Player에 STOP을 발행하는
  정지 전용 스킬이므로 2호에서도 사용할 수 있습니다. 정지 목적 외에는 호출하지 않습니다.
- **긴급 정지**: CLOiD는 자체 safety machine(`safety_machine_manager`, `/safety/estop`,
  `/emergency/state`)이 모터 안전을 관리합니다. 비상 상황에서는 로봇의 안전 체계가 우선이며,
  RoboClaw의 `emergency_stop` 스킬이 CLOiD 베이스를 직접 세우지는 않습니다.

---

## 정리 스킬 (tidy_home)

- 2호의 `tidy_home`은 기억된 장소를 한 번 순회하며 정리 대상을 관찰하고, 연결 및 조작 능력이
  확인된 동료 한 대에 폐기물 후보를 단발 요청하는 경로로만 사용합니다.
- 2호는 로컬 집기/배치를 수행하지 않으므로, 비추정 3D 폐기 pose가 있더라도 직접 처리하지
  않습니다. 컵·병·개인 소지품·위험물처럼 모호한 항목은 이동하거나 위임하지 않고 사용자 확인을
  요청합니다.
- 상태 표시 모션은 상체를 움직이므로 2호에서는 실행하지 않습니다. 위임 결과가 실패/불명이어도
  정리 완료로 보고하지 않고, 처리되지 않은 대상과 위치를 사용자에게 보고합니다.

### 요약

- CLOiD 2호는 **팔·상체 구동 오류로 상체 동작을 사용할 수 없는 개체**입니다. 이동·인지·조회·
  정지 계열 스킬만 사용하고, 상체 동작 요청은 사유 안내와 대안 제시로 처리합니다.
- 기억할 점: **`execute_cloid_motion`·조작 스킬·정리 표시 모션 사용 불가**, **`self_localize`
  사용 불가**, **LaserScan 없음**, **카메라는 compressed 토픽**.
