# 정리 태스크 협업 실험 재실행 가이드

이 문서는 former0047과 Cloid 2호를 대상으로 `'안방 정리해줘'` 협업 시나리오 실험을 다시 실행할 때
따라야 하는 절차를 정리합니다. 지표 정의와 run log 형식은
[docs/CLEANUP_RUN_METRICS.md](CLEANUP_RUN_METRICS.md), 조건 파일과 러너 사용법은
[validation/cleanup-experiment/README.md](../validation/cleanup-experiment/README.md)를 따릅니다.

## 1. 대상 환경

| 구성 요소 | 값 | 비고 |
| --- | --- | --- |
| former0047 | `192.168.50.165` (user `former`) | 에이전트 컨테이너 `robo_claw_container`, RoboMessenger **50052** |
| Cloid 2호(cloid2) | `192.168.50.105` (user `cloi`) | 컨테이너 `cloi-robo-claw`, 피어 서버 **50152**, `RC_AGENT_ID=cloi02` |
| AI Config Server | `http://192.168.50.47:30180` | former0047 프로필 `former`/`kon47`, Cloid 2호 프로필 `cloid`/`cloi02` |
| 동료 등록명 | **`Cloid 2호`** | former0047의 `GRPC_TARGET_PEERS_JSON` `agent_name` 값. 공백 포함 |

동료 등록명은 배포마다 다를 수 있습니다. 러너의 `--expect-peer`에는 실제 등록명을 넣습니다.

## 2. 사전 조건

1. 런타임 설정 컨트랙트 **v2.10.0 이상**이 두 저장소에 반영되어 있어야 합니다
   (`agent.cleanup_run_log_path` / `RC_CLEANUP_RUN_LOG_PATH`).
2. former0047 저장소가 계측 코드를 포함한 커밋 이상이어야 합니다
   (`src/robo_claw_agent/robo_claw_agent/skills/cleanup_run_metrics.py` 존재 여부로 확인).
3. former0047 프로필에 run log 경로가 있어야 합니다.
   관리 화면 `6. 로컬 환경 경로 및 리소스 설정` → `RC_CLEANUP_RUN_LOG_PATH`
   값 예: `/ros2_ws/agent_workspace/cleanup_runs.jsonl`
4. Cloid 2호 프로필에 시연 권한이 있어야 합니다.
   `cloid_cleanup_demo_enabled=true`, `cloid_cleanup_demo_motion_ids_json=[22]`
5. former0047 기억 장소에 `안방` 좌표가 등록되어 있어야 합니다.
   없으면 `tidy_home`이 `requested_location_not_found`로 끝납니다.

## 3. 실행 위치

| 실행물 | 위치 | 이유 |
| --- | --- | --- |
| 측정 러너 | former0047 **호스트**(컨테이너 밖) | gRPC 대상이 `127.0.0.1:50052`이고, run log 파일을 직접 읽어야 합니다 |
| 계측 기록 생성 | former0047 에이전트 컨테이너 | `tidy_home`·위임 결정·실행 결과를 기록 |
| 시연 수신·실행 | Cloid 2호 에이전트 컨테이너 | `cleanup_demo` 실행 |
| 프로필 값 변경 | AI Config Server(관리 API/웹) | 배포 `.env` 생성 경로 |

Cloid 2호에는 코드 배포가 필요하지 않습니다. 설정과 스킬 등록만 확인합니다.

## 4. 사전 점검

### 4.1 AI Config Server와 프로필

```bash
TOKEN=$(cat .admin_token)          # 저장소 루트의 관리자 토큰 파일
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://192.168.50.47:30180/api/v1/configs/active?robot_name=former&environment=kon47" \
  | python3 -c "import json,sys; c=json.load(sys.stdin); print('cleanup_run_log_path =', repr(c['cleanup_run_log_path'])); print('peers =', c['grpc_target_peers_json'])"
```

- `cleanup_run_log_path`가 비어 있지 않아야 합니다.
- `peers`에 `Cloid 2호`와 `capabilities: ["cleanup_demo"]`가 보여야 합니다.

### 4.2 former0047

