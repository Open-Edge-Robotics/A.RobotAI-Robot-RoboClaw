# 로봇 에이전트 런타임 구조

이 문서는 현재 RoboClaw의 패키지 책임과 요청 처리 경로를 설명합니다. 실행 설정은
[설정 가이드](../CONFIGURATION_GUIDE.md), 스킬 계약은 [스킬 레퍼런스](../SKILLS.md)를 기준으로 합니다.

## 패키지와 실행 경계

| 패키지 | 책임 |
| --- | --- |
| `robo_claw_msgs` | ROS msg/srv/action 계약을 정의합니다. |
| `robo_claw_core` | C++ 하드웨어·센서 어댑터와 기본 50Hz 루프를 제공합니다. |
| `robo_claw_agent` | 대화·검색 문맥, 라우팅, LLM 계획, 스킬 정책과 실행을 관리합니다. |
| `robo_claw_channel` | HTTP·RoboMessenger gRPC·Discord·Slack·Telegram을 ROS 요청과 연결합니다. |
| `robo_claw_discovery` | 노드의 ROS 그래프 API로 토픽·서비스·액션·동료를 탐색합니다. |
| `robo_claw_grpc` | 외부 제어용 RosGrpc 서비스와 Maestro outbound connector를 제공합니다. |
| `robo_claw_vision` | ONNX 객체 탐지 결과를 발행합니다. |
| `robo_claw_dashboard` | 상태·지도·설정·로그 조회와 시나리오 실행 UI를 제공합니다. |
| `robo_claw_bringup` | 로봇 YAML·launch 인자·선택적 노드를 조립합니다. |
| `robo_claw_cli` | Go CLI `rclaw`의 설정 동기화·실행·진단·QA를 구현합니다. |

모든 로봇 명령이 C++ core를 통과하는 것은 아닙니다. 주행 스킬은 Nav2 action을,
Stretch·AGX 조작 백엔드는 드라이버 action·service·topic을 직접 사용합니다.
정책·스키마·주행 안전 검사는 Python 실행 경계에도 있으며, 외부 로봇 드라이버의
안전 체계를 대신하지 않습니다.

## 실행 진입점

저장소 루트에서 다음 명령을 사용합니다. 과거 `rc.sh`는 현재 진입점이 아닙니다.

```bash
task setup-deps
task proto
task build
./rclaw run
# 시뮬레이션은 ./rclaw sim --nav2를 사용합니다.
# 중앙 프로필은 ./rclaw launch <robot> <env>를 사용합니다.
```

`task unit-test`는 빠른 Python 검사이며 `task test`는 colcon 테스트입니다.
로컬 `.env` 매핑과 원격 프로필의 우선순위는 실행 방식마다 다릅니다.

## 자연어 요청 처리

1. 채널은 자연어 명령을 에이전트의 `ExecuteTask` action으로 전달합니다.
2. 에이전트는 대화 이력·로봇 상태·현재 요청의 검색 문맥을 수집합니다.
3. 사전 라우터는 규칙 또는 Laya 중 하나를 사용해 직접 스킬·고정 응답·LLM 경로를 결정합니다.
4. 기본 `llm_tool_calling=true`이고 provider가 지원하면 스킬 schema를 API `tools`로 전달합니다.
   복합 요청도 원문을 하나의 도구 대화에서 처리하며, 도구 호출·결과를 후속 라운드에 보존합니다.
5. 네이티브 도구 호출을 끄거나 provider가 지원하지 않으면 JSON 계획 경로로 폴백합니다.
   이 경로에서만 활성화된 `TaskDecomposer`가 복합 요청을 분해하고 하위 단계 관문을 적용합니다.
6. `SkillManager.execute`가 정책·사전조건·입력 schema를 확인한 뒤 실행합니다.
   mock 모드에서는 정책·schema를 유지하고 로봇 실행을 대체합니다.
7. 결과·실패·취소·timeout을 요청자에게 반환합니다. 학습 대상 경험은 설정된 표본 정책에 따라
   저장하며, 모든 성공 응답을 무조건 벡터 DB에 저장하지 않습니다.

네이티브 도구 호출의 선택을 JSON 경로의 키워드 교정으로 바꾸지 않습니다. 자세한 경로는
[요청 흐름](../SYSTEM1_LAYA_REQUEST_FLOW_ANALYSIS.md)을 참고합니다.

## 구현 위치

| 기능 | 소스 |
| --- | --- |
| 노드·파라미터·초기화 | `agent_node/node.py`, `params.py`, `initialization.py` |
| 요청 실행·계획 | `agent_node/execution.py`, `planner/llm_planner.py`, `task_planner.py` |
| 사전 라우터 | `agent_node/fast_router.py`, `system1_router.py` |
| LLM 공급자 어댑터 | `llm_bridge/` |
| 스킬 계약·실행기 | `skills/base.py`, `skills/manager.py` |
| 메모리 | `memory_manager/` |
| 로봇 조작 백엔드 | `manipulation_runtime/` |
| 자율·반응형 BT | `skills/autonomous_skill/` |

위 경로는 `src/robo_claw_agent/robo_claw_agent/`를 기준으로 합니다.
`agent_node.py`·`llm_bridge.py`·`memory_manager.py`라는 단일 파일 구조로 해석하지 않습니다.
`skill_manager.py`는 호환 import 경계이며 실제 실행기는 `skills/manager.py`입니다.

## 기억과 자율 행동

장소·별칭·좌표와 실행 경험은 SQLite에서 구조화해 저장·조회합니다.
장소 정확 조회는 `get_location`, 현재 좌표의 장소 역조회는 `identify_location`을 사용합니다.
교훈·장면 설명 등 비정형 지식은 벡터 저장소에서 의미 검색합니다.
[검색과 학습](../RAG_AND_LEARNING.md)에 저장소와 이관 정책을 설명합니다.

`autonomous_act`의 기본 patrol 모드는 기억된 장소를 한 번씩 순회한 뒤 종료합니다.
장소가 없으면 제자리 관찰을 수행하며, 프론티어 탐험은 별도 `explore` 스킬입니다.
반복 순찰은 `patrol(rounds=0)`을 사용합니다. BT의 goal 모드와 반응형 동작은
[행동 트리 가이드](../behavior-tree/README.md)를 참고합니다.

## 인터페이스

| 인터페이스 | 계약 위치 | 용도 |
| --- | --- | --- |
| ROS `ExecuteTask`, `ExecuteSkill`, `QueryState`, `SendMessage` | `src/robo_claw_msgs/action/`, `srv/` | 에이전트 요청·결과·상태·역방향 메시지를 전달합니다. |
| RoboMessenger | `src/robo_claw_channel/proto/messenger.proto` | 채팅·명령·파일 전송과 동료 통신을 제공합니다. 기본 포트는 50052입니다. |
| RosGrpc | `src/robo_claw_grpc/proto/robo.proto` | 외부 제어 API를 제공합니다. 기본 포트는 50051입니다. |
| HTTP 채널 | [HTTP API](../HTTP_API.md) | `/health`, `/status`, `/task`, `/skill`을 제공합니다. |
| 웹 대시보드 | [대시보드](../DASHBOARD.md) | 별도 HTTP 노드로 기본 포트는 9090입니다. |

## 관련 구조도

- [시스템 아키텍처](architecture.md)
- [실행 흐름도](flowchart.md)
- [기능별 책임](functional-architecture.md)
