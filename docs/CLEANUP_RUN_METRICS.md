# 정리 태스크 실험 계측

이 문서는 `tidy_home` 정리 태스크의 성공률, 동료 로봇 할당, 위임 결과, 구간별 소요 시간을
실측하는 절차를 정리합니다. 논문·보고서의 실험 결과 표를 로그 문자열 파싱 없이 재현하는 것이
목적이며, 계측은 정리 동작의 판단을 바꾸지 않고 이미 내려진 결정만 기록합니다.

## 1. 수집 항목과 근거

| 항목 | 산출 방법 |
| --- | --- |
| 반복 횟수 | `cleanup_run` 레코드 수 |
| 전체 성공률 | `success=true` 비율. 방문한 모든 장소가 `handled`·`delegated`·`delegated_demo` 일 때만 성공으로 기록합니다 |
| 할당 정확도 | 차단되지 않은 위임 결정 중 기대 동료(`--expect-peer`)와 일치한 비율 |
| 위임 성공률 | `cleanup_delegate` 레코드의 `success` 비율. 레코드가 없는 이전 로그에서는 `delegated`·`delegated_demo` 상태인 실행의 비율로 대신 계산합니다 |
| 평균 시간 | `phase_ms` 의 구간별 평균(`navigate`, `observe`, `discovery`, `delegate`, `handle`)과 `elapsed_ms` 평균 |
| 재계획 성공률 | 정상 협업에서는 `tidy_home` 이 단일 백그라운드 스킬이라 재계획이 발생하지 않으므로 해당 없음입니다. 실패 및 복구 조건에서만 에이전트 로그(`attempting replanning`)로 계산합니다 |

태스크 분해 구간은 이 계측 범위가 아닙니다. System1 라우터의 `latency_ms`
(`system1_router.py`)와 LLM 계획 구간은 LangSmith 트레이스에서 확인합니다
([docs/LANGSMITH_INTEGRATION.md](LANGSMITH_INTEGRATION.md)).

## 2. 활성화

`RC_CLEANUP_RUN_LOG_PATH` 에 JSONL 경로를 지정합니다. 비어 있으면 기록하지 않습니다.

| 실행 방식 | 설정 위치 |
| --- | --- |
| `./rclaw run` / `ros2 launch` 직접 실행 | 셸에서 `export RC_CLEANUP_RUN_LOG_PATH=...` |
| `scripts/run_robo_claw_docker.sh` | 저장소 루트 `.env`(`--env-file` 로 전달) |
| `./rclaw launch <robot> <env>` | AI Config Server 프로필에 대응 항목이 없으므로 전달되지 않습니다. 실험은 위 두 방식으로 실행합니다 |

컨테이너에서는 마운트된 경로를 지정합니다.

```bash
export RC_CLEANUP_RUN_LOG_PATH=/workspace/log/cleanup_runs.jsonl
./rclaw run
```

## 3. 기록 형식

한 줄에 JSON 1건을 append 합니다. `run_id` 가 실행 1회를 묶는 키입니다.

```json
{"event": "cleanup_handoff", "run_id": "former0047-20261026T165827-0c99c6", "place": "안방", "handoff_kind": "cleanup_demo", "target": "수건", "peer": "cloid2", "transport": "grpc", "send_path": "call_peer_robot", "blocked": false, "candidates": [{"peer": "cloid2", "capabilities": ["cleanup_demo"], "selected": true}], "discovery_ms": 812.4}
{"event": "cleanup_delegate", "run_id": "former0047-20261026T165827-0c99c6", "place": "안방", "peer": "cloid2", "send_path": "call_peer_robot", "success": true, "message": "에이전트 [cloid2] 응답: 정리 시연 모션 실행을 요청했습니다.", "duration_ms": 41220.0}
{"event": "cleanup_run", "run_id": "former0047-20261026T165827-0c99c6", "place_count": 1, "results": [{"place": "안방", "status": "delegated_demo", "partial_observation": false}], "outcomes": {"delegated_demo": 1}, "handled": true, "success": true, "phase_ms": {"navigate": 18210.5, "observe": 9410.2, "discovery": 812.4, "delegate": 41220.0, "handle": 51442.6}, "elapsed_ms": 79065.3, "error": ""}
```