```bash
ssh former@192.168.50.165
cd ~/workspace/A.RobotAI-Robot-RoboClaw
git log -1 --format='%h %s'
ls src/robo_claw_agent/robo_claw_agent/skills/cleanup_run_metrics.py

# 계측 env와 피어 등록이 컨테이너에 들어갔는지 확인
docker inspect robo_claw_container --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -E '^(RC_CLEANUP_RUN_LOG_PATH|GRPC_TARGET_PEERS_JSON)='
docker logs --tail 40 robo_claw_container 2>&1 | grep -E "AgentNode ready|Connected to peer"
```

`RC_CLEANUP_RUN_LOG_PATH`가 없으면 프로필 값을 바꾼 뒤 에이전트를 재기동해야 합니다.
`docker restart`는 `--env-file`을 다시 읽지 않으므로 **`./rclaw launch former kon47 --docker`로
컨테이너를 다시 만들어야 합니다.**

### 4.3 Cloid 2호

```bash
ssh cloi@192.168.50.105
docker ps --format '{{.Names}}' | grep robo-claw
docker logs --tail 80 cloi-robo-claw 2>&1 | grep "Loaded" | tail -1 | grep -o cleanup_demo
(ss -lntp 2>/dev/null || netstat -lntp 2>/dev/null) | grep 50152
```

`cleanup_demo`가 보이고 50152가 LISTEN이어야 합니다. `cloi` 명령은 cloid2 호스트의
`~/ws/cloi_entropos/scripts/cloi.sh`로 실행합니다(비대화형 셸에서는 `cloi`가 PATH에 없습니다).

### 4.4 러너 준비 (former0047 호스트, 최초 1회)

```bash
cd ~/workspace/A.RobotAI-Robot-RoboClaw
uv venv -q ~/cleanup-exp
~/.local/bin/uv pip install -q --python ~/cleanup-exp/bin/python grpcio grpcio-tools
PATH=$HOME/cleanup-exp/bin:$PATH bash ./validation/talk/gen_proto.sh
```

호스트에 `python3-venv`가 없어 `python3 -m venv`는 실패합니다. `uv venv`를 사용합니다.

## 5. 실행 공통 규칙

- gRPC 채팅 인증: former0047에 `GRPC_PEER_TOKEN`이 설정되어 있으므로 러너에 토큰이 필요합니다.
  컨테이너 env에서 읽어 환경변수로 넘깁니다(값을 출력하지 않습니다).

```bash
export ROBO_CLAW_PEER_TOKEN=$(docker inspect robo_claw_container \
  --format '{{range .Config.Env}}{{println .}}{{end}}' | grep '^GRPC_PEER_TOKEN=' | cut -d= -f2-)
```

- 장시간 실행: SSH 세션이 끊겨도 계속 돌도록 `setsid nohup`으로 분리하고 로그 파일을 폴링합니다.

```bash
RUNLOG=/home/former/workspace/A.RobotAI-Robot-RoboClaw/agent_workspace/cleanup_runs.jsonl
setsid nohup ~/cleanup-exp/bin/python validation/cleanup-experiment/run_cleanup_experiment.py \
  --condition normal --host 127.0.0.1 --port 50052 --run-log "$RUNLOG" \
  --expect-peer "Cloid 2호" --repeat 10 --yes \
  --output /tmp/cleanup-normal-10.md > /tmp/cleanup-normal-10.log 2>&1 < /dev/null &
```

- 진행 확인: `grep -cE "✅|❌" /tmp/cleanup-normal-10.log`, 마지막 줄에 `→ 표 2 행: …`가 출력됩니다.
- 조건별로 run log 파일을 분리해 두면 리포트가 섞이지 않습니다.

```bash
mv "$RUNLOG" "${RUNLOG%.jsonl}.normal10.jsonl"
```

## 6. 조건 1 — 정상 협업 (10회)

1. 사전 점검(4장)을 모두 통과시킵니다.
2. 위 공통 규칙의 명령으로 `--condition normal --repeat 10`을 실행합니다.
3. 기대 결과: 전 회차 `delegated_demo`, 위임 동료 `Cloid 2호`, `cleanup_run.success=true`.

집계:

```bash
python3 scripts/cleanup_run_report.py "$RUNLOG" --expect-peer "Cloid 2호" --markdown
```

## 7. 조건 2 — 기능 제약 (10회)

위임 대상이 없을 때 시스템이 완료를 주장하지 않는지 확인합니다.

