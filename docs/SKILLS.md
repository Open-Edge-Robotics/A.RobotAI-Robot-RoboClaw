# 스킬 레퍼런스

현재 소스에 등록된 스킬 전체 목록입니다. `Public` 스킬은 LLM 프롬프트에 노출되며, `Internal` 스킬은 상위 시퀀스가 사용하는 내부 서브루틴입니다. `stretch` 표시는 Stretch3 조작 백엔드가 필요한 스킬입니다.

## 실행 결과와 안전 계약

모든 스킬 결과는 기존 `success`/`message` 필드를 유지하면서 `status`를 함께
반환합니다. 정상 완료는 `completed`, 백그라운드 시작 수락은 `started`, 그리고
`failed`, `timeout`, `cancelled`, `partial` 상태를 구분합니다. timeout 또는 취소가
발생하면 `cancel_requested`와 실제 중단 확인 여부인 `cancel_confirmed`가 결과 JSON에
보존됩니다. 센서 기반 스킬은 용도별 freshness 상한과 frame 검증을 적용하며, stale
또는 frame 불일치 데이터를 조작 목표로 승격하지 않습니다.

스킬은 성공 후 사용자 답변을 만드는 방식(`answer_mode`)을 함께 선언합니다.
조회·목록·분석처럼 결과 데이터를 보여줘야 하는 스킬은 `informational` 로 표시되어
플래너가 결과를 LLM에 넘겨 최종 답변을 합성합니다. 물리 동작 스킬은 기본값인
`action` 으로 남아 실행 결과 메시지를 그대로 보고합니다(추가 LLM 라운드가 두 번째
물리 동작을 계획하는 위험을 피하기 위함입니다).

