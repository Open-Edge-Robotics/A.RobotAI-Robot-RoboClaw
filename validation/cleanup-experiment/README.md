# 정리 태스크 3조건 실험

`'안방 정리해줘'` 요청이 former0047의 `tidy_home` 실행 → 동료 cloid2 위임 → 시연 모션 실행
요청으로 이어지는 흐름을 세 조건으로 반복 측정하는 실험 자산입니다. 장소를 넓힌
`cycle4` 조건(안방 → 거실 → 부엌 → 신발장 한 주기 × 10)도 같은 러너로 실행합니다.
지표 정의와 run log 형식은 [docs/CLEANUP_RUN_METRICS.md](../../docs/CLEANUP_RUN_METRICS.md)를 따릅니다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `conditions/normal.json` | 정상 협업 — 시연 권한이 있는 cloid2에게 위임해 `delegated_demo` 까지 성공 |
| `conditions/constrained.json` | 기능 제약 — cloid2의 시연 능력 선언을 제거해 위임이 차단되고 완료를 주장하지 않음 |
| `conditions/recovery.json` | 실패 및 복구 — cloid2 정지 상태에서 실패(1단계) 후 재기동해 성공(2단계) |
| `conditions/cycle4.json` | 4장소 순환 — 안방 → 거실 → 부엌 → 신발장을 한 주기로 묶어 10주기(40회) 실행 |
| `run_cleanup_experiment.py` | 반복 실행·판정·리포트 생성 러너 |

조건 파일은 `setup.operator_steps`(사람이 준비할 항목)와 `setup.required_env`(로봇별 환경변수),
`phases`(프롬프트·반복 횟수·기대값)로 구성됩니다. `row: true` 인 단계의 회차만 표 2 행으로
집계하고, 나머지 단계는 실패 처리 정확성의 근거로 상세 표에 남깁니다.

## 실행

```bash
# 1) 사전 준비 확인 (로봇에 명령을 보내지 않음)
python3 validation/cleanup-experiment/run_cleanup_experiment.py --dry-run --all

# 2) 조건별 반복 실행. run log 는 에이전트가 기록하는 경로를 지정한다.
python3 validation/cleanup-experiment/run_cleanup_experiment.py \
    --condition normal --host 10.159.172.69 \
    --run-log /workspace/log/cleanup_runs.jsonl \
    --agent-log ~/.ros/log/latest/agent.log \
    --output validation/cleanup-experiment/report-normal.md

# 3) 실패 유발 단계만 (cloid2 중지 상태)
python3 validation/cleanup-experiment/run_cleanup_experiment.py \
    --condition recovery --phase fail_injected --host 10.159.172.69 \
    --run-log /workspace/log/cleanup_runs.jsonl
```

- 실행 전 `setup.operator_steps` 를 출력하고 확인을 받습니다(`--yes` 로 생략).
- 회차마다 gRPC 응답, `cleanup_handoff`·`cleanup_delegate`·`cleanup_run` 레코드, 재계획 발생
  횟수(`--agent-log` 지정 시)를 모아 판정하고 실패 사유를 출력합니다.
- 모든 회차가 기대를 만족하면 종료 코드 0, 하나라도 어긋나면 1입니다.
- `--repeat N` 으로 조건 파일의 반복 횟수를 덮어쓸 수 있고, `--settle-sec` 으로 회차 사이
  대기 시간을 조정합니다.
- `--expect-peer` 로 기대 동료 이름을 덮어쓸 수 있습니다. 동료 이름은 AI Config Server 의
  `agent_name` 을 그대로 쓰므로 배포마다 다릅니다(예: `Cloid 2호`).
- 조건 파일에 `cycle: true` 와 `cycles: N` 이 있으면 phases 를 한 주기로 묶어 N회 반복합니다.
  `cycle4` 는 4개 장소를 1주기로 보므로 `--repeat 1` 은 파일럿 1주기(4회), `--repeat 10` 은
  40회입니다.
- `--run-log` 는 에이전트가 보는 경로입니다. 컨테이너로 띄운 경우 마운트된 경로(예:
  `/ros2_ws/agent_workspace/cleanup_runs.jsonl`)를 주고, 같은 파일을 호스트에서 읽습니다.

## 리포트

`--output` 파일에는 조건별로 다음이 기록됩니다.

1. 표 2 행 — 반복 횟수, 전체 성공률(태스크 실행 성공 비율), 할당 정확도, 위임 성공률,
   평균 시간, 재계획 성공률. 조건이 기대한 실패(기능 제약)를 올바르게 처리한 비율은
   '기대 동작 준수'로 따로 표시합니다
2. 장소별 표 — phase 가 2개 이상인 조건(예: `cycle4`)에서 장소별 반복·성공률·평균 시간을 따로 표시
3. 회차 상세 — 응답, 응답 시간, `run_id`, 장소 status, 위임 동료, 판정과 실패 사유
4. 구간별 평균 — `navigate`, `observe`, `discovery`, `delegate`, `handle`

장소별 JSONL 로 나누려면 집계 스크립트의 `--split-dir` 을 씁니다.

```bash
python3 scripts/cleanup_run_report.py "$RC_CLEANUP_RUN_LOG_PATH" \
    --expect-peer "Cloid 2호" --split-dir /tmp/cleanup-by-place --markdown
```

## 측정 결과

실측 회차의 원시 로그와 종합 보고서는 `results/<날짜>/`에 보관합니다. 재집계는 해당 디렉터리에서
`python3 build_report.py`로 수행합니다.

- [results/2026-10-26/EXPERIMENT_REPORT.md](results/2026-10-26/EXPERIMENT_REPORT.md)

## 주의 사항

- 위임 성공은 `cleanup_delegate` 레코드의 성공 여부입니다. 수신 로봇이 "정리를 완료했다"가
  아닌 거부 문장을 돌려주면서 성공 플래그를 주는 경우가 있으므로, 리포트의 회차 상세에서
  응답과 장소 status 를 함께 확인합니다. 시연 모션의 물리 완료는 확인되지 않습니다.
- 러너는 로봇 상태를 변경하지 않습니다. 컨테이너 중지·재기동과 피어 설정 변경은 사람이
  수행하고, `--dry-run` 으로 무엇을 준비해야 하는지 먼저 확인합니다.
- 재계획 성공률은 `--agent-log` 를 준 경우에만 계산합니다. 주지 않으면 `n/a` 로 남습니다.
