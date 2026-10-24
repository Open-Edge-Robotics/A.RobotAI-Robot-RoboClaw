# Skill Manager와 Tool 구성

이 문서는 RoboClaw 아키텍처 그림의 **Skill Manager** 블록(로봇 내장 스킬)과 **Tool** 블록(MCP로 연결하는 외부 도구)에 들어갈 주요 항목을 정리합니다. 스킬별 파라미터와 전체 목록은 [SKILLS.md](SKILLS.md)를, MCP 설정은 [OPERATIONS.md](OPERATIONS.md)의 "MCP(Model Context Protocol) 서버 연동"을 참고합니다.

## 1. 구성 요약

```text
User ── Channel Gateway ── Roboclaw_core (PI Agent) ──┬── LLM Bridge ── LLM
                                                       │        └── MCP ── Tool (외부 도구)
                                                       └── Skill Manager (로봇 내장 스킬)
```

| 블록 | 위치 | 호출 방식 |
|---|---|---|
| Skill Manager | RoboClaw 내부(`robo_claw_agent/skills`, `robo_claw_discovery`) | 에이전트가 계획한 스킬을 `SkillManager`가 실행합니다. 정책·사전조건·입력 스키마 검사를 거칩니다. |
| Tool | RoboClaw 외부의 MCP 서버 | MCP 서버가 제공하는 도구를 스킬처럼 등록해 호출합니다. |

## 2. Skill Manager

robo-claw 소스의 스킬 정의(공개 스킬 95개)를 기준으로 그림의 6개 박스에 나눈 주요 스킬입니다.

| 박스 | 주요 스킬 |
|---|---|
| Navigation | `navigate_to`(장소·좌표 이동), `move_relative`(상대 이동), `rotate` / `face_direction`(회전·방향 맞추기), `patrol` / `follow_waypoints`(순찰·경유지 이동), `stop`, `self_localize`(위치 재추정), `mark_virtual_obstacle`(가상 장애물) |
| Vision | `capture_camera_image`(촬영), `analyze_scene`(VLM 장면 분석), `annotate_image`, `find_object` / `detect_object`(ONNX YOLO 인식), `scan_room`(360° 스캔), `describe_surroundings`(종합 상황 보고), `monitor_detection`(객체 감시) |
| Map | `capture_map`, `analyze_map`, `get_map_visual`, `annotate_map`, `find_reachable_places`(갈 수 있는 장소 등록), `explore`(SLAM 탐험) |
| System/HRI | `get_status`(배터리·좌표), `get_datetime`, `identify_location` / `get_location`(장소 이름·좌표), `rag_search` / `rag_add`(지식 검색·저장), `emergency_stop`(비상 정지), `send_message`(메신저 보고) |
| Cooperate/File | 협업: `list_peer_robots`, `call_peer_robot`, `delegate_task`, `coordinate_peer_task`, `autonomous_cooperate` / 파일: `read_text_file`, `write_text_file`, `run_script` |
| Robo_claw_discovery | ROS 그래프 자동 탐색(토픽·서비스·액션·노드), 네트워크 내 동료 에이전트 탐색, `list_topics`, `ros_command`(읽기 전용 ROS 2 CLI) |

### 2.1 추가 권장 박스

그림의 6개 박스에 들어가지 않는 스킬 그룹입니다. 박스를 늘릴 수 있으면 추가합니다.

| 박스 | 주요 스킬 |
|---|---|
| Manipulation | `adaptive_pick_object`(물체 집기), `grasp` / `place`, `arm_pose`, `open_gripper` / `close_gripper`, `head_pan_tilt`, `stow_for_navigation` |
| Autonomous | `autonomous_act`(자율 행동, Behavior Tree), `reactive_navigate`(이동 중 객체 반응), `condition_reactive`(조건 반응형 작업) |
| Motion (CLOi) | `list_cloid_motions`, `execute_cloid_motion`, `stop_cloid_motion` |

## 3. Tool (MCP)

robo-claw는 자체 외부 도구를 내장하지 않고 **MCP 클라이언트**만 제공합니다. 아래 표는 연결 대상 예시이며, 실제로 어떤 도구가 보일지는 연결한 MCP 서버가 정합니다.

- 설정: `RC_ENABLE_MCP=true`, `RC_MCP_SERVERS_JSON`
- 지원 transport: `stdio`, `sse`, `streamable_http`
- 연결된 서버의 도구는 스킬처럼 등록되어 같은 정책(허용/차단 목록)을 적용받습니다.

| 박스 | 내용 |
|---|---|
| Filesystem MCP | 파일 읽기·쓰기·검색(`@modelcontextprotocol/server-filesystem`, 설정 문서의 기본 예시) |
| Web Search / Fetch | 웹 검색, 웹 페이지·API 조회 |
| 사내 업무 시스템 | 일정·회의실 예약, 사내 DB/ERP, 작업 지시 시스템 조회 |
| IoT / 빌딩 제어 | 엘리베이터·자동문·조명·센서 연동(로봇 이동 경로와 연계) |
| Fleet / 관제 (Maestro) | 다중 로봇 상태·작업 배정 조회 |
| Remote MCP Server | SSE / Streamable HTTP로 연결하는 원격 MCP 서버(인증 헤더 지원) |

설정 예시입니다.

```dotenv
RC_ENABLE_MCP=true
RC_MCP_SERVERS_JSON='[{"name":"filesystem","transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]},{"name":"remote","transport":"sse","url":"https://example.com/mcp/sse","headers":{"Authorization":"Bearer your-token"}}]'
```

## 4. 그림 작성 시 주의

- `robo_mcp`는 Tool 블록에 넣지 않습니다. `robo_mcp`는 외부 시스템이 `robo_claw_grpc`(RosGrpc 서비스, 포트 `50051`)로 **이 로봇을 제어하는 반대 방향** 연동입니다. 그리려면 RoboClaw 바깥에서 gRPC로 들어오는 별도 화살표로 표시합니다.
- Maestro FleetControl 연동은 `robo_claw_grpc`가 Maestro로 연결하는 outbound gRPC 스트림입니다. MCP Tool로 연결하는 관제 조회와는 경로가 다릅니다([OPERATIONS.md](OPERATIONS.md#maestro-fleetcontrol-outbound-connector)).
- 스킬을 추가·변경하면 [SKILLS.md](SKILLS.md)와 이 문서의 박스 구성을 함께 갱신합니다.
