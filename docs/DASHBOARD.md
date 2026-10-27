# RoboClaw 웹 대시보드

로봇 에이전트의 실시간 상태를 웹 브라우저에서 확인하고, 에이전트에 자연어 메시지를 전달하는 전용 웹 노드(`robo_claw_dashboard`) 가이드입니다. HTTP 채널(`robo_claw_channel`)과 별개로 동작하며, 기본 포트는 `9090`입니다.

## 개요

`robo_claw_dashboard`는 ROS2 노드로, 에이전트의 `QueryState`/`ExecuteTask`/`ListPeers` 클라이언트와 `AgentStatus`/`BatteryState` 구독을 취합해 단일 스냅샷으로 제공합니다. 정적 웹 리소스를 서빙하고 `/api/*` 엔드포인트를 제공합니다.

| 구성 요소                   | 파일                                       | 책임                                              |
| --------------------------- | ------------------------------------------ | ------------------------------------------------- |
| 도메인(순수 규칙)           | `domain/`                                  | 상태 매핑, 스냅샷 취합, 배터리 정규화, 경로 검증  |
| 유스케이스                  | `application/`                             | `DashboardService`, HTTP 라우팅                   |
| 어댑터                      | `adapters/`                                | ROS 클라이언트, HTTP 핸들러, 보안 정책             |
| 노드/브라우저 프론트        | `dashboard_node.py`, `web/`               | 파라미터 배선, 정적 HTML/JS/CSS                    |

## 실행 방법

`robo_claw.launch.py`에 `use_dashboard` 인자를 활성화합니다. 외부 노출을 원하면 `dashboard_host`, 그리고 인증을 위해 `http_*` 토큰/CIDR을 함께 설정합니다.

```bash
export RC_USE_DASHBOARD=true               # .env 또는 rclaw run 에서 사용하는 값
```

```bash
ros2 launch robo_claw_bringup robo_claw.launch.py \
  use_dashboard:=true \
  dashboard_host:=127.0.0.1 \
  dashboard_port:=9090 \
  http_readonly_token:=robo-read-token \
  http_control_token:=robo-write-token \
  http_allowed_cidrs_json:='["127.0.0.1/32","192.168.0.0/24"]'
```

브라우저에서 `http://<로봇IP>:<dashboard_port>/` 로 접속합니다.

## 원격 접속

대시보드는 기본값이 로컬 전용입니다. 원격 브라우저에서 접속하려면 아래 세 조건을 모두 만족해야
합니다. 하나라도 빠지면 증상이 서로 다르게 나타납니다.

| 조건 | 파라미터 | 로컬 전용 기본값 | 원격 접속 시 필요 값 |
| ---- | -------- | ---------------- | -------------------- |
| 노드 실행 | `use_dashboard` | `false` | `true` |
| 바인드 주소 | `dashboard_host` | `127.0.0.1` | `0.0.0.0` 또는 로봇 LAN IP |
| 허용 대역 | `http_allowed_cidrs_json` | `["127.0.0.1/32","::1/128"]` | 접속할 PC 대역 포함 (예: `192.168.0.0/24`) |
| 인증(권장) | `http_readonly_token`, `http_control_token` | 빈 값(인증 없음) | 원격 노출 시 필수 |

`.env`로 실행하는 경우 다음처럼 설정한 뒤 `rclaw run`을 사용합니다.

```bash
RC_USE_DASHBOARD=true
RC_DASHBOARD_HOST=0.0.0.0
RC_DASHBOARD_PORT=9090
RC_HTTP_ALLOWED_CIDRS_JSON=["127.0.0.1/32","192.168.0.0/24"]
RC_HTTP_READONLY_TOKEN=<읽기 토큰>
RC_HTTP_CONTROL_TOKEN=<제어 토큰>
```

`ros2 launch`로 직접 실행할 때는 다음 한 줄이면 됩니다.

```bash
ros2 launch robo_claw_bringup robo_claw.launch.py \
  use_dashboard:=true \
  dashboard_host:=0.0.0.0 \
  http_allowed_cidrs_json:='["127.0.0.1/32","192.168.0.0/24"]' \
  http_readonly_token:=<읽기 토큰> \
  http_control_token:=<제어 토큰>
```

기동 로그에서 실제 접속 범위를 확인합니다. 정상적으로 열린 경우 다음 줄이 남습니다.

```text
[Dashboard] listening on http://0.0.0.0:9090 (web=...)
[Dashboard] access scope=remote allowed_cidrs=['127.0.0.1/32', '192.168.0.0/24'] auth=on
```

원격 PC에서 접속을 확인합니다. `403`이면 CIDR, `401`이면 토큰, 응답 없음이면 바인드 주소나
방화벽 문제입니다.

```bash
# 원격 PC에서 실행
curl -i http://<로봇IP>:9090/health
curl -i -H "X-RoboClaw-Token: $RC_HTTP_READONLY_TOKEN" http://<로봇IP>:9090/api/status
```

로봇 호스트에서 리스닝 주소와 방화벽도 함께 확인합니다. `127.0.0.1:9090`으로 보이면 아직
로컬 전용입니다.

```bash
ss -ltnp | grep 9090
sudo ufw status | grep 9090   # ufw 사용 시
```

### 원격 접속이 안 될 때