1. 관리자 API로 former0047 프로필의 피어 목록을 비웁니다(값 백업 필수).

```bash
curl -s -H "Authorization: Bearer $(cat .admin_token)" \
  "http://192.168.50.47:30180/api/v1/admin/configs/14" > /tmp/cfg14.json
python3 -c "import json;print(json.load(open('/tmp/cfg14.json'))['grpc_target_peers_json'])" > /tmp/peers-backup.json

curl -s -X PUT -H "Authorization: Bearer $(cat .admin_token)" -H 'Content-Type: application/json' \
  -d '{"grpc_target_peers_json":"[]"}' \
  "http://192.168.50.47:30180/api/v1/admin/configs/14"
```

2. 배포 `.env`에서 해당 줄만 바뀌었는지 확인합니다.

```bash
curl -s -H "Authorization: Bearer $(cat .admin_token)" \
  "http://192.168.50.47:30180/api/v1/configs/active/files/.env?robot_name=former&environment=kon47" \
  | grep GRPC_TARGET_PEERS_JSON
```

3. former0047 에이전트를 재기동합니다(`./rclaw launch former kon47 --docker`).
4. `--condition constrained --repeat 10`을 실행합니다.
5. 기대 결과: 전 회차 `unhandled`, `cleanup_handoff.blocked=true`, 위임 준비 0건,
   `기대 동작 준수 10/10`, 전체 성공률 0%.
6. 백업값으로 프로필을 원복하고 다시 재기동합니다.

```bash
python3 - <<'PY'
import json, subprocess, pathlib
token = pathlib.Path('.admin_token').read_text().strip()
peers = pathlib.Path('/tmp/peers-backup.json').read_text().strip()
body = json.dumps({"grpc_target_peers_json": peers})
subprocess.run(["curl", "-s", "-X", "PUT",
  "-H", f"Authorization: Bearer {token}", "-H", "Content-Type: application/json",
  "-d", body, "http://192.168.50.47:30180/api/v1/admin/configs/14"], check=True)
print("프로필 원복 완료")
PY
```

## 8. 조건 3 — 실패 및 복구

### 8.1 실패 유발 (3회)

```bash
# cloid2 호스트
cd ~/ws/cloi_entropos
bash scripts/cloi.sh down -f robo-claw dev -y
```

```bash
# former0047 호스트
setsid nohup ~/cleanup-exp/bin/python validation/cleanup-experiment/run_cleanup_experiment.py \
  --condition recovery --phase fail_injected --host 127.0.0.1 --port 50052 \
  --run-log "$RUNLOG" --repeat 3 --yes \
  --output /tmp/cleanup-recovery-fail-3.md > /tmp/cleanup-recovery-fail-3.log 2>&1 < /dev/null &
```

기대 결과: 전 회차 `unhandled`(피어 미연결 시 요청하지 않는 설계), `기대 동작 준수 3/3`.

### 8.2 복구 (3회)

```bash
# cloid2 호스트 — 컨테이너 내부에서 colcon 빌드를 다시 수행하므로 수 분 소요
cd ~/ws/cloi_entropos
setsid nohup bash scripts/cloi.sh up robo-claw dev > /tmp/cloi-up-robo-claw.log 2>&1 < /dev/null &

# 준비 확인: AgentNode ready, cleanup_demo 등록, 50152 LISTEN, former0047의 peer 연결 재개
docker logs --tail 80 cloi-robo-claw 2>&1 | grep -E "AgentNode ready"
docker logs --tail 80 cloi-robo-claw 2>&1 | grep "Loaded" | tail -1 | grep -o cleanup_demo
```

```bash
# former0047 호스트
setsid nohup ~/cleanup-exp/bin/python validation/cleanup-experiment/run_cleanup_experiment.py \
  --condition recovery --phase recovered --host 127.0.0.1 --port 50052 \
  --run-log "$RUNLOG" --expect-peer "Cloid 2호" --repeat 3 --yes \
  --output /tmp/cleanup-recovery-recovered-3.md > /tmp/cleanup-recovery-recovered-3.log 2>&1 < /dev/null &
```

집계는 `--phase recovered` 회차만 표 2 행으로 계산됩니다.

## 9. 결과 해석 규칙

