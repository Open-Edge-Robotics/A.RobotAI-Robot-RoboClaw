# RoboClaw 웹 대시보드

로봇 에이전트의 실시간 상태를 웹 브라우저에서 확인하고, 에이전트에 자연어 메시지를 전달하는 전용 웹 노드(`robo_claw_dashboard`) 가이드입니다. HTTP 채널(`robo_claw_channel`)과 별개로 동작하며, 기본 포트는 `9090`입니다.

## 개요

`robo_claw_dashboard`는 ROS2 노드로, 에이전트의 `QueryState`/`ExecuteTask`/`ListPeers` 클라이언트와 `AgentStatus`/`BatteryState` 구독을 취합해 단일 스냅샷으로 제공합니다. 정적 웹 리소스를 서빙하고 `/api/*` 엔드포인트를 제공합니다.

| 구성 요소                   | 파일                                       | 책임                                              |
| --------------------------- | ------------------------------------------ | ------------------------------------------------- |
| 도메인(순수 규칙)           | `domain/`                                  | 상태 매핑, 스냅샷 취합, 배터리 정규화, 지도/위치 정규화, 초기 위치 검증, 경로 검증 |
| 유스케이스                  | `application/`                             | `DashboardService`, HTTP 라우팅                   |
| 어댑터                      | `adapters/`                                | ROS 클라이언트, HTTP 핸들러, 보안 정책             |
| 노드/브라우저 프론트        | `dashboard_node.py`, `web/`               | 파라미터 배선, 지도 캔버스/위치 표시, 실행 설정 패널, 정적 HTML/JS/CSS |

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

설정 서버(AI Config Server) 프로필로 실행하는 경우에는 `.env` 대신 프로필 값을 사용합니다. 대시보드를
켜려면 프로필에서 `dashboard.enabled`(`RC_USE_DASHBOARD`)를 `true`로 두고 `dashboard.host`,
`dashboard.port`를 함께 지정한 뒤 활성화합니다. `rclaw launch`는 이 값을 `use_dashboard`,
`dashboard_host`, `dashboard_port` launch 인자로 변환합니다.

```bash
./rclaw launch former kon47          # 프로필 활성 설정으로 기동
./rclaw launch former kon47 --dry-run  # 적용될 launch 명령만 확인
```

`dashboard.host`/`dashboard.port`만 지정하고 `dashboard.enabled`를 켜지 않으면 노드가 기동하지
않으므로 9190 같은 포트에 아무 응답이 없습니다.

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
| 지도가 표시되지 않음 | 로봇 지도 토픽이 `map_topic` 목록에 없음(예: CLOiD `/lio/grid_map`) | `/api/map`의 `available`/`topic`과 기동 로그의 `map topics=` 줄을 확인합니다 |

`http_allowed_cidrs_json`에 작은따옴표(`['192.168.0.0/24']`)를 쓰면 JSON이 아니므로 해석에
실패합니다. 이때 대시보드는 전체 허용으로 열리지 않고 기본 루프백 제한을 적용하고 오류 로그를
남깁니다. 노출 설정이 위험한 조합이면 기동 시 `[Dashboard]` 오류/경고 로그로 알려 줍니다.

## 엔드포인트

| 경로          | 메서드 | 권한                    | 설명                                          |
| ------------- | ------ | ----------------------- | --------------------------------------------- |
| `/`           | `GET`  | 공개(static)            | 대시보드 HTML                                 |
| `/assets/*`   | `GET`  | 공개(static)            | JS/CSS 정적 리소스                            |
| `/health`     | `GET`  | 공개                    | 노드 상태 확인                                |
| `/api/status` | `GET`  | auth 활성 시 readonly 이상 | 취합된 상태 스냅샷 (JSON, 지도 메타·현재 위치 포함) |
| `/api/map`    | `GET`  | auth 활성 시 readonly 이상 | 최신 OccupancyGrid 셀 데이터(base64)          |
| `/api/config` | `GET`  | auth 활성 시 readonly 이상 | 실행 중인 노드 파라미터(시크릿 마스킹)         |
| `/api/chat`   | `POST` | control                 | 자연어 메시지를 에이전트에 전달 (`ExecuteTask`) |
| `/api/initial_pose` | `POST` | control           | AMCL 초기 위치 설정 (`/initialpose` 발행)     |

토큰은 `Authorization: Bearer <token>` 또는 `X-RoboClaw-Token: <token>` 헤더로 전달합니다. 프론트의 "접근 토큰" 입력란에 저장하면 브라우저가 헤더로 자동 전송합니다.

## 상세 파라미터

