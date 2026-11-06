# HTTP 채널 API

RoboClaw HTTP 채널은 기본적으로 `127.0.0.1:8080`에 바인딩됩니다. 외부 노출이 필요하면 바인드 주소, 허용 CIDR, 토큰을 명시적으로 설정합니다.

## 실행 예시

프로젝트 루트 `.env`에 다음 값을 설정한 뒤 `./rclaw run`을 실행합니다.
`run`의 HTTP 설정 매핑은 셸에서 export한 값이 아니라 `.env`를 읽습니다.

```dotenv
RC_HTTP_HOST="0.0.0.0"
RC_HTTP_PORT="8080"
RC_HTTP_READONLY_TOKEN="robo-read-token"
RC_HTTP_CONTROL_TOKEN="robo-write-token"
RC_HTTP_ALLOWED_CIDRS_JSON='["127.0.0.1/32","192.168.0.0/24"]'
RC_HTTP_RATE_LIMIT_PER_MINUTE="60"
RC_HTTP_ALLOWED_SKILLS_JSON='["get_status","analyze_scene","get_map_visual"]'
```

## 인증 규칙

| 경로      | 메서드 | 권한                     |
| --------- | ------ | ------------------------ |
| `/health` | `GET`  | 토큰 없음 가능           |
| `/status` | `GET`  | 읽기 전용 또는 제어 토큰 |
| `/task`   | `POST` | 제어 토큰                |
| `/skill`  | `POST` | 제어 토큰                |

위 표는 읽기 전용 또는 제어 토큰이 하나 이상 설정된 경우에 적용합니다.
두 토큰이 모두 비어 있으면 인증을 생략하여 `/status`, `/task`, `/skill`도 토큰 없이 접근할 수 있습니다.
읽기 전용 토큰만 설정하면 제어 요청을 인증할 수 없으므로 제어 호출에는 제어 토큰을 설정합니다.
`/health`를 포함한 모든 요청에 CIDR 검사와 요청 제한을 적용합니다.

토큰은 아래 둘 중 하나로 보낼 수 있습니다.

```http
Authorization: Bearer <token>
X-RoboClaw-Token: <token>
```

## 보안 관련 설정

| 환경 변수                       | launch 파라미터              | 설명                                                         |
| ------------------------------- | ---------------------------- | ------------------------------------------------------------ |
| `RC_HTTP_HOST`                  | `http_host`                  | 바인드 주소. 기본값 `127.0.0.1`                              |
| `RC_HTTP_PORT`                  | `http_port`                  | HTTP 포트                                                    |
| `RC_HTTP_READONLY_TOKEN`        | `http_readonly_token`        | 읽기 전용 토큰                                               |
| `RC_HTTP_CONTROL_TOKEN`         | `http_control_token`         | 제어 토큰                                                    |
| `RC_HTTP_ALLOWED_CIDRS_JSON`    | `http_allowed_cidrs_json`    | 허용 CIDR JSON 리스트                                        |
| `RC_HTTP_RATE_LIMIT_PER_MINUTE` | `http_rate_limit_per_minute` | 분당 요청 제한                                               |
| `RC_HTTP_ALLOWED_SKILLS_JSON`   | `http_allowed_skills_json`   | HTTP로 허용할 스킬 JSON 리스트. 비어 있으면 allowlist 미사용 |
| `RC_HTTP_BLOCKED_SKILLS_JSON`   | `http_blocked_skills_json`   | HTTP에서 차단할 스킬 JSON 리스트                             |

