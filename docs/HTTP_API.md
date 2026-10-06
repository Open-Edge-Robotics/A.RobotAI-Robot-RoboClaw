# RoboClaw HTTP API

RoboClaw HTTP 채널은 기본적으로 `127.0.0.1:8080`에 바인딩됩니다. 외부 노출이 필요하면 바인드 주소, 허용 CIDR, 토큰을 명시적으로 설정하세요.

## 실행 예시

```bash
export RC_HTTP_HOST="0.0.0.0"
export RC_HTTP_PORT="8080"
export RC_HTTP_READONLY_TOKEN="robo-read-token"
export RC_HTTP_CONTROL_TOKEN="robo-write-token"
export RC_HTTP_ALLOWED_CIDRS_JSON='["127.0.0.1/32","192.168.0.0/24"]'
export RC_HTTP_RATE_LIMIT_PER_MINUTE="60"
export RC_HTTP_ALLOWED_SKILLS_JSON='["get_status","analyze_scene","get_map_visual"]'
./rclaw run
```

## 인증 규칙

| 경로      | 메서드 | 권한                     |
| --------- | ------ | ------------------------ |
| `/health` | `GET`  | 토큰 없음 가능           |
| `/status` | `GET`  | 읽기 전용 또는 제어 토큰 |
| `/task`   | `POST` | 제어 토큰                |
| `/skill`  | `POST` | 제어 토큰                |

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

기본 차단 스킬:

- `delete_file`
- `emergency_stop`
- `rag_delete`
- `rag_reindex`
- `ros_command`

## 엔드포인트

### `GET /health`

채널 노드와 ROS 클라이언트 readiness, HTTP 보안 설정, 최근 RPC 메트릭을 반환합니다.

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
curl -H "Authorization: Bearer ${RC_HTTP_READONLY_TOKEN}" \
  http://127.0.0.1:8080/status
```

### `POST /task`

자연어 태스크를 에이전트 액션으로 전달합니다.

```bash
curl -X POST http://127.0.0.1:8080/task \
  -H "Authorization: Bearer ${RC_HTTP_CONTROL_TOKEN}" \
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
  -H "Authorization: Bearer ${RC_HTTP_CONTROL_TOKEN}" \
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

1. 외부망에 열 때는 `RC_HTTP_HOST=0.0.0.0`와 함께 반드시 `RC_HTTP_ALLOWED_CIDRS_JSON`을 설정
2. `/task`는 전체 에이전트 실행 경로이므로 신뢰된 네트워크와 제어 토큰에서만 사용
3. 자동화 스크립트는 `/skill` + allowlist 조합으로 좁게 열기