- **전체 성공률**은 태스크 실행 성공(`cleanup_run.success`) 비율입니다. 조건이 기대한 실패
  (기능 제약)를 올바르게 처리한 비율은 리포트의 `기대 동작 준수`로 따로 봅니다.
- **위임 성공률**은 `cleanup_delegate.success` 비율입니다. 위임 시도가 없으면 `n/a`입니다.
- **시연 성공의 정의**는 "시연 모션 실행 요청까지"입니다. 모션 완료와 물체 이동은 확인되지
  않습니다.
- **`navigate` 구간은 시작 위치에 좌우됩니다.** 회차마다 로봇을 같은 위치로 돌려놓지 않으면
  첫 회차만 이동 시간이 크고 이후 회차는 매우 작게 나옵니다. 조건 간 비교를 하려면 시작 위치를
  고정하거나 첫 회차를 분리해 표기합니다.
- **`delegate` 구간**은 `cleanup_delegate.duration_ms`를 사용합니다. 과거 로그에 실행 레코드의
  단위가 어긋난 값이 있어도 집계 스크립트가 보정합니다.

## 10. 트러블슈팅

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| 러너가 `gRPC 인증 실패`로 실패 | `GRPC_PEER_TOKEN` 설정 배포 | `ROBO_CLAW_PEER_TOKEN` export 후 재실행 |
| 위임이 첫 회차만 `timeout after 60.0 s` | Cloid 2호 재기동 직후 boxing 자세 콜드 스타트 | 워밍업 1회 후 측정, 또는 회차를 늘려 표기 |
| 모든 회차 `unhandled` | 피어 미등록/미연결, 또는 기능 제약 프로필이 원복되지 않음 | `GRPC_TARGET_PEERS_JSON` 확인 후 재기동 |
| 모든 회차 `requested_location_not_found` | 기억 장소에 `안방` 없음 | 장소 등록 후 재실행 |
| `cleanup_run` 레코드가 안 생김 | `RC_CLEANUP_RUN_LOG_PATH` 미주입 또는 경로 권한 | 4.2 확인. 컨테이너 경로(`/ros2_ws/agent_workspace/...`)여야 함 |
| `python3 -m venv` 실패 | Jetson 호스트에 `python3-venv` 없음 | `uv venv` 사용 |
| SSH 세션 종료로 러너가 죽음 | 포그라운드 실행 | `setsid nohup`로 분리하고 로그 폴링 |
| `cloi` 명령을 못 찾음 | 비대화형 셸 PATH | `~/ws/cloi_entropos/scripts/cloi.sh` 전체 경로 사용 |

## 11. 마무리 점검

- [ ] former0047 프로필의 `grpc_target_peers_json`이 원래 값인가
- [ ] former0047 컨테이너에 `RC_CLEANUP_RUN_LOG_PATH`와 정상 피어 목록이 주입되었는가
- [ ] Cloid 2호 컨테이너가 기동 중이고 `cleanup_demo`가 등록되었는가
- [ ] 수집한 run log와 리포트를 실험 기록 폴더로 복사했는가(저장소에는 커밋하지 않음)
- [ ] 기능 제약 조건을 실행했다면 원복 후 에이전트를 재기동했는가

## 12. 2026-10-26 실측 결과 (참고)

원시 로그와 종합 보고서를 저장소에 함께 보관했습니다:
[validation/cleanup-experiment/results/2026-10-26/EXPERIMENT_REPORT.md](../validation/cleanup-experiment/results/2026-10-26/EXPERIMENT_REPORT.md).
수치를 다시 계산하려면 같은 디렉터리에서 `python3 build_report.py`를 실행합니다.

| 실험 조건 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 정상 협업 | 10 | 100.0% | 100.0% | 100.0% | 147.0s | 해당 없음 |
| 기능 제약 | 10 | 0.0% (기대 동작 준수 10/10) | n/a | n/a | 27.0s | 해당 없음 |
| 실패 및 복구 | 3 | 66.7% | 100.0% | 66.7% | 119.0s | n/a |

- 정상 협업 구간 평균: navigate 4.5s / observe 27.1s / discovery 12.4ms / delegate 104.6s
- 복구 3회 중 1회는 Cloid 2호의 boxing 자세 타임아웃(60.0s)으로 실패했고, 이후 회차는 성공했습니다.