| 카테고리 | 스킬 | 공개 범위 | 위험도 | 백엔드 | 설명 |
| --- | --- | --- | --- | --- | --- |
| Manipulation | `adaptive_pick_object` | Public | action | stretch | 물체 탐색부터 정렬·파지·복귀까지 수행 |
| Manipulation | `align_right_arm_to_front` | Internal | action | 공통 | 오른쪽 팔 작업축을 목표 방향에 정렬 |
| Manipulation | `arm_pose` | Public | action | 공통 | 이름이 지정된 팔 자세로 이동 |
| Manipulation | `close_gripper` | Public | action | 공통 | 그리퍼 닫기 |
| Manipulation | `classify_object_surface` | Internal | action | 공통 | 물체가 바닥 또는 높은 곳에 있는지 판별 |
| Manipulation | `fixed_pattern_grasp` | Internal | action | stretch | 표면 유형별 결정론적 파지 |
| Manipulation | `grasp` | Public | action | 공통 | pose 또는 물체명 기반 접근·파지·후퇴 |
| Manipulation | `head_pan_tilt` | Public | action | 공통 | 헤드 카메라 pan/tilt 제어 |
| Manipulation | `move_joints` | Public | action | 공통 | 개별 조인트 목표 제어 |
| Manipulation | `move_pose` | Public | action | 공통 | end-effector pose 제어 |
| Manipulation | `observe_gripper_target` | Public | action | stretch | 그리퍼 카메라 물체 관측 및 파지 가능성 진단 |
| Manipulation | `observe_head_target` | Public | action | 공통 | 헤드 RGB-D 물체 3D 위치 진단 |
| Manipulation | `open_gripper` | Public | action | 공통 | 그리퍼 열기 |
| Manipulation | `pick_from_right_side_zone` | Public | action | stretch | 오른쪽 작업 구역의 결정론적 물체 집기 |
| Manipulation | `pick_front_object` | Public | action | stretch | 정면 물체 정렬 및 결정론적 집기 |
| Manipulation | `place` | Public | action | 공통 | pose 또는 좌표 기반 접근·배치·후퇴 |
| Manipulation | `prepare_right_side_pick` | Internal | action | stretch | 오른쪽 집기 준비 자세 구성 |
| Manipulation | `search_object` | Public | action | 공통 | 헤드 스윕과 몸통 회전으로 물체 능동 탐색 |
| Manipulation | `servo_gripper_to_object` | Internal | action | stretch | 그리퍼를 목표 물체에 미세 정렬 |
| Manipulation | `stow_for_navigation` | Public | action | stretch | 주행 전 팔·그리퍼 안전 자세 구성 |
| Manipulation | `stretch_navigation_mode` | Public | action | stretch | Stretch 주행 모드 전환 |
| Manipulation | `stretch_position_mode` | Public | action | stretch | Stretch 조작 모드 전환 |
| Manipulation | `switch_stretch_mode` | Public | action | stretch | Stretch 주행·조작 모드 선택 |
| Manipulation | `vla_pick_front_object` | Public | action | stretch | VLA 기반 정면 물체 집기 |
| Manipulation | `vla_pick_gripper_object` | Public | action | stretch | 그리퍼 시야 물체 VLA 집기 |
| Navigation | `approach_object` | Public | action | 공통 | 카메라 물체를 지정 거리까지 접근 |
| Navigation | `clear_virtual_obstacles` | Public | action | 공통 | 가상 장애물 전체 제거 |
| Navigation | `face_direction` | Public | action | 공통 | 절대 방향·장소·좌표를 바라보기 |
| Navigation | `follow_waypoints` | Public | action | 공통 | 여러 waypoint 순차 이동 |
| Navigation | `mark_virtual_obstacle` | Public | action | 공통 | 가상 장애물 등록 |
| Navigation | `move_relative` | Public | action | 공통 | 현재 방향 기준 상대 이동 |
| Navigation | `navigate_to` | Public | action | 공통 | 좌표 또는 장소명으로 목적지 이동 |
| Navigation | `patrol` | Public | action | 공통 | 지정 장소 반복 순찰 |
| Navigation | `rotate` | Public | action | 공통 | 상대 각도 회전 |
| Navigation | `self_localize` | Public | action | 공통 | AMCL 글로벌 위치 재탐색 |
| Navigation | `set_initial_pose` | Public | action | 공통 | AMCL 초기 x/y/yaw 지정 |
| Navigation | `stop` | Public | action | 공통 | 현재 이동·회전 작업 취소 |
| Navigation | `stop_patrol` | Public | action | 공통 | 순찰 중단 |
| Perception | `describe_surroundings` | Public | action | 공통 | 센서와 VLM 기반 주변 상황 종합 설명 |
| Perception | `detect_object` | Public | action | 공통 | 카메라 객체 감지 상태 확인 |
| Perception | `estimate_gripper_object_pose` | Public | action | 공통 | 그리퍼 물체 3D pose 추정 및 저장 |
| Perception | `find_object` | Public | action | 공통 | 특정 물체 탐지 및 위치 추정 |
| Perception | `get_detections` | Public | action | 공통 | 최신 ONNX 탐지 결과 조회 |
| Perception | `get_distance` | Public | action | 공통 | 방향별 라이다 거리 조회 |
| Perception | `monitor_detection` | Public | action | 공통 | 객체 감지 백그라운드 감시 |
| Perception | `scan_room` | Public | action | 공통 | 방을 360도 스캔하고 객체 등록 |
| Perception | `stop_monitor` | Public | action | 공통 | 객체 감시 중단 |
| Vision | `analyze_scene` | Public | action | 공통 | 단일 카메라 장면 VLM 분석 |
| Vision | `annotate_image` | Public | action | 공통 | 이미지에 객체·위치 표시 |
| Vision | `capture_camera_image` | Public | action | 공통 | 카메라 이미지 캡처 및 전송 |
| Map | `analyze_map` | Public | action | 공통 | OccupancyGrid 통과 가능성 분석 |
| Map | `annotate_map` | Public | action | 공통 | 지도에 좌표 마커 표시 |
| Map | `capture_map` | Public | action | 공통 | 현재 지도 이미지 캡처 |
| Map | `find_reachable_places` | Public | action | 공통 | 도달 가능한 개방 공간 후보 계산 |
| Map | `get_map_visual` | Public | action | 공통 | 현재 지도 시각화 및 전송 |
| Autonomous | `autonomous_act` | Public | action | 공통 | 기억된 좌표 순찰·제자리 관찰(프론티어 탐험 미수행) |
| Autonomous | `tidy_home` | Public | action | 공통 | 특정 기억 장소 또는 집 범위의 단회 정리 작업, 지정된 폐기 pose·집기/배치 계획 검증, 동료 단발 위임, CLOiD 임시 표시 |
| Autonomous | `condition_reactive` | Public | action | 공통 | 조건 감지 시 foreground 중단 및 후속 체인 실행 |
| Autonomous | `explore` | Public | action | 공통 | 미탐사 영역 자율 탐험 |
| Autonomous | `reactive_navigate` | Public | action | 공통 | 이동 중 객체 감지 시 반응 행동 |
| Autonomous | `stop_autonomous` | Public | action | 공통 | 자율 행동 루프 중단 |
| Autonomous | `stop_explore` | Public | action | 공통 | 탐험 루프 중단 |
| Motion | `execute_cloid_motion` | Public | action | CLOiD | 확인된 CLOi 모션 하나 실행(목록·ID 검증 및 선행 모션 차단) |
| Motion | `list_cloid_motions` | Public | read | CLOiD | CLOi ScenarioManager 모션 카탈로그 조회 |
| Motion | `stop_cloid_motion` | Public | action | CLOiD | 확인된 CLOi 모션 정지 요청 |
| Collaboration | `autonomous_cooperate` | Public | action | 공통 | 동료 로봇과 지속적 자연어 협동 |
| Collaboration | `broadcast_to_peers` | Public | action | 공통 | 모든 동료 로봇에 메시지 전송 |
| Collaboration | `call_peer_robot` | Public | action | 공통 | 원격 동료 로봇에 작업 요청 |
| Collaboration | `coordinate_peer_task` | Public | action | 공통 | 동료에게 복합 작업을 순차 위임 |
| Collaboration | `delegate_task` | Public | action | 공통 | 같은 ROS 네트워크 에이전트에 작업 위임 |
| Collaboration | `list_peer_robots` | Public | read | 공통 | 연결된 동료 로봇 목록 조회 |
| Collaboration | `query_peer_capabilities` | Public | read | 공통 | 동료 로봇 능력 목록 조회 |
| Collaboration | `query_peer_status` | Public | read | 공통 | 동료 로봇 상태 조회 |
| Collaboration | `stop_autonomous_cooperate` | Public | action | 공통 | 자율 협동 백그라운드 루프 중단 |
| System/RAG | `emergency_stop` | Public | dangerous | 공통 | 모든 동작 중단 및 비상정지 래치 |
| System/RAG | `get_datetime` | Public | read | 공통 | 현재 날짜·시간 조회 |
| System/RAG | `get_location` | Public | read | 공통 | 장소명에서 좌표 조회 |
| System/RAG | `get_status` | Public | read | 공통 | 배터리·pose·텔레메트리 조회 |
| System/RAG | `identify_location` | Public | read | 공통 | 현재 좌표에서 기억된 장소명 역조회 |
| System/RAG | `list_topics` | Public | read | 공통 | ROS 그래프 토픽 목록·메시지 타입 조회 (전체 또는 카메라 범주 필터) |
| System/RAG | `log_observation` | Public | action | 공통 | 위치·객체·장면 관찰 기록 |
| System/RAG | `rag_add` | Public | action | 공통 | 지식 항목 추가 |
| System/RAG | `rag_add_file` | Public | action | 공통 | 파일 청크를 지식 베이스에 적재 |
| System/RAG | `rag_delete` | Public | action | 공통 | 지식 항목 삭제 |
| System/RAG | `rag_list` | Public | read | 공통 | 지식 항목 목록 조회 |
| System/RAG | `rag_reindex` | Public | action | 공통 | RAG 인덱스 재구성 |
| System/RAG | `rag_search` | Public | read | 공통 | 지식 베이스 의미 검색 |
| System/RAG | `rag_status` | Public | read | 공통 | RAG backend 상태 조회 |
| System/RAG | `reflect_skills` | Public | action | 공통 | 스킬 실행 경험에서 교훈 추출 |
| System/RAG | `reset_emergency_stop` | Public | dangerous | 공통 | 확인 후 비상정지 래치 해제 |
| System/RAG | `ros_command` | Public | write | 공통 | ROS2 CLI 및 시스템 명령 실행 |
| File | `analyze_stored_file` | Public | action | 공통 | 저장 파일 내용·이미지 분석 |
| File | `delete_file` | Public | action | 공통 | 작업 공간 파일 삭제 |
| File | `edit_text_file` | Public | action | 공통 | 텍스트 파일 부분 수정 |
| File | `list_files` | Public | action | 공통 | 작업 공간 파일 목록 조회 |
| File | `read_text_file` | Public | action | 공통 | 텍스트 파일 읽기 |
| File | `run_script` | Public | action | 공통 | 작업 공간 스크립트 실행 |
| File | `write_text_file` | Public | action | 공통 | 텍스트 파일 생성·쓰기 |
| Butler | `list_butler_scripts` | Public | action | 공통 | Butler 스크립트 목록 조회 |
| Butler | `run_butler_script` | Public | dangerous | 공통 | 허용된 Butler 스크립트 실행 |
| HRI | `send_message` | Public | action | 공통 | 텍스트·파일 메시지 전송 |

읽기 전용 스킬은 [태스크 큐](OPERATIONS.md#태스크-큐-순차-실행)를 우회해 즉시 실행될 수 있습니다. 위험 스킬은 HTTP API에서 기본 차단되며 `skill_allowed_json`과 `skill_blocked_json`으로 조정합니다. 상세 파라미터와 안전 조건은 시스템 프롬프트의 schema 요약, 각 기능 문서, 그리고 실제 `input_schema`를 기준으로 합니다.
