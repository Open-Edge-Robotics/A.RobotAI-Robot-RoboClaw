# 2026-10-28 4장소 순환 실험 준비 메모

어제(2026-10-27)에 이어 안방 → 거실 → 부엌 → 신발장 1주기를 단위로 실행합니다.
이번 계획은 1주기 실행 후 휴식, 총 5주기(요청 20회)입니다.

## 실행

```bash
# 전체 5주기 + 주기 사이 600초 휴식 (기본값)
setsid nohup ~/cleanup-experiment/run_cycle4_5cycles_1028.sh 5 600 \
  > ~/cleanup-experiment/2026-10-28-cycle4/logs/run5.log 2>&1 < /dev/null &

# 1주기만 수동 실행 (라벨 2자리)
~/cleanup-experiment/run_cycle4_cycle.sh ~/cleanup-experiment/2026-10-28-cycle4 01
```

진행 확인:

```bash
grep -cE "✅|❌" ~/cleanup-experiment/2026-10-28-cycle4/logs/cycle-01.log
tail -5 ~/cleanup-experiment/2026-10-28-cycle4/progress.log
```

## 결과 집계

```bash
cat ~/cleanup-experiment/2026-10-28-cycle4/runlog/cleanup_runs.cycle-*.jsonl \
  > /tmp/cleanup_runs.1028.jsonl
cd ~/workspace/A.RobotAI-Robot-RoboClaw
python3 scripts/cleanup_run_report.py /tmp/cleanup_runs.1028.jsonl \
  --expect-peer "Cloid 2호" --markdown
python3 scripts/cleanup_run_report.py /tmp/cleanup_runs.1028.jsonl \
  --expect-peer "Cloid 2호" --split-dir ~/cleanup-experiment/2026-10-28-cycle4/reports/by_place --markdown
```

5주기 스크립트는 종료 시 `runlog/cleanup_runs.all.jsonl`, `reports/all.md`,
`reports/by_place.md`를 자동으로 만듭니다.

## 시작 전 확인

1. former0047: `docker ps | grep robo_claw_container`, `RC_CLEANUP_RUN_LOG_PATH` 주입 확인
2. cloid2: `cloi-robo-claw` 기동, 50152 LISTEN, `cleanup_demo` 등록
3. 양쪽 peer 연결: former0047 로그에 `Cloid 2호` 연결 성공, 실패 시 `--- gRPC connection` 재시도 확인
4. 장소 해석: `get_location`으로 안방·거실·주방·신발장·신발 확인 (부엌은 get_location으로 안 나오고
   `tidy_home`이 주방 별칭으로 처리)
5. AMCL 현재 위치가 실제 로봇 위치와 맞는지 확인
