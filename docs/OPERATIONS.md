# 운영 파라미터 & 태스크 큐

## 운영 파라미터

다음 launch 파라미터는 운영 안전성과 직결됩니다.

모두 `.env`(`RC_*`) 또는 `./rclaw run <파라미터>:=<값>` launch 인자로 오버라이드할 수 있습니다(둘 다 비어 있으면 코드 기본값 사용).

| 파라미터                                 | 기본값                                   | `.env` 변수                                 | 설명                                                                                                                                                                                   |
| ---------------------------------------- | ---------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `llm_fail_fast`                          | `false`                                  | `RC_LLM_FAIL_FAST`                          | 시작 시 LLM 설정 검증 및 헬스체크 실패 시 기동 중단. 운영 배포 시 `true` 권장.                                                                                                         |
| `strict_config`                          | `false`                                  | `RC_STRICT_CONFIG`                          | 필수 설정 파일(ROBOT_LIMITS.json, ROBOT.md 등) 누락 시 기동 중단. 운영 배포 시 `true` 권장.                                                                                            |
| `skill_allowed_json`                     | `[]`                                     | `RC_HTTP_ALLOWED_SKILLS_JSON`               | 허용할 스킬 이름 JSON 리스트. 빈 리스트면 전체 허용.                                                                                                                                   |
| `skill_blocked_json`                     | `["delete_file", "emergency_stop", ...]` | `RC_HTTP_BLOCKED_SKILLS_JSON`               | 차단할 스킬 이름 JSON 리스트. 모든 실행 경로(HTTP /task, /skill, gRPC, 메신저, ROS service)에 공통 적용.                                                                               |
| `task_queue_max_size`                    | `8`                                      | `RC_TASK_QUEUE_MAX_SIZE`                    | 태스크 큐 최대 대기열 크기. `0`이면 큐 비활성화(병렬 실행). 연달은 요청을 순차 처리하려면 `1` 이상 설정.                                                                               |
| `enable_task_decomposition`              | `true`                                   | `RC_ENABLE_TASK_DECOMPOSITION`              | 복합 명령("A로 이동해 B하고 C해") 자동 분해 활성화. 하위 지시문으로 나눠 순차 실행해 SLM이 여러 절을 한 번에 처리하지 못하는 문제를 보완합니다. 단순 명령은 영향 없음(기존 경로 그대로). |
| `task_decomposition_max_steps`           | `6`                                      | `RC_TASK_DECOMPOSITION_MAX_STEPS`           | 복합 명령 분해 시 허용하는 최대 하위 단계 수.                                                                                                                                          |
| `task_step_max_retries`                  | `1`                                      | `RC_TASK_STEP_MAX_RETRIES`                  | 분해된 하위 단계 실패 시 동일 단계를 재시도하는 횟수. 재시도 후에도 실패하면 남은 단계를 중단하고 보고합니다.                                                                            |
| `task_decomposition_wait_margin_cap_sec` | `1800.0`                                 | `RC_TASK_DECOMPOSITION_WAIT_MARGIN_CAP_SEC` | `ExecuteTask` 액션이 결과를 기다리는 여유 시간의 상한(초). 복합 명령은 단계 수 × 재시도 횟수에 비례해 여유를 늘리되 이 값을 넘지 않는다. 단순 명령은 영향 없음(기존 300초 마진 유지).  |

## 태스크 큐 (순차 실행)

사용자가 여러 요청을 연달아 보내면, 에이전트는 **하나씩 순차적으로 처리**합니다.
ExecuteTask action goal과 ExecuteSkill service 요청이 모두 단일 워커 큐를 통과하여,
`_state` / `_current_skill` 경쟁을 방지합니다.

