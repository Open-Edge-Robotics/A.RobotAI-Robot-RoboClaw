# 멀티 에이전트(동료 로봇) 협업

## Maestro 사용 시 (MAESTRO_ENABLED=true)

Maestro가 로봇 명령의 단일 경로인 배포에서는 아래 기능이 **비활성화**됩니다.

- `delegate_task`, `call_peer_robot`, `broadcast_to_peers`, `list_peer_robots`,
  `query_peer_status`, `query_peer_capabilities`, `coordinate_peer_task`,
  `autonomous_cooperate`, `stop_autonomous_cooperate` 스킬은 **등록되지 않습니다**
  (LLM 도구 목록, `query_state`, FleetControl capability에도 나타나지 않음).
- `~/peer_message` 수신이 거부되고, 동료 메시지로 자율협동이 자동 시작되지 않습니다.
- `ENABLE_GRPC`, `ENABLE_GRPC_CLIENT`, `GRPC_TARGET_PEERS_JSON`은 설정 서버가
  `false`/`[]`로 정규화하며, 값이 남아 있어도 client_node가 연결을 열지 않습니다.
- 동료 작업이 필요하면 스킬 결과의 `peer_requests`로 보고하고, Maestro가 해당
  동료에게 후속 작업을 보냅니다. `RC_PEER_HANDOFF_MODE=direct`는 무시됩니다.

**예외**: `request_yield_slot`(`~/yield_slot`)은 LLM 판단이 없는 결정론적 충돌
방지 프로토콜이므로 Maestro 사용 중에도 유지합니다.

**혼재 플릿 주의**: Maestro 명령과 동료 로봇의 `delegate_task`는 같은
`/execute_task` 액션을 사용해 수신 측에서 출처를 구분할 수 없습니다. Maestro를
쓰는 플릿은 모든 로봇에 `MAESTRO_ENABLED=true`를 배포해 peer 스킬을 함께
비활성화해야 합니다.

아래 내용은 Maestro를 사용하지 않는(=`MAESTRO_ENABLED=false`) 배포에 적용됩니다.

RoboClaw 에이전트는 두 가지 방식으로 다른 RoboClaw 인스턴스와 협업할 수 있습니다.

| 방식                    | 스킬                                                                             | 대상                                              | 특징                                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| ROS 액션 직접 호출      | `delegate_task`                                                                  | 같은 ROS 네트워크(도메인) 안의 다른 에이전트 노드 | `{agent_id}/execute_task` 액션 서버를 직접 호출. 네트워크가 분리되어 있으면 사용 불가                           |
| gRPC 피어 통신          | `call_peer_robot`, `broadcast_to_peers`, `list_peer_robots`, `query_peer_status` | `GRPC_TARGET_PEERS_JSON`에 등록된 원격 로봇       | 같은 ROS 네트워크가 아니어도(별도 서브넷/폐쇄망) 동작. `robo_claw_channel`의 gRPC 메신저 서버/클라이언트를 경유 |
| **자율협동 (LLM 대화)** | `autonomous_cooperate`, `stop_autonomous_cooperate`                              | `GRPC_TARGET_PEERS_JSON`에 등록된 동료 로봇       | 동료와 지속적으로 자연어 메시지를 주고받으며 도움 요청/지시/판단. 백그라운드 루프로 동작                        |
| **자리 양보 (결정론)**  | `request_yield_slot` (+ `~/yield_slot` 서비스)                                   | 같은 ROS 네트워크(도메인)의 동료 에이전트 노드    | 목표 지점을 점유한 동료에게 양보를 요청. LLM 판단을 거치지 않고 로컬 정책으로 즉시 판정                        |
| **자리 양보 (gRPC)**    | `request_yield_slot` → `call_peer_yield`                                         | `GRPC_TARGET_PEERS_JSON`에 등록된 원격 로봇       | ROS 서비스가 발견되지 않을 때 JSON 구조화 요청을 중계. 수신측이 로컬 `YieldSlot`으로 판정                     |

## 1. 설정 (`.env`)

```bash
# 이 로봇이 gRPC 서버를 열어 다른 로봇의 요청을 받을지 여부
ENABLE_GRPC=true
# 이 로봇이 다른 로봇에게 먼저 접속(클라이언트)할지 여부
ENABLE_GRPC_CLIENT=true

# 1:1 연결 시 기본 타겟
GRPC_TARGET_HOST=127.0.0.1
GRPC_TARGET_PORT=50052

# 1:N (여러 동료 로봇) 연결 — 설정되어 있으면 위 1:1 설정보다 우선 적용
GRPC_TARGET_PEERS_JSON=[{"host":"192.168.1.10","port":50052,"agent_name":"butler","auth_token":"butler-to-this-robot-token","description":"집게 팔 장착 매니퓰레이터 로봇","role":"manipulator"},{"host":"192.168.1.11","port":50052,"agent_name":"former","auth_token":"former-to-this-robot-token","description":"이동형 카트 로봇","role":"agv"}]
# 모든 로봇에서 동일한 값을 사용해야 하는 gRPC peer 인증 토큰
GRPC_PEER_TOKEN=교체할_랜덤_토큰
```