| 파라미터(launch 인자)      | 기본값                          | 설명                                          |
| -------------------------- | ------------------------------- | --------------------------------------------- |
| `use_dashboard`            | `false`                         | 노드 실행 여부 (`RC_USE_DASHBOARD`, 계약 `dashboard.enabled`) |
| `dashboard_host`          | `127.0.0.1`                     | 바인드 주소                                   |
| `dashboard_port`          | `9090`                          | HTTP 포트                                    |
| `http_readonly_token`      | `""`                            | `/api/status` 읽기 권한 토큰                  |
| `http_control_token`       | `""`                            | `/api/chat` 제어 권한 토큰                    |
| `http_allowed_cidrs_json`  | `["127.0.0.1/32","::1/128"]`   | 허용 클라이언트 CIDR                          |
| `http_rate_limit_per_minute` | `60`                          | 분당 요청 제한                                |
| `http_blocked_skills_json` | `["delete_file","emergency_stop",...]` | 차단 위험 스킬                        |

지도/위치 표시에 쓰는 ROS2 노드 파라미터입니다. launch 인자는 아니며 노드 파라미터로만 지정합니다.

| 노드 파라미터        | 기본값        | 설명                                              |
| -------------------- | ------------- | ------------------------------------------------- |
| `map_topic`         | `/map,/lio/grid_map` | OccupancyGrid 토픽 목록(쉼표 구분, transient local 구독) |
| `map_frame`         | `map`         | 지도/위치 기준 프레임                              |
| `base_frame`        | `base_link`   | 로봇 기준 프레임 (map→base TF)                     |
| `initialpose_topic` | `/initialpose`| 초기 위치 발행 토픽                                |
| `config_query_timeout_sec` | `2.0`  | 실행 설정 조회 타임아웃(초)                        |

보안 정책은 `robo_claw_channel`의 `HttpSecurityPolicy`를 확장한 대시보드 전용 클래스를 사용합니다. 토큰이 모두 비어 있으면 인증 미활성(공개)이며, 기본 바인드가 `127.0.0.1`이므로 외부 노출 시 반드시 `dashboard_host=0.0.0.0`와 CIDR/토큰을 함께 설정합니다. 외부 바인드 상태에서 CIDR 이 루프백 전용이거나 토큰이 비어 있으면 기동 시 오류 로그로 알립니다.

## 화면 구성과 프론트엔드 의존성

화면은 Bootstrap 5.3.8 CSS를 **패키지에 포함(vendored)** 해서 CDN 없이 동작합니다. 로봇이 외부
인터넷과 끊긴 망에 있어도 접속 PC만 로봇망에 있으면 그대로 표시됩니다.

- `web/assets/vendor/bootstrap.min.css` · `bootstrap.LICENSE` (MIT, 5.3.8)
- `web/assets/map_geometry.js`: 월드(m)↔캔버스(px) 좌표 변환(순수 함수, `node`로 회귀 테스트)
- `web/assets/style.css`: Bootstrap 위에 얹는 최소 오버라이드(지도 캔버스, 설정 표 등)
- `web/assets/app.js`: 외부 JS 의존성 없음(바닐라). Bootstrap JS 번들은 쓰지 않습니다.

레이아웃은 상단 고정 헤더(로봇/에이전트 ID, 상태 배지, 연결 상태, 갱신 시각)와 2단 본문으로
구성됩니다. 넓은 화면에서는 지도(8/12)와 상태 카드(4/12)가 나란히 놓이고, 980 px 이하에서는
한 단으로 접힙니다. 접근 토큰 입력은 접이식 카드로 숨겨 기본 화면을 가리지 않습니다.

## 실행 설정 패널

"실행 설정" 카드는 지금 구동 중인 노드의 ROS2 파라미터를 그대로 보여줍니다. 값은 launch
파라미터 파일과 `rclaw launch`가 넘긴 인자가 반영된 **실제 적용값**입니다.

- 에이전트 노드(`agent_name`, 기본 `robo_claw_agent_node`)의 파라미터를 `/list_parameters`,
  `/get_parameters` 서비스로 조회합니다.
- 대시보드 자신의 파라미터도 같은 형식으로 함께 표시합니다.
- 이름에 `token`, `secret`, `password`, `api_key` 등이 들어간 값은 `***`로 마스킹합니다.
  빈 값은 마스킹하지 않고 빈 칸으로 두어 "미설정"을 구분합니다.
- 긴 문자열(160자 초과)과 배열(20개 초과)은 화면 표시용으로 줄입니다.
- 조회 결과는 5초 캐시합니다. `/api/status` 폴링이 파라미터 서비스 지연에 영향받지 않습니다.
- 에이전트 노드가 없거나 파라미터 서비스가 응답하지 않으면 해당 영역만 "응답 없음"으로
  표시하고 나머지 화면은 정상 동작합니다.

```bash
curl -s http://127.0.0.1:9090/api/config | jq '.counts, (.agent.parameters[] | select(.secret))'
```

## 지도와 현재 위치