- **대기 중**: 큐에 적재되면 `feedback`으로 대기열 위치를 전송합니다.
- **큐 가득 참**: `task_queue_max_size`를 초과하면 요청이 거절됩니다.
- **읽기 전용 스킬**: `get_status`, `query_peer_status`, `rag_search` 등은 큐를 우회해 즉시 실행됩니다.
- **비활성화**: `task_queue_max_size:=0`으로 병렬 실행(기존 동작)으로 되돌릴 수 있습니다.

```bash
# 큐 크기 4로 실행
./rclaw run task_queue_max_size:=4

# 큐 비활성화 (병렬 실행)
./rclaw run task_queue_max_size:=0
```

## Maestro FleetControl outbound connector

`robo_claw_grpc`를 실행할 때 `MAESTRO_IP`가 설정되어 있으면 Robot이 Maestro의 FleetControl server로 outbound stream을 연결합니다. `rclaw launch --docker --use-grpc`는 `.env`를 컨테이너에 전달하므로 다음 설정을 사용할 수 있습니다.

```bash
ROBOT_ID=former-01
MAESTRO_IP=192.168.10.10
ROBOT_PORT=50053
ROBOT_SITE_ID=lab
ROBOT_MAP_ID=floor-1
ROBOT_MAP_VERSION=v1
ROBOT_MAP_FRAME_ID=map
RC_SKILLS_GUIDE_FILE=/ros2_ws/src/robo_claw_bringup/config/SKILLS.former.md
FLEET_CONTROL_TLS=false
MAESTRO_DISCONNECT_POLICY=complete
FLEET_COMMAND_JOURNAL_PATH=/tmp/robo_claw_fleet_commands.sqlite3
```

- `MAESTRO_IP`가 비어 있으면 connector는 비활성화됩니다.
- 개발 단계의 insecure 연결은 신뢰할 수 있는 LAN에서만 사용합니다.
- `ROBOT_ID`는 배포 동안 유지하고 `session_id`는 process 재시작마다 바뀝니다.
- capability는 `SKILLS.md`에서 구조화해 registration 시 전송합니다.
- command idempotency와 완료 결과는 SQLite journal에 저장됩니다.
- 기본 disconnect 정책 `complete`는 현재 명령을 마친 뒤 reconnect합니다.
- `stop`은 로컬 stop skill을 실행하고 active command를 취소합니다.
- `finish_atomic`은 현재 FleetControl command를 atomic 단위로 완료합니다.
- 연결 단절 중 완료한 결과는 reconnect 후 다시 전송합니다.

운영 mTLS 설정:

```bash
FLEET_CONTROL_TLS=true
FLEET_CONTROL_CA_CERT=/run/secrets/ca.crt
FLEET_CONTROL_CLIENT_CERT=/run/secrets/robot.crt
FLEET_CONTROL_CLIENT_KEY=/run/secrets/robot.key
```

## MCP(Model Context Protocol) 서버 연동

에이전트가 외부 MCP 서버(파일시스템, 원격 SSE/Streamable HTTP 서버 등)에 연결해 그 서버가 제공하는 도구를 스킬처럼 호출할 수 있습니다.

`.env` 설정:

```bash
RC_ENABLE_MCP=true
# stdio, sse, streamable_http 방식을 혼용 가능
RC_MCP_SERVERS_JSON='[{"name":"filesystem","transport":"stdio","command":"npx","args":["-y","@modelcontextprotocol/server-filesystem","/tmp"]},{"name":"remote","transport":"sse","url":"https://example.com/mcp/sse","headers":{"Authorization":"Bearer your-token"}}]'
```

- `RC_ENABLE_MCP=false`(기본값)면 `RC_MCP_SERVERS_JSON`이 있어도 로드하지 않습니다.
- `stdio` 서버: `name`, `transport`, `command`, `args`(선택), `env`(선택), `cwd`(선택)
- `sse` 서버: `name`, `transport`, `url`, `headers`(선택), `connect_timeout_sec`(선택, 기본 5.0), `read_timeout_sec`(선택, 기본 300.0)
- `streamable_http` 서버: `name`, `transport`, `url`, `headers`(선택)