> `GRPC_PEER_TOKEN`(또는 `GRPC_PEER_TOKENS_JSON`)을 설정하면 이 로봇의 messenger 서버가 `127.0.0.1`이 아닌 `0.0.0.0`으로 바인딩되어 **다른 PC/로봇에서도 채팅에 접속**할 수 있고, 모든 클라이언트는 인증 토큰을 보내야 합니다. 토큰이 없으면 서버는 루프백 전용으로 동작합니다(같은 호스트에서만 접속 가능).
>
> 토큰은 **연결하는 양쪽 로봇 모두에 설정해야 합니다.** 한쪽에만 설정하면 상대 로봇의 서버가
> 루프백(`127.0.0.1`)에만 바인딩되어 다른 로봇의 클라이언트가 접속하지 못하고, 클라이언트 로그에
> `gRPC server connection timed out`이 반복되며, 반대 방향 메시지는 서버에서
> `[gRPC] Unauthorized message rejected`로 거부됩니다.

## 2. 스킬

### `delegate_task` — 같은 ROS 네트워크의 에이전트에 위임

`agent_id`, `instruction`을 받아 `{agent_id}/execute_task` (`robo_claw_msgs/action/ExecuteTask`) 액션 서버를 호출하고 결과를 기다립니다. 별도 gRPC 설정 없이, 같은 ROS 도메인에서 다른 에이전트 노드가 떠 있기만 하면 동작합니다.

### `call_peer_robot` — 원격(gRPC) 동료 로봇 호출

`GRPC_TARGET_PEERS_JSON`으로 연결된 특정 `peer_name`에게 자연어 지시나 파일을 전달합니다. 같은 ROS 네트워크가 아니어도(다른 서브넷의 로봇도) 동작하는 점이 `delegate_task`와의 차이입니다.

파라미터: `peer_name`(필수), `instruction`(지시/메시지, `file_path`만 보낼 경우 생략 가능), `file_path`(선택), `wait_for_result`(선택, 기본 `true` — `false`면 응답을 기다리지 않고 즉시 반환), `timeout_sec`(선택)

### `broadcast_to_peers` — 전체 동료 로봇에게 동시 전달

`GRPC_TARGET_PEERS_JSON`에 연결된 모든 동료 로봇에게 동일한 메시지/파일을 동시에 전달합니다.

파라미터: `message`(필수), `file_path`(선택)

### `list_peer_robots` — 동료 로봇 목록/연결 상태 조회

