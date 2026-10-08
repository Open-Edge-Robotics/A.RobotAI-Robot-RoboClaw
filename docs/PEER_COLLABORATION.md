# 멀티 에이전트(동료 로봇) 협업

RoboClaw 에이전트는 두 가지 방식으로 다른 RoboClaw 인스턴스와 협업할 수 있습니다.

| 방식                    | 스킬                                                                             | 대상                                              | 특징                                                                                                            |
| ----------------------- | -------------------------------------------------------------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| ROS 액션 직접 호출      | `delegate_task`                                                                  | 같은 ROS 네트워크(도메인) 안의 다른 에이전트 노드 | `{agent_id}/execute_task` 액션 서버를 직접 호출. 네트워크가 분리되어 있으면 사용 불가                           |
| gRPC 피어 통신          | `call_peer_robot`, `broadcast_to_peers`, `list_peer_robots`, `query_peer_status` | `GRPC_TARGET_PEERS_JSON`에 등록된 원격 로봇       | 같은 ROS 네트워크가 아니어도(별도 서브넷/폐쇄망) 동작. `robo_claw_channel`의 gRPC 메신저 서버/클라이언트를 경유 |
| **자율협동 (LLM 대화)** | `autonomous_cooperate`, `stop_autonomous_cooperate`                              | `GRPC_TARGET_PEERS_JSON`에 등록된 동료 로봇       | 동료와 지속적으로 자연어 메시지를 주고받으며 도움 요청/지시/판단. 백그라운드 루프로 동작                        |

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

`./rclaw run`을 사용할 때는 다음 환경 변수를 설정합니다.

```bash
# Former 로봇
export RC_AGENT_ID=Former0047
export GRPC_TARGET_PEERS_JSON='[{"host":"STRETCH_IP","port":50052,"agent_name":"Stretch3"}]'

# Stretch 로봇
export RC_AGENT_ID=Stretch3
export GRPC_TARGET_PEERS_JSON='[{"host":"FORMER_IP","port":50052,"agent_name":"Former0047"}]'
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

`tidy_home`은 특정 방 또는 집 전체 범위의 정리 작업을 한 번 수행합니다. 특정 방은 기억된 좌표로 먼저 이동하고, 집 범위는 기억된 장소만 한 차례 방문합니다. 지속 순찰은 `autonomous_act`의 책임이며, 두 작업은 같은 시점에 중복 실행하지 않습니다.

정리 후보를 관찰하면 다음 정책을 적용합니다.

1. 폐기물임이 명확한 쓰레기·포장지 등만 처리 후보로 인정합니다. 컵·병·의류·개인 소지품·위험물·액체 유출처럼 소유자나 처리 방식이 불분명한 항목은 옮기거나 위임하지 않고 사용자 확인을 요청합니다.
2. 로컬 집기·배치는 등록 스킬뿐 아니라 허용된 쓰레기통 객체 이름과 `cleanup_disposal_target: true`, 비추정 3D `target_pose`가 지정된 기억 장소가 모두 있을 때만 계획합니다. 집기마다 `place` 단계가 필요하며, LLM이 제안한 임의 좌표는 사용하지 않고 기억된 pose로 대체합니다.
3. 로컬 조건이 충족되지 않으면 현재 연결 상태와 매니퓰레이션 능력이 확인된 동료 한 대에게 해당 폐기물 후보만 단발 작업으로 요청합니다. 같은 ROS 네트워크는 `delegate_task`, gRPC 피어는 `call_peer_robot` 경로를 사용하고 응답을 기다립니다. 피어도 안전한 지정 폐기 장소를 확인할 수 없으면 이동하지 않고 사용자 확인을 요청해야 합니다.
4. 요청에는 대상, 관찰 내용, 순찰 장소 또는 현재 지도 위치를 포함합니다. 완료 응답을 받기 전에는 정리가 끝났다고 보고하지 않습니다.
5. 피어가 연결되지 않았거나 매니퓰레이션 능력을 확인할 수 없으면 실행을 위임하지 않고 사용자에게 미처리 사유를 알립니다. 결과가 불명확한 시간 초과는 중복 조작 방지를 위해 자동 재요청하지 않습니다.

이 동작은 정리 작업 한 건을 직접 위임하는 방식이며, `autonomous_cooperate`처럼 지속적으로 대화하거나 여러 로봇이 작업을 조율하는 루프를 시작하지 않습니다. 같은 ROS 네트워크 동료는 ROS 그래프에서 현재 노드가 발견되어야 하며, 해당 피어의 `GRPC_TARGET_PEERS_JSON` 항목에 `capabilities: ["manipulation"]` 또는 매니퓰레이션 역할을 지정해야 합니다. gRPC 동료는 `ListPeers`의 현재 연결 상태를 확인하며, 설정에 능력이 없으면 연결된 피어의 사용 가능 스킬 목록을 조회합니다.

`autonomous_act` patrol 루프의 `ProcessPeerMessages` BT 노드는 별도로 동료가 보낸 메시지를 수집해 `LLMDecideAction` 프롬프트에 "동료와의 최근 대화"로 주입합니다. 따라서 지속 순찰 중 동료의 도움 요청에도 대응할 수 있습니다. `tidy_home`의 위임은 정리 대상 한 건에 대한 단발 요청이며 지속 대화나 협업 루프를 시작하지 않습니다.

### 내부 구조

- 대화 상태: `cooperation_skill/conversation.py` (`PeerConversationManager`)
- 수신 인박스: `cooperation_skill/inbox.py` (`PeerMessageInbox`)
- 자율협동 루프: `cooperation_skill/cooperate_autonomous.py` (`AutonomousCooperateSkill`)
- BT 통합: `cooperation_skill/bt_nodes.py` (`ProcessPeerMessages`)
- 서비스 정의: `robo_claw_msgs/srv/PeerMessage.srv`

## 3. 내부 구조

- 서비스/메시지 정의: `robo_claw_msgs/srv/CallPeerRobot.srv`, `ListPeers.srv`, `SendMessage.srv`, `msg/PeerInfo.msg`
- 스킬 구현: `src/robo_claw_agent/robo_claw_agent/skills/cooperate_skill.py`
- gRPC 메신저 서버/클라이언트: `robo_claw_channel` 패키지 (HTTP/Discord/Slack/Telegram과 같은 채널 노드에 통합되어 있음)
- gRPC 프로토콜 스텁: `robo_claw_cli/proto/messenger_pb`(테스트 도구 `robo_claw_cli test`가 내부적으로 사용)

> `robo_claw_grpc` 패키지가 제공하는 `RosGrpc` 서비스(로봇 상태/내비게이션/매니퓰레이션 등 세분화된 gRPC API, `robo_mcp` 연동용)와는 별개입니다. 그건 외부 시스템이 이 로봇 한 대를 원격 제어하기 위한 API이고, 여기서 다루는 피어 협업은 로봇들끼리 서로 통신하는 기능입니다.