- `cleanup_handoff`: 정리 대상 1건의 위임 결정 시점입니다. 차단된 경우 `blocked=true` 와
  `reason` 이 남습니다. `candidates` 는 설정에 등록된 동료와 `capabilities` 스냅샷이며
  추가 RPC 를 호출하지 않습니다.
- `cleanup_delegate`: 동료에게 보낸 위임 요청 1건의 실행 결과입니다. `success`, 응답
  `message`(수신 로봇의 거부 문장 포함), `duration_ms` 를 남깁니다.
- `cleanup_run`: 정리 실행 1회의 종료 시점입니다. 예외로 중단되면 `error` 에 예외 문자열이
  남고 `success=false` 가 됩니다.
- `run_id` 는 `tidy_home` 스킬 결과(`run_id` 필드)에도 포함되어 요청 1건과 로그를 연결합니다.
- `autonomous_act` 순찰 중 발생한 위임 결정도 `cleanup_handoff` 로 남습니다. 이 경우
  `source=autonomous_patrol` 이고 `run_id` 는 비어 있습니다.

## 4. 집계

```bash
python3 scripts/cleanup_run_report.py "$RC_CLEANUP_RUN_LOG_PATH" \
    --expect-peer cloid2 --markdown
```

`--markdown` 은 실험 결과 표 한 행을 출력합니다. `--json` 은 같은 집계를 JSON 으로,
옵션을 주지 않으면 표를 plain text 로 출력합니다. 파일 경로를 생략하면
`RC_CLEANUP_RUN_LOG_PATH` 를 사용합니다.

```text
| 실험 조건 | 반복 횟수 | 전체 성공률 | 할당 정확도 | 위임 성공률 | 평균 시간 | 재계획 성공률 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 정상 협업 | 3 | 66.7% | 66.7% | 66.7% | 70.3s | 해당 없음 |
```

실험 조건(`정상 협업`·`기능 제약`·`실패 및 복구`)은 실행 조건이 다르므로 조건별로
로그 파일을 분리해 집계합니다.

## 5. 3조건 실험

조건(정상 협업·기능 제약·실패 및 복구)별 반복 실행과 판정은
[validation/cleanup-experiment](../validation/cleanup-experiment/README.md)에 있습니다.
실제 로봇 2대를 대상으로 처음부터 다시 실행하는 절차는
[docs/CLEANUP_EXPERIMENT_RUNBOOK.md](CLEANUP_EXPERIMENT_RUNBOOK.md)를 따릅니다.
`run_cleanup_experiment.py` 가 조건 파일의 프롬프트를 N회 전송하고, 매 회차의 응답과 run log
레코드를 모아 표 2 행을 만듭니다. 실행 전에는 `--dry-run` 으로 사전 준비 항목을 확인합니다.

## 6. 해석 주의 사항

- **시연 성공의 의미**: `delegated_demo` 는 위임 요청이 수락되고 시연 모션 실행 요청까지
  성공했다는 뜻입니다. CLOi MotionCmd 는 완료 응답을 주지 않으므로 모션 완료와 물체 이동은
  확인되지 않습니다([docs/PEER_COLLABORATION.md](PEER_COLLABORATION.md)).
- **`no_target` 은 실패로 집계됩니다**: 방문한 장소에서 정리 대상을 관찰하지 못한 경우입니다.
  관찰 자체가 목적이 아닌 실험에서는 조건을 분리해 해석합니다.
- **판정 재계산**: 원본 `results` 와 `outcomes` 가 그대로 남아 있으므로, 성공 기준을 다르게
  정의하면 `scripts/cleanup_run_report.py` 의 `HANDLED_STATUSES` 대신 자체 집계를 사용합니다.
- **프로세스 재시작**: run log 는 파일 append 방식이라 에이전트가 재시작돼도 이어집니다.
  메모리 기반 `OperationTracker` 스냅샷(`QueryState` 서비스)은 누적값이며 실행 1회 구분이
  없으므로 실험 집계에는 사용하지 않습니다.