설정된 동료 로봇들의 `peer_name`과 현재 gRPC 연결 상태를 조회합니다. `call_peer_robot`이나 `query_peer_status`를 쓰기 전 정확한 `peer_name`을 모르면 먼저 이 스킬로 확인합니다. 파라미터 없음. 읽기 전용(`risk_level: read`) 스킬이라 [태스크 큐](OPERATIONS.md#태스크-큐-순차-실행)를 우회해 즉시 실행됩니다.

### `query_peer_status` — 동료 로봇 상태 질의

특정 `peer_name`에게 배터리 잔량, 위치, 수행 중인 작업 여부를 물어보고 자연어 응답을 받습니다. `call_peer_robot`을 상태 조회용으로 미리 정형화한 스킬입니다. 파라미터: `peer_name`(필수), `timeout_sec`(선택). 읽기 전용 스킬로 태스크 큐를 우회합니다.

## 2-1. 자율협동 (`autonomous_cooperate`)

동료 로봇과 **지속적으로 자연어 메시지를 주고받으며** 도움을 요청하거나 지시하고 판단하는 자율협동 기능입니다. `cooperation_skill` 패키지가 담당합니다.

### 동작 방식

`autonomous_cooperate`는 백그라운드 루프를 시작해 매 사이클마다:

1. **수신 메시지 처리** — 동료가 보낸 메시지를 인박스에서 꺼내 LLM으로 응답을 결정하고 `call_peer_robot`로 회신합니다. 동료가 작업을 위임하면(`execute_task`) 스킬 매니저로 실행해 결과를 회신합니다.
2. **능동 협동 점검** — 주기적으로 자신의 상태(배터리/위치/감지 객체)를 점검해 동료에게 도움 요청/지시를 보낼지 LLM으로 판단합니다.
3. **대화 이력 유지** — 모든 대화를 `PeerConversationManager`에 기록해 맥락을 유지합니다.

시작 시 연결된 각 동료에게 협동 시작 인사를 한 번 보내므로, 양쪽 모두 자율협동을 요청한 뒤에도 실제 메시지 교환이 시작됩니다. 연결된 피어는 `GRPC_TARGET_PEERS_JSON`과 ROS 그래프를 우선 확인하고, 필요하면 `ListPeers`의 연결 상태를 보조로 확인합니다.

`goal` 파라미터를 지정하면 해당 목표가 모든 협동 프롬프트와 시작 메시지에 포함됩니다. 예를 들어 `{"goal": "정리"}`로 시작하면 로봇들이 정리할 구역과 맡을 작업을 조율하고 진행 상황을 공유합니다. 원격 로봇이 실제 하위 작업을 실행하려면 `allow_remote_task_execution=true` 및 `remote_allowed_skills` 명시가 모두 필요하며, 동료는 허용된 스킬의 `skill_name`과 `skill_params`를 함께 보내야 합니다.

자율협동은 목표 유무와 관계없이 각 로봇이 자신의 RAG 시멘틱 맵에 저장된 장소와 좌표를 중복 제거해 한 번 순회합니다. 각 장소에서 `navigate_to`로 이동한 뒤 `analyze_scene`을 실행하고, 이동 실패·분석 결과·장소별 진행 상황을 동료에게 공유합니다. 정리·청소 목표에서는 정리 대상을 찾고, 일반 자율협동에서는 새로운 이벤트·환경 변화·도움이 필요한 상황을 찾습니다. RAG에 좌표가 있는 장소가 없으면 임의 좌표로 이동하지 않고 순회를 시작하지 않습니다.

### 다중 로봇 필수 설정

각 로봇은 반드시 서로 다른 `agent_id`를 사용해야 합니다. 기본값 `robo_claw_agent`를 여러 로봇이 공유하면 메시지를 받은 뒤 어느 피어로 결과를 회신해야 하는지 구분할 수 없습니다. 예를 들어 Former 로봇은 `agent_id:=Former0047`, Stretch 로봇은 `agent_id:=Stretch3`으로 실행하고, 각 로봇의 `target_peers_json.agent_name`은 상대 ID와 동일하게 설정합니다.

`ros2 launch`를 직접 사용할 때:

```bash
# Former 로봇
ros2 launch robo_claw_bringup robo_claw.launch.py \
  agent_id:=Former0047 \
  target_peers_json='[{"host":"STRETCH_IP","port":50052,"agent_name":"Stretch3"}]'

# Stretch 로봇
ros2 launch robo_claw_bringup robo_claw.launch.py \
  agent_id:=Stretch3 \
  target_peers_json='[{"host":"FORMER_IP","port":50052,"agent_name":"Former0047"}]'
```

`./rclaw run`을 사용할 때는 각 로봇의 프로젝트 루트 `.env`에 다음 값을 설정합니다.

```bash
# Former 로봇
RC_AGENT_ID=Former0047
GRPC_TARGET_PEERS_JSON='[{"host":"STRETCH_IP","port":50052,"agent_name":"Stretch3"}]'

# Stretch 로봇
RC_AGENT_ID=Stretch3
GRPC_TARGET_PEERS_JSON='[{"host":"FORMER_IP","port":50052,"agent_name":"Former0047"}]'
```

실제 하위 스킬 실행까지 테스트하려면 자동 참여한 로봇도 `allow_remote_task_execution=true`로 시작해야 합니다. 자동 참여는 보안상 이 권한을 원격 메시지로 임의 활성화하지 않습니다.

`allow_remote_task_execution`은 launch 인자가 아닙니다. `autonomous_cooperate` 스킬 호출의 파라미터이므로, 각 로봇에서 다음과 같이 직접 호출하거나 LLM이 동일한 파라미터를 생성하도록 해야 합니다.

```json
{
  "goal": "주변 환경 정리하기",
  "allow_remote_task_execution": true,
  "remote_allowed_skills": [
    "analyze_scene",
    "navigate_to",
    "adaptive_pick_object",
    "grasp",
    "place"
  ]
}
```

실제 조작을 허용하는 예시는 다음과 같습니다. 이 설정은 각 로봇에 적용하고, 실제 로봇에 적용하기 전에 시뮬레이션에서 스킬별 입력과 작업 구역을 확인해야 합니다.

```json
{
  "goal": "정리",
  "allow_remote_task_execution": true,
  "remote_allowed_skills": [
    "analyze_scene",
    "navigate_to",
    "adaptive_pick_object",
    "grasp",
    "place",
    "open_gripper",
    "close_gripper"
  ]
}
```

배터리 상태는 능동 협동 판단을 위한 참고 정보로만 수집하며, 배터리 값에 따라 자율협동을 자동 종료하지 않습니다. 자율협동의 종료는 `max_cycles` 도달 또는 `stop_autonomous_cooperate` 호출로만 수행합니다.

### 파라미터

| 파라미터                      | 기본값                                                                                                                                                           | 설명                                                                                |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `cycle_interval_sec`          | `5.0`                                                                                                                                                            | 사이클 간 대기(초)                                                                  |
| `proactive_interval_sec`      | `30.0`                                                                                                                                                           | 능동 협동 점검 주기(초)                                                             |
| `max_cycles`                  | `0`                                                                                                                                                              | 최대 사이클 수 (0=무제한)                                                           |
| `goal`                        | `""`                                                                                                                                                             | 선택적 협동 목표. 예: `정리`, `회의실 점검` (최대 500자)                            |
| `allow_remote_task_execution` | `false`                                                                                                                                                          | 원격 동료의 작업 실행 허용 여부. 안전 검증 후 명시적으로 활성화                     |
| `remote_allowed_skills`       | `analyze_scene`, `scan_room`, `describe_surroundings`, `detect_object`, `navigate_to`, `adaptive_pick_object`, `grasp`, `place`, `open_gripper`, `close_gripper` | 원격 작업 실행 시 허용할 단일 스킬 이름 목록. 테스트용 정리 작업 스킬이 기본 등록됨 |
| `peer_worker_count`           | `2`                                                                                                                                                              | 동시에 처리할 peer 메시지 worker 수 (1~8)                                           |

gRPC 파일 전송은 기본 최대 `64 MiB`이며 `grpc_max_file_bytes`로 조정할 수 있습니다. 협동 메시지는 `created_at`, `expires_at`, `task_id`, `message_id`를 metadata로 전달하고, 서버는 SQLite dedup journal(`grpc_dedup_path`)을 사용해 재시작 후에도 중복 작업을 차단합니다.

중단은 `stop_autonomous_cooperate` 스킬을 사용합니다.

### 수신 메시지 라우팅

동료가 보낸 메시지는 `robo_claw_channel`의 `client_node`가 수신해 에이전트의 `PeerMessage` 서비스(`~/peer_message`)로 전달하고, 이 서비스가 협동 인박스에 넣습니다. 자율협동 루프가 인박스에서 꺼내 처리합니다.

자율협동 메시지는 일반 `ExecuteTask` 요청과 구분되는 `ChatMessage.metadata[type=peer_cooperation]`로 전달됩니다. gRPC peer token이 설정되면 `metadata[auth_token]`도 함께 검증합니다. 토큰이 없는 기존 설정은 호환되지만 실기 네트워크에서는 사용하지 마세요.

토큰이 설정된 서버는 협동 메시지뿐 아니라 회신과 일반 메시지도 검증합니다. 따라서 `client_node`는
`call_peer_robot`, `broadcast_to_peers`, 협동 회신 등 **모든 송신 메시지**에 `metadata[auth_token]`을
싣고, `ChatStream`·`SendCommand`·`UploadFile` 호출 metadata로 `x-robo-claw-peer-token`과
`x-robo-claw-peer-id`도 함께 보냅니다. 서버는 두 위치의 토큰을 모두 받아들입니다.

`grpc_peer_tokens_json`을 사용하면 `sender_id`별 token을 바인딩할 수 있습니다. 회신(`sender_id=robot`)처럼
`sender_id`가 피어 식별자가 아닌 메시지는 호출 metadata의 `x-robo-claw-peer-id`로 바인딩 대상 피어를 찾습니다.

```bash
GRPC_PEER_TOKENS_JSON={"butler":"butler-token","former":"former-token"}
```

원격 작업 실행은 `allow_remote_task_execution=true`와 `remote_allowed_skills`의 명시적 allowlist가 모두 필요합니다. allowlist가 비어 있으면 원격 작업은 거부됩니다.

수신 측은 allowlist뿐 아니라 실제 등록된 스킬과 위험도도 다시 검사하며, `dangerous` 또는
`write` 위험도의 스킬은 원격 요청으로 실행하지 않습니다. 자율협동 루프와
`autonomous_act`가 동시에 실행되어도 인박스는 원자적인 단일 소비권을 사용하므로 한
메시지가 두 루프에서 중복 소비되지 않습니다. 협업 task 상태에는 완료·실패 외에
`partial`, `cancel_requested`, `cancelled`, `expired`를 기록할 수 있으며 오래된
비종료 task는 만료 처리됩니다.

원격 실행 정책은 단순 허용/거부가 아니라 `allow`, `allow_with_limits`,
`needs_confirmation`, `deny`로 결정됩니다. 읽기 전용 스킬은 허용하고, 일반 action
스킬은 최대 실행 시간과 재시도 예산을 붙여 제한적으로 허용합니다. 로컬 권한이 필요한
스킬은 계속 거부하지만, 결과에 `policy_reason`과 `fallback_action`을 포함해 상태 조회,
capability 재확인, 로컬 확인 요청 또는 부분 결과 보고로 이어질 수 있습니다. 이 정책은
LLM이 아니라 수신 로봇의 로컬 실행 경계에서 최종 적용됩니다.

### `tidy_home`의 단발 작업 위임

`tidy_home`은 특정 방 또는 집 전체 범위의 정리 작업을 한 번 수행합니다. 특정 방은 기억된 좌표로 먼저 이동하고, 집 범위는 기억된 장소만 한 차례 방문합니다. 순찰은 `autonomous_act`의 책임이며(기억된 장소를 한 번씩 순회하면 자동 종료), 두 작업은 같은 시점에 중복 실행하지 않습니다.

정리 후보를 관찰하면 다음 정책을 적용합니다.

1. 정리 후보에는 명확한 폐기물뿐 아니라 수건, 쿠션/방석, 명확한 빨래/세탁물이 포함됩니다. 일반 옷·컵·병·개인 소지품·위험물·액체 유출처럼 소유자나 처리 방법이 불분명한 항목은 임의로 이동하지 말고 확인을 요청합니다.
2. 폐기물은 허용된 쓰레기통 이름과 `cleanup_disposal_target: true`, 비추정 3D `target_pose`가 모두 지정된 기억 장소에만 배치합니다. 생활용품은 기억 객체 metadata에 `cleanup_storage_target: true`, 유형별 `cleanup_return_categories`(허용값: `towel`, `cushion`, `laundry`), 비추정 3D `target_pose`가 있을 때 해당 유형에 맞는 장소에만 배치합니다. 집기마다 `place` 단계가 필요하며, 임의 좌표나 유형이 다른 목적지는 사용하지 않습니다.
3. 로컬 조작은 등록 스킬과 유형별 승인 목적지가 모두 있을 때만 수행합니다. 실제 조작 피어가 없는 경우 확인된 `manipulation` 능력 동료에게 단일 대상을 위임할 수 있습니다. 별도 `cleanup_demo` 능력은 고정 시연 모션 요청 전용이며, 명확한 폐기물뿐만 아니라 단일 수건(`towel`), 쿠션(`cushion`) 등 승인된 생활용품 정리 후보 시연에도 사용할 수 있습니다. 장면 내 폐기물 후보가 함께 관찰되면 폐기물이 최우선 시연 대상으로 선정됩니다.
4. CLOi 시연 요청은 버전이 지정된 구조화된 자연어 명령으로 방 이름, 정리 후보 이름, 장면 요약을 전달합니다. 물체 3D 좌표·파지 궤적은 전달하지 않습니다. 수신 로봇은 자기 지도에서 방을 해석해야 하며, 후보가 재확인되지 않거나 모호하면 시연하지 않습니다. 수신 로봇(`CleanupDemoSkill`)은 한글-영문 레이블 매핑(`수건` ↔ `towel`, `쿠션` ↔ `cushion` 등)을 지원하여 현장 VLM/YOLO 분석 결과와 요청 대상을 안전하게 대조합니다.
5. CLOi MotionCmd는 완료 응답을 제공하지 않으므로 시연 결과를 물체 정리 완료로 보고하지 않습니다. 정리 시연의 성공은 승인된 모션 실행 요청까지를 의미하며, 모션 완료나 물체 이동은 별도로 확인되지 않습니다.
6. 피어가 연결되지 않았거나 요구 능력이 확인되지 않으면 요청하지 않습니다. 결과가 불명확한 시간 초과는 중복 실행을 피하기 위해 자동 재요청하지 않습니다.

이 동작은 한 대상에 대한 단발 요청이며 `autonomous_cooperate`처럼 지속적으로 대화하거나 여러 로봇이 작업을 조율하는 루프를 시작하지 않습니다. 같은 ROS 네트워크 동료는 현재 ROS 그래프에서 발견되어야 하고 `GRPC_TARGET_PEERS_JSON`의 `capabilities`에 `manipulation` 또는 `cleanup_demo`를 정확히 등록해야 합니다. gRPC 동료는 `ListPeers`의 현재 연결 상태를 확인하며, 일반 공개 스킬의 capability가 설정되지 않으면 사용 가능 스킬 목록(JSON 배열 또는 쉼표/불릿 구분 텍스트)을 조회·해석합니다(RAG/LLM 응답 지연을 수용하기 위해 타임아웃 25초 적용, 조회 결과는 300초간 캐싱). CLOi 계열 로봇(이름에 `cloi`가 포함되거나 `execute_cloid_motion` 스킬 보유)은 MoveIt 팔 조작이 불가능한 휴머노이드이므로 `manipulation` 피어로 분류되지 않고 `cleanup_demo` 피어로 자동 분류됩니다. CLOi 1호는 경로 플래그가 기본 `true`이고 검토된 기본 allowlist `[22, 60]`을 사용하지만, allowlist가 비어 있으면 스킬을 등록하지 않습니다. 다른 CLOi 개체도 `RC_CLOID_CLEANUP_DEMO_MOTION_IDS_JSON`으로 검토된 allowlist를 지정하면 동일하게 로컬 시연을 수행합니다.

`autonomous_act` patrol 루프의 `ProcessPeerMessages` BT 노드는 별도로 동료가 보낸 메시지를 수집해 `LLMDecideAction` 프롬프트에 "동료와의 최근 대화"로 주입합니다. 따라서 순찰 중 동료의 도움 요청에도 대응할 수 있습니다(`autonomous_act`는 기억된 장소를 한 번씩 순회하면 종료하므로, 반복 순찰에는 `patrol(rounds=0)`을 사용합니다). `tidy_home`의 위임은 정리 대상 한 건에 대한 단발 요청이며 지속 대화나 협업 루프를 시작하지 않습니다.

### Maestro 관제 중의 위임: 직접 호출 대신 보고 (`RC_PEER_HANDOFF_MODE`)

Maestro(FleetControl)에 연결된 로봇은 모든 로봇 명령이 Maestro를 거쳐야 합니다. 그래야 Maestro의 safety policy, dangerous 스킬 승인, 로봇별 1건 실행, Mission 기록이 지켜집니다. 그래서 관제 중에는 `tidy_home`(그리고 같은 `ResolveCleanupHandoff` BT 노드를 쓰는 `autonomous_act`)이 동료를 `delegate_task`/`call_peer_robot`으로 직접 부르지 않습니다.

| `RC_PEER_HANDOFF_MODE` | 동작 |
| --- | --- |
| 생략 | `MAESTRO_IP`가 있으면 `report`, 없으면 `direct` |
| `report` | 동료를 부르지 않고 필요한 동료 작업을 보고합니다. 장소 결과는 `reported_to_maestro` |
| `direct` | 기존처럼 동료에게 단발 위임합니다 |

`report`에서 `tidy_home`은 기본으로 정리를 끝까지 마친 뒤 반환합니다(`wait_for_completion` 생략 시 `true`). 결과에는 장소별 `results`와 `peer_requests`가 들어갑니다.

```json
{"status": "completed", "run_id": "...", "results": [{"place": "안방", "status": "reported_to_maestro"}],
 "peer_requests": [{"kind": "cleanup_demo", "required_capability": "cleanup_demo",
   "suggested_peer": "cloid01", "place": "안방", "target": "포장지",
   "instruction": "<위임 때 보내던 구조화 명령>", "source_robot": "Former0047"}]}
```

Maestro는 `peer_requests`마다 해당 동료에게 후속 Action을 만들어 실행합니다. 동료 로봇이 `instruction`을 해석하는 방식은 직접 위임과 같습니다. `direct` 모드나 Maestro 없이 쓸 때는 기존 동작(시작 즉시 반환)이 그대로입니다.

### 내부 구조

- 위임 경로 결정: `skills/fleet_handoff.py`
- 대화 상태: `cooperation_skill/conversation.py` (`PeerConversationManager`)
- 수신 인박스: `cooperation_skill/inbox.py` (`PeerMessageInbox`)
- 자율협동 루프: `cooperation_skill/cooperate_autonomous.py` (`AutonomousCooperateSkill`)
- BT 통합: `cooperation_skill/bt_nodes.py` (`ProcessPeerMessages`)
- 서비스 정의: `robo_claw_msgs/srv/PeerMessage.srv`

## 2-2. 자리 양보 협상 (`request_yield_slot`)

좁은 공간에서 두 로봇이 같은 지점을 목표로 하면 뒤에 온 로봇이 목표에 도달하지
못합니다. `request_yield_slot`은 목표 지점을 점유한 동료에게 자리를 비켜줄 것을
요청하고, 요청을 받은 동료는 로컬 정책으로 판정한 뒤 안전한 다른 지점으로 실제
이동합니다.

### 요청자 흐름

1. 이동 스킬은 Nav2 goal을 보내기 **전에** `request_yield_slot`에 `target_place`(장소
   이름)를 넘겨 자리를 먼저 확인합니다. 이 교섭은 이동을 막지 않습니다(거절·시간
   초과여도 goal은 그대로 전송).
2. 선확인은 다음 세 경로에서 모두 동작합니다.
   - `navigate_to`의 장소 이름 이동(사용자 요청)
   - 동료가 위임한 이동(`autonomous_cooperate`의 `execute_task`)
   - 자율협동 장소 순회(`_run_place_sweep`)
   좌표만 있는 이동은 자기 시멘틱 맵에서 가까운 장소 이름(기본 1.0m)을 되짚어
   확인하고, 이름을 찾지 못하면 좌표를 넘기지 않는 원칙에 따라 묻지 않습니다.
3. 같은 ROS 도메인의 `*/yield_slot` 서비스를 찾아 동료별로 요청합니다(`peer_name`을 주면 그 동료만).
4. 응답이 `accepted`면 동료가 이미 비켜선 상태입니다.
5. `busy`면 `retry_after_sec`만큼 기다렸다가 `max_attempts`(기본 2회)까지 재요청합니다.
6. `not_needed`면 동료가 그 자리를 점유하지 않았다는 뜻이므로 원인을 다른 곳에서 찾아야 합니다.

### 수신측 판정 순서

| 순서 | 조건 | 응답 |
| --- | --- | --- |
| 1 | 요청 파라미터가 유효하지 않음 | `denied` / `invalid_request` |
| 2 | `yield_slot_enabled=false` | `denied` / `yield_disabled` |
| 3 | 장소 이름을 이 로봇의 시멘틱 맵에서 해석하지 못함 | `denied` / `unknown_place` |
| 4 | 현재 위치를 알 수 없음(TF 실패) | `denied` / `pose_unknown` |
| 5 | 수신자가 해석한 그 장소에서 `radius_m` 밖 | `not_needed` / `not_blocking` |
| 6 | 조작/파지 중(`cloid_motion`, `manipulation`, `gripper` 점유) | `busy` / `manipulation_in_progress` |
| 7 | 배터리 10% 미만 | `denied` / `battery_critical` |
| 8 | 양쪽 모두 반경 안 + 로봇 id 사전순에서 상대 우선 | `busy` / `requester_lower_priority` |
| 9 | 태스크 큐 사용 중 또는 자율/자율협동 루프 실행 중 | `busy` / `agent_busy` |
| 10 | 요청자 목표에서 1.5m 이상, 개방도 0.45m 이상, 현재 위치에서 4m 이내인 후보지 없음 | `denied` / `no_safe_spot` |
| 11 | 위 조건을 모두 통과 | `accepted` + 실제 이동 후 응답 |

### 장소 이름으로 협상 (`target_place`)

로봇마다 같은 맵을 쓰더라도 좌표가 다른 경우가 많으므로 **좌표를 넘기지
않습니다.** 목표는 항상 **장소 이름**으로 지정합니다.

1. 요청자가 `target_place: "안방"`을 보냅니다(좌표 없음).
2. 수신자가 **자기 시멘틱 맵**에서 "안방"의 좌표를 해석합니다.
3. 수신자는 그 좌표를 자기 pose와 비교해 점유/거리를 판정합니다.

좌표가 로봇을 넘지 않으므로 `radius_m`도 수신자 프레임에서만 쓰여 일관됩니다.

동시 점유(양쪽 모두 그 자리에 있음) 판정도 좌표를 넘기지 않습니다. 요청자는
**자기 맵에서 스스로 판정한 불리언**(`requester_at_target`)만 보냅니다. 이 값을
보내지 않으면 수신자는 보수적으로 동시 점유로 간주해 중재를 거칩니다.

수신자가 그 장소를 모르면 추측하지 않고 `denied / unknown_place`로 거부합니다.
좌표를 넘기지 않는 것이 원칙이므로 요청자는 다른 지점으로 자동 폴백하지 않고,
사용자에게 장소 등록을 요청하거나 이동을 그대로 진행합니다.

장소 이름 뒤에 로봇 라벨이 붙은 경우(예: `안방 (Former)`)는 라벨을 뗀 표준 이름으로
한 번 더 해석합니다. 라벨은 어느 로봇 맵에서 기록했는지만 표시한 것이고 수신자 맵에는
존재하지 않기 때문입니다. 표준 이름도 해석되지 않으면 그대로 `unknown_place`로
거부하며, 좌표를 추측하지 않습니다.

### 설계 원칙

- **점유한 쪽이 양보합니다.** 두 로봇이 동시에 같은 반경 안에 있을 때만 로봇 id 사전순으로 tie-break 합니다. 비교가 반대칭이므로 양쪽이 각자 판정해도 정확히 한 대만 양보하고 교착·진동이 없습니다.
- **fail-closed.** 위치를 모르거나, 안전한 후보지가 없거나, 지금 이동을 시작할 수 없으면 양보하지 않고 거부를 반환합니다.
- **LLM을 거치지 않습니다.** 수신측 판정은 `domain/yield_slot.py`의 순수 규칙과 로컬 상태만 사용합니다. 네트워크 지연은 서비스 호출 1회로 제한됩니다.
- **조작 중에는 절대 양보하지 않습니다.** 물체를 들고 이동하면 낙하 위험이 있습니다.
- `accepted`는 "비켜주기로 약속"이 아니라 **실제로 비켜선 상태**를 뜻합니다. 수신측은 선정된 자리로 이동을 마친 뒤 응답합니다(이동 상한 25초).

### 파라미터

- 요청자: `target_place`(장소 이름, 필수), `peer_name`(선택), `radius_m`(선택, 0.1~2.0), `max_attempts`(선택, 1~3)
- 수신측 노드 파라미터: `yield_slot_enabled`(기본 `true`) — `false`면 모든 양보 요청을 거부합니다.
- `navigate_to` 노드 파라미터: `yield_precheck_mode`

| 모드 | 동작 |
| --- | --- |
| `ask`(기본) | 장소 이름이 있으면 동료에게 이동 전에 직접 묻는다 |
| `off` | 모든 이동 경로에서 선제 요청을 하지 않는다 |

좌표 비교를 쓰지 않으므로 로컬 자세로 점유를 추정하는 모드는 두지 않습니다.
좌표만 있고 장소 이름을 찾지 못하면 요청하지 않습니다 — 로봇마다 같은 맵이라도
좌표가 다를 수 있어 좌표를 넘기지 않는 것이 원칙입니다.

```bash
# 장소 이름으로 양보 요청 — 좌표는 로봇 사이에 넘기지 않는다
ros2 service call /Former0047/robo_claw_agent_node/yield_slot robo_claw_msgs/srv/YieldSlot \
  "{requester_id: cloid02, target_place: '안방', has_requester_at_target: true, requester_at_target: false, radius_m: 0.8, timeout_sec: 5.0}"
```

### 내부 구조

- 판정 규칙(순수): `src/robo_claw_agent/robo_claw_agent/domain/yield_slot.py`
- 수신측 처리: `src/robo_claw_agent/robo_claw_agent/skills/yield_slot_service.py`
- 요청자 스킬: `src/robo_claw_agent/robo_claw_agent/skills/yield_skill.py`
- 요청자 선확인 규칙(공용): `src/robo_claw_agent/robo_claw_agent/skills/yield_precheck.py`
- 서비스 정의: `robo_claw_msgs/srv/YieldSlot.srv`, 노드 서비스 이름 `~/yield_slot`

## 2-3. 서브넷이 분리된 로봇의 자리 양보 (gRPC 중계)

`GRPC_TARGET_PEERS_JSON`으로 연결된 로봇은 같은 ROS 도메인이 아니므로
`*/yield_slot` 서비스가 발견되지 않습니다. 이때 `request_yield_slot`은 메신저
클라이언트의 `call_peer_yield` 서비스로 구조화된 요청을 중계합니다.

```text
agent → (ROS) CallPeerYield → messenger client_node → (gRPC SendCommand, type=yield_slot)
      → 상대 channel_node → (ROS) YieldSlot → 상대 agent
```

- gRPC `content`는 자유 문자열이므로 JSON 페이로드를 씁니다(`robo_claw_channel/yield_protocol.py`).
- 수신측은 **형식 검증을 통과한 값만** 로컬 `YieldSlot` 서비스로 승격합니다.
  잘못된 페이로드·알 수 없는 판정 코드·크기 초과는 거부합니다.
- `type=yield_slot`은 협동 메시지와 같은 엄격한 인증·만료·중복 검사를 적용합니다.
- 처리기가 없는 노드로 `yield_slot` 메시지가 오면 **자연어 경로로 흘리지 않고** 거부합니다.
  좌표 JSON을 LLM이 지시문으로 해석하는 것을 막기 위함입니다.

### 내부 구조

- 전송 규약(순수): `src/robo_claw_channel/robo_claw_channel/yield_protocol.py`
- gRPC 라우팅: `src/robo_claw_channel/robo_claw_channel/grpc_server.py` (`type=yield_slot`)
- 수신 중계: `src/robo_claw_channel/robo_claw_channel/channel_node.py` (`on_yield_request`)
- 요청 중계: `src/robo_claw_channel/robo_claw_channel/client_node.py` (`~/call_peer_yield`)
- 서비스 정의: `robo_claw_msgs/srv/CallPeerYield.srv`

## 협동 이벤트와 상세 로그

`autonomous_cooperate`의 송수신, 판단, 로컬 정책, 스킬 실행·결과 회신을 구조화된
협동 이벤트로 기록합니다. `QueryState.state_json`의 `cooperation` 객체를 통해 최근
이벤트와 작업 상태를 조회하며, [대시보드 협동 패널](DASHBOARD.md#협동-관측과-데모-화면)에서
같은 정보를 시각화합니다. 일반 peer 호출·ROS 직접 위임·자리 양보 전체를 통합하는
플릿 관측 기능은 포함하지 않습니다.

### 식별자와 상태

| 필드 | 의미 |
| --- | --- |
| `stream_id` | 에이전트 프로세스별 관측 스트림 식별자입니다. 재시작 시 바뀝니다 |
| `sequence` | 해당 스트림의 이벤트 순번입니다 |
| `session_id` | 협동 세션 식별자입니다. 수신 이벤트는 송신자의 세션 ID를 보존합니다 |
| `task_id` | 요청과 후속 회신·실행 결과를 연결하는 작업 식별자입니다 |
| `message_id` | 메시지 한 건의 식별자입니다. 재전송 중복 차단에 사용합니다 |
| `reply_to` | 회신이 가리키는 원래 송신 메시지의 ID입니다 |
| `robot_id`, `peer_id` | 관측한 로컬 로봇과 대화 상대를 구분합니다 |

`queued`는 상대 수신 확인이 아니라 로컬 송신 큐 등록을 의미합니다. `received`와
`running`은 로컬 수신·실행 상태이며, 상대의 구조화된 결과는 원 요청과의 상관을
검증한 뒤 관측 결과로 기록합니다. 산문 응답만으로 상대 작업을 성공 처리하지 않습니다.
상관이 맞지 않는 구조화 결과는 작업 지시로 다시 실행하지 않습니다.
`session_stopped`는 로컬 루프 종료이며 상대 작업 취소를 의미하지 않습니다.

협동 메시지는 기존 gRPC `ChatMessage.metadata`에 ID와 `kind`를 전달합니다.
`request`는 작업 중복 차단을 유지하고, `reply`·`result`는 같은 작업의 서로 다른
메시지를 허용하되 같은 `message_id`의 재전송은 차단합니다. 로컬 `PeerMessage` 서비스와
인박스도 상관 정보를 유지합니다. ID가 없는 기존 평문은 로컬 ID를 생성해 수신합니다.

### 로그와 저장소

- `[CooperationEvent]` JSON 로그에는 메시지 본문, 상관 ID, 판단 요약, 정책 이유와
  로컬 실행 정보를 제한된 크기로 기록합니다. 전체 LLM 프롬프트나 인증 metadata는 기록하지 않습니다.
- 토큰·비밀번호·인증 헤더 등 민감 키와 알려진 본문 패턴을 마스킹합니다. 마스킹이
  임의의 개인정보까지 식별하는 것은 아니므로 메시지에 불필요한 민감정보를 넣지 않습니다.
- 상세 이벤트 본문과 상세 JSON은 각각 최대 2048자로 제한합니다. 상태 조회용 응답은
  더 짧은 최근 이벤트 사본을 사용합니다.
- 에이전트의 메모리 링은 최근 100건, 표시용 작업 스냅샷은 최대 50건을 보관합니다.
- SQLite는 `$XDG_STATE_HOME/robo-claw/cooperation/<robot_id>.sqlite3`에 저장하며,
  `XDG_STATE_HOME`이 없으면 `~/.local/state`를 사용합니다. 파일명에 맞게 로봇 ID를 정규화합니다.
- 저장은 크기가 제한된 별도 큐와 스레드로 처리하고, SQLite 이벤트는 최근 5000행으로
  제한합니다. 저장 오류나 큐 포화가 협동 실행을 막지 않으며 진단에 오류·유실을 노출합니다.
- SQLite 행은 재시작 후에도 남지만, 과거 행을 대시보드에 복구하거나 재생하지는 않습니다.
  기록이 필요한 환경에서는 컨테이너의 해당 상태 디렉터리를 지속 볼륨으로 보존합니다.
- Python `logging` 이벤트는 `/rosout`으로 자동 전달되지 않습니다. 일반 로그 패널에
  나타나지 않더라도 협동 패널은 `QueryState`를 통해 표시하며 프로세스 로그에서 JSON을 확인합니다.

### 배포 주의사항

`PeerMessage.srv`에 상관 필드를 추가하므로 배포 시 관련 인터페이스와 소비 패키지를
함께 재빌드해야 합니다. 재빌드하지 않은 ROS workspace와의 서비스 wire 호환은 보장하지 않습니다.
실제 로봇에 적용하기 전에 fake/mock 테스트와 ROS 환경의 인터페이스 생성·전달을 검증합니다.
공통 runtime contract나 기존 원격 실행 권한·allowlist·취소 정책은 변경하지 않습니다.
Maestro 배포에서는 직접 협동이 계속 비활성화됩니다.

```bash
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install --packages-up-to \
  robo_claw_agent robo_claw_channel robo_claw_dashboard
source install/setup.bash
ros2 interface show robo_claw_msgs/srv/PeerMessage
```

## 3. 내부 구조

- 서비스/메시지 정의: `robo_claw_msgs/srv/CallPeerRobot.srv`, `ListPeers.srv`, `SendMessage.srv`, `msg/PeerInfo.msg`
- 스킬 구현: `src/robo_claw_agent/robo_claw_agent/skills/cooperate_skill.py`
- gRPC 메신저 서버/클라이언트: `robo_claw_channel` 패키지 (HTTP/Discord/Slack/Telegram과 같은 채널 노드에 통합되어 있음)
- gRPC 프로토콜 스텁: `robo_claw_cli/proto/messenger_pb`(테스트 도구 `robo_claw_cli test`가 내부적으로 사용)

> `robo_claw_grpc` 패키지가 제공하는 `RosGrpc` 서비스(로봇 상태/내비게이션/매니퓰레이션 등 세분화된 gRPC API, `robo_mcp` 연동용)와는 별개입니다. 그건 외부 시스템이 이 로봇 한 대를 원격 제어하기 위한 API이고, 여기서 다루는 피어 협업은 로봇들끼리 서로 통신하는 기능입니다.