대시보드는 `map_topic`으로 받은 OccupancyGrid와 `map_frame`→`base_frame` TF를 사용해 지도와 로봇
위치를 같은 화면에 표시합니다. `/api/status`의 `localization`에는 위치(`pose`)와 지도 메타(`map`)만
담고, 셀 데이터는 `/api/map`으로 분리해 전송합니다. 지도는 `stamp_ns`가 바뀔 때만 다시 받으므로
2초 폴링 부하가 지도 크기에 비례해 커지지 않습니다.

```bash
# 현재 위치 스냅샷
curl -s http://127.0.0.1:9090/api/status | jq '.localization'

# 지도 셀 데이터 (base64 uint8, 255 = 미확인 셀)
curl -s http://127.0.0.1:9090/api/map | jq '{available, width, height, resolution, origin}'
```

로봇마다 지도 토픽이 다릅니다. former0047 계열은 `/map`(map_server), CLOiD는 `/lio/grid_map`
(grid_map_node, transient local)을 발행합니다. `map_topic`에 쉼표로 여러 토픽을 지정하면
**가장 최근에 도착한 지도**를 사용하므로 기본값 `"/map,/lio/grid_map"` 하나로 두 로봇 모두
표시됩니다. 어느 토픽을 쓰는지는 `/api/map`의 `topic`과 화면 "지도" 항목에 표시됩니다.

```bash
# CLOiD 처럼 지도 토픽이 다른 경우(노드 파라미터로만 지정)
ros2 param get /robo_claw_dashboard_node map_topic
ros2 topic info -v /lio/grid_map   # Durability: TRANSIENT_LOCAL 확인
```

지도가 아직 없으면 `available: false`이고, 이때 화면은 "지도 미수신"으로 표시합니다. TF를 못 찾으면
위치만 `available: false`가 되고 지도는 계속 표시됩니다.

로봇 마커 좌표는 `resolution`(m/cell)과 무관하게 `px/m` 스케일로 계산합니다. 해상도를 두 번 곱하면
(cell 인덱스 × px/m) 마커가 캔버스 밖으로 나가므로, `tests/test_map_geometry.py`가 실좌표로
캔버스 내부 여부와 왕복 변환을 검증합니다.

## 초기 위치 설정

`/api/initial_pose`는 AMCL 초기 위치(`/initialpose`, `PoseWithCovarianceStamped`)를 발행합니다.
`control` 권한이 필요합니다.

```bash
curl -X POST http://127.0.0.1:9090/api/initial_pose \
  -H "X-RoboClaw-Token: $RC_HTTP_CONTROL_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"x": 1.25, "y": -0.40, "yaw": 1.57}'
```

화면에서는 "초기 위치 설정"을 누른 뒤 지도에서 위치를 클릭하고, 누른 채로 끌어 방향을 지정합니다.
확인 대화상자에서 승인해야 전송됩니다.

안전을 위해 다음 조건을 발행 전에 검사하고, 하나라도 어긋나면 400/거부 응답으로 되돌립니다.

- `x`, `y`는 유한한 수여야 하고 절대값 1000 m를 넘을 수 없습니다(`yaw` 생략 시 0).
- 지도를 수신한 상태여야 하며, 좌표는 지도 경계 + 여유 1 m 안에 있어야 합니다.
- 공분산은 `robo_claw_agent`의 `set_initial_pose` 스킬과 같은 값(0.05)을 사용합니다.
- `/initialpose` 구독자가 없으면 최대 1초 기다린 뒤 그대로 발행합니다(AMCL 미기동 시 위치가
  반영되지 않을 수 있음).

초기 위치는 로봇을 직접 움직이지 않지만, AMCL 파티클을 재배치하므로 잘못된 값은 이후 주행을
잘못된 곳으로 유도합니다. 원격 노출 시 `http_control_token`을 반드시 설정하고, 주행 중에는
초기 위치를 함부로 바꾸지 않습니다.

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
- `/api/initial_pose`도 `control` 권한이 필요합니다. 지도 미수신, 좌표 범위 초과, 비유한 값은 발행 전에 거부합니다.
- 자연어 명령이 차단 스킬을 간접 유발하지 않도록 에이전트 폴리시 설정과 함께 운영 시 검증합니다.
- 외부 웹 노출 전에는 시뮬레이션에서 토큰 무단 접근 차단, CIDR 차단, rate-limit, 경로 traversal 거부를 확인하세요.

## 검증

관련 테스트는 `src/robo_claw_dashboard/tests/`에 있으며 도메인 단위/컴포넌트/fake backend 기반으로 ROS 없이 실행됩니다.

```bash
task unit-test -- -k "dashboard or builder or validation or telemetry or paths or router or handlers or service or exposure or localization or config_view"
```

지도/위치/초기 위치 규칙은 `tests/test_localization.py`(도메인)와 `test_service.py`,
`test_http_router.py`, `test_http_handlers.py`(권한 포함)에서 검증합니다.

`tests/test_exposure.py`는 바인드 주소·CIDR·토큰 조합별로 로컬/원격 접속 가능 여부와 잘못된
CIDR JSON 처리까지 검증합니다.