CIDR 목록의 기본값은 `["127.0.0.1/32", "::1/128"]`이며, 빈 목록은 모든 IP를 허용합니다.
요청 제한은 기본 60회이며 토큰이 있으면 토큰별, 없으면 IP별로 최근 60초를 계산합니다.
0 이하 값은 요청 제한을 비활성화합니다. 차단 목록은 허용 목록보다 우선합니다.
HTTP 핸들러는 `/skill` 직접 호출에서 목록을 검사합니다. 표준 `robo_claw.launch.py`는
같은 launch 값을 에이전트의 `skill_allowed_json`·`skill_blocked_json`에도 전달하므로,
`/task`·gRPC·메신저·ROS service를 통한 스킬 실행에도 공통 정책으로 적용합니다.
채널과 에이전트를 별도 실행하면 각 노드의 정책을 따로 설정합니다.

기본 차단 스킬:

- `delete_file`
- `emergency_stop`
- `rag_delete`
- `rag_reindex`
- `ros_command`

## 엔드포인트

### `GET /health`

채널 노드와 ROS 클라이언트 readiness, HTTP 보안 설정, 최근 RPC 메트릭을 반환합니다.
`status`는 `ok`로 고정되므로 실제 ROS 준비 여부는 `clients`의 각 값을 확인합니다.

```bash
curl http://127.0.0.1:8080/health
```

응답 예시:

```json
{
  "status": "ok",
  "agent_namespace": "/robo_claw_agent_node",
  "messenger_channel_count": 1,
  "clients": {
    "execute_task_ready": true,
    "execute_skill_ready": true,
    "query_state_ready": true
  },
  "http_security": {
    "auth_enabled": true,
    "allowed_cidrs": ["127.0.0.1/32", "::1/128"],
    "rate_limit_per_minute": 60,
    "allowed_skills": [],
    "blocked_skills": [
      "delete_file",
      "emergency_stop",
      "rag_delete",
      "rag_reindex",
      "ros_command"
    ]
  },
  "metrics": {}
}
```

### `GET /status`

에이전트 `QueryState` 서비스 결과를 JSON으로 반환합니다.

```bash
curl -H "Authorization: Bearer robo-read-token" \
  http://127.0.0.1:8080/status
```

### `POST /task`

자연어 태스크를 에이전트 액션으로 전달합니다.

```bash
curl -X POST http://127.0.0.1:8080/task \
  -H "Authorization: Bearer robo-write-token" \
  -H "Content-Type: application/json" \
  -d '{
    "instruction": "현재 배터리 상태 알려줘",
    "context": ""
  }'
```

요청 본문:

| 필드          | 타입   | 설명                            |
| ------------- | ------ | ------------------------------- |
| `instruction` | string | 자연어 명령                     |
| `context`     | string | 선택. 추가 컨텍스트 JSON 문자열 |

### `POST /skill`

단일 스킬을 직접 호출합니다. allowlist/blocked list 규칙이 적용됩니다.

```bash
curl -X POST http://127.0.0.1:8080/skill \
  -H "Authorization: Bearer robo-write-token" \
  -H "Content-Type: application/json" \
  -d '{
    "skill_name": "get_status",
    "params": {}
  }'
```

요청 본문:

| 필드         | 타입   | 설명             |
| ------------ | ------ | ---------------- |
| `skill_name` | string | 실행할 스킬 이름 |
| `params`     | object | 스킬 파라미터    |

## 오류 코드

| 코드  | 의미                               |
| ----- | ---------------------------------- |
| `400` | JSON 형식 오류 또는 필수 필드 누락 |
| `401` | 토큰 누락 또는 권한 부족           |
| `403` | 허용되지 않은 IP 또는 차단된 스킬  |
| `404` | 알 수 없는 경로                    |
| `429` | rate limit 초과                    |

## 운영 권장값

1. 외부망에 열 때는 `RC_HTTP_HOST=0.0.0.0`와 함께 `RC_HTTP_ALLOWED_CIDRS_JSON`과 제어 토큰을 설정합니다.
2. `/task`는 전체 에이전트 실행 경로이므로 신뢰된 네트워크와 제어 토큰에서만 사용합니다.
3. 자동화 스크립트는 `/skill`과 allowlist 조합으로 호출 범위를 제한합니다.