| 증상 | 원인 | 조치 |
| ---- | ---- | ---- |
| 로컬은 되고 원격만 안 됨 | `dashboard_host=127.0.0.1` | `dashboard_host:=0.0.0.0`으로 변경합니다 |
| `403 허용되지 않은 클라이언트 IP` | CIDR 목록이 루프백 전용 | `http_allowed_cidrs_json`에 접속 PC 대역을 추가합니다 |
| `401 ... 권한 토큰이 필요합니다` | 토큰 미설정 또는 불일치 | 브라우저 "접근 토큰" 입력란에 제어 토큰을 넣습니다 |
| 연결 자체가 거부됨 | 호스트 방화벽 또는 다른 네트워크 | `ufw`, VLAN, 로봇 IP를 확인합니다 |
| `dashboard_host`를 바꿨는데 그대로 | `use_dashboard`가 꺼져 있거나 다른 launch 인자를 사용 | 기동 로그의 `listening on` 줄을 확인합니다 |

`http_allowed_cidrs_json`에 작은따옴표(`['192.168.0.0/24']`)를 쓰면 JSON이 아니므로 해석에
실패합니다. 이때 대시보드는 전체 허용으로 열리지 않고 기본 루프백 제한을 적용하고 오류 로그를
남깁니다. 노출 설정이 위험한 조합이면 기동 시 `[Dashboard]` 오류/경고 로그로 알려 줍니다.

## 엔드포인트

| 경로          | 메서드 | 권한                    | 설명                                          |
| ------------- | ------ | ----------------------- | --------------------------------------------- |
| `/`           | `GET`  | 공개(static)            | 대시보드 HTML                                 |
| `/assets/*`   | `GET`  | 공개(static)            | JS/CSS 정적 리소스                            |
| `/health`     | `GET`  | 공개                    | 노드 상태 확인                                |
| `/api/status` | `GET`  | auth 활성 시 readonly 이상 | 취합된 상태 스냅샷 (JSON)                   |
| `/api/chat`   | `POST` | control                 | 자연어 메시지를 에이전트에 전달 (`ExecuteTask`) |

토큰은 `Authorization: Bearer <token>` 또는 `X-RoboClaw-Token: <token>` 헤더로 전달합니다. 프론트의 "접근 토큰" 입력란에 저장하면 브라우저가 헤더로 자동 전송합니다.

## 상세 파라미터

| 파라미터(launch 인자)      | 기본값                          | 설명                                          |
| -------------------------- | ------------------------------- | --------------------------------------------- |
| `use_dashboard`            | `false`                         | 노드 실행 여부                                |
| `dashboard_host`          | `127.0.0.1`                     | 바인드 주소                                   |
| `dashboard_port`          | `9090`                          | HTTP 포트                                    |
| `http_readonly_token`      | `""`                            | `/api/status` 읽기 권한 토큰                  |
| `http_control_token`       | `""`                            | `/api/chat` 제어 권한 토큰                    |
| `http_allowed_cidrs_json`  | `["127.0.0.1/32","::1/128"]`   | 허용 클라이언트 CIDR                          |
| `http_rate_limit_per_minute` | `60`                          | 분당 요청 제한                                |
| `http_blocked_skills_json` | `["delete_file","emergency_stop",...]` | 차단 위험 스킬                        |

보안 정책은 `robo_claw_channel`의 `HttpSecurityPolicy`를 확장한 대시보드 전용 클래스를 사용합니다. 토큰이 모두 비어 있으면 인증 미활성(공개)이며, 기본 바인드가 `127.0.0.1`이므로 외부 노출 시 반드시 `dashboard_host=0.0.0.0`와 CIDR/토큰을 함께 설정합니다. 외부 바인드 상태에서 CIDR 이 루프백 전용이거나 토큰이 비어 있으면 기동 시 오류 로그로 알립니다.

## 메시지 전송

`/api/chat`은 자연어 메시지를 `ExecuteTask` 액션으로 에이전트에 전달합니다. 응답에 성공 여부, 메시지, 실행된 스킬별 결과(`skill_results`)를 포함합니다.

```bash
curl -X POST http://127.0.0.1:9090/api/chat \
  -H "X-RoboClaw-Token: $RC_HTTP_CONTROL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"현재 배터리 상태 알려줘"}'
```

## 안전 관련

- `/api/chat`은 전체 에이전트 실행 경로이므로 기본적으로 `control` 권한이 필요합니다. `emergency_stop` 등 위험 스킬은 `http_blocked_skills_json` 기본값으로 차단됩니다.
- 자연어 명령이 차단 스킬을 간접 유발하지 않도록 에이전트 폴리시 설정과 함께 운영 시 검증합니다.
- 외부 웹 노출 전에는 시뮬레이션에서 토큰 무단 접근 차단, CIDR 차단, rate-limit, 경로 traversal 거부를 확인하세요.

## 검증

관련 테스트는 `src/robo_claw_dashboard/tests/`에 있으며 도메인 단위/컴포넌트/fake backend 기반으로 ROS 없이 실행됩니다.

```bash
task unit-test -- -k "dashboard or builder or validation or telemetry or paths or router or handlers or service or exposure"
```

`tests/test_exposure.py`는 바인드 주소·CIDR·토큰 조합별로 로컬/원격 접속 가능 여부와 잘못된
CIDR JSON 처리까지 검증합니다.
