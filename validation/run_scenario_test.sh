#!/usr/bin/env bash
#
# 실로봇 First-Use 시나리오 자동 테스트 러너 (로봇을 실제로 움직이며 검증)
# ------------------------------------------------------------------
# robo_claw_cli 의 시나리오 테스트를 올바른 플래그(특히 --port 50052)로 실행하고,
# 타임스탬프 리포트를 저장한 뒤 PASS/FAIL 요약을 출력한다.
#
# 이 스크립트는 "테스트 클라이언트"만 담당한다. 에이전트 컨테이너 기동(터미널 1)은
# 아래 [사전] 절차로 먼저 띄워 두고, 이 스크립트는 별도 터미널(터미널 2)에서 실행한다.
#
# [사전] 로봇에서 에이전트 기동 (터미널 1):
#   robo_claw_cli launch former w2_2f_2 --docker \
#       --image-tag <배포태그> --pull --use-grpc
#   (개선 이미지가 이미 로봇에 있으면 --pull 생략 가능. --use-grpc 는 ping(tc00)용)
#
# 사용법 (터미널 2):
#   ./run_scenario_test.sh                     # 로봇에서 실행(기본 localhost:50052)
#   HOST=10.159.172.69 ./run_scenario_test.sh  # 원격 PC에서 로봇 IP로 실행
#   ROBOT=former ENV=w2_2f_2 CLI=./robo_claw_cli ./run_scenario_test.sh
#
# 환경변수(기본값):
#   ROBOT=former  ENV=w2_2f_2  HOST=localhost  PORT=50052
#   CLI=robo_claw_cli          # robo_claw_cli 바이너리 경로
#   SCENARIO=<이 스크립트 폴더>/testcases/first-use-scenario-auto.json
#   REPORT=./test-report-<타임스탬프>.md
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ROBOT="${ROBOT:-former}"
ENV="${ENV:-w2_2f_2}"
HOST="${HOST:-localhost}"
PORT="${PORT:-50052}"
if [[ -z "${CLI:-}" ]]; then
  if command -v rclaw >/dev/null 2>&1; then
    CLI="rclaw"
  elif [[ -x "$SCRIPT_DIR/../rclaw" ]]; then
    CLI="$SCRIPT_DIR/../rclaw"
  elif command -v robo_claw_cli >/dev/null 2>&1; then
    CLI="robo_claw_cli"
  elif [[ -x "$SCRIPT_DIR/../robo_claw_cli/robo_claw_cli" ]]; then
    CLI="$SCRIPT_DIR/../robo_claw_cli/robo_claw_cli"
  else
    CLI="rclaw"
  fi
fi
SCENARIO="${SCENARIO:-$SCRIPT_DIR/testcases/first-use-scenario-auto.json}"
REPORT="${REPORT:-./test-report-$(date +%Y%m%d-%H%M%S).md}"

echo "==============================================================="
echo " RoboClaw 실로봇 시나리오 테스트"
echo "  robot=$ROBOT  env=$ENV  target=$HOST:$PORT"
echo "  scenario=$SCENARIO"
echo "  report=$REPORT"
echo "==============================================================="

# CLI 존재 확인
if ! command -v "$CLI" >/dev/null 2>&1 && [[ ! -x "$CLI" ]]; then
  echo "[ERROR] rclaw / robo_claw_cli 를 찾을 수 없습니다: '$CLI'"
  echo "        CLI=<경로> 로 지정하거나 PATH 에 등록하세요. (빌드: task build 또는 cd robo_claw_cli && go build -o robo_claw_cli .)"
  exit 1
fi

# 시나리오 파일 확인
if [[ ! -f "$SCENARIO" ]]; then
  echo "[ERROR] 시나리오 파일이 없습니다: $SCENARIO"
  exit 1
fi

# 사전 연결 점검 (RoboMessenger 50052)
echo "[1/3] 에이전트 연결 점검 ($HOST:$PORT) ..."
if timeout 3 bash -c ">/dev/tcp/$HOST/$PORT" 2>/dev/null; then
  echo "      OK — 포트 열림"
else
  echo "[WARN] $HOST:$PORT 에 연결되지 않습니다."
  echo "       - 터미널 1에서 'robo_claw_cli launch ... --docker' 로 에이전트를 먼저 띄웠는지,"
  echo "       - 자연어 명령 포트는 50052(RoboMessenger)인지 확인하세요."
  echo "       (그래도 시도합니다. 실패하면 여기서 원인을 먼저 해결하세요.)"
fi

# 로봇 초기 위치 안내
echo "[2/3] 로봇 준비 확인 — 맵 w2_2f_2 로드 + 초기 위치(AMCL) 셋업이 완료됐는지 확인하세요."
echo "      시나리오가 실제로 로봇을 이동시킵니다(충전대/거실/키친 왕복). 주변 안전을 확보하세요."

# 테스트 실행
echo "[3/3] 시나리오 실행 중 ... (이동 스텝은 수분 소요)"
set +e
"$CLI" test -r "$ROBOT" -e "$ENV" \
  --host "$HOST" --port "$PORT" \
  --scenario-file "$SCENARIO" \
  --output "$REPORT"
rc=$?
set -e

echo "==============================================================="
if [[ -f "$REPORT" ]]; then
  pass=$(grep -c "✅ PASS" "$REPORT" 2>/dev/null || true)
  fail=$(grep -c "❌ FAIL" "$REPORT" 2>/dev/null || true)
  skip=$(grep -c "➖ SKIP" "$REPORT" 2>/dev/null || true)
  echo " 결과 요약: PASS=$pass  FAIL=$fail  SKIP=$skip"
  echo " 리포트: $REPORT"
  echo
  echo " (참고) tc00(ping)은 50052엔 RosGrpc가 없어 FAIL이 정상입니다."
  echo "        FAIL 스텝의 '실패 사유'에서 걸린 fail_keywords(예: '[스킬', '이동 성공 좌표')를 확인하세요."
else
  echo " 리포트가 생성되지 않았습니다. 연결/기동 상태를 확인하세요."
fi
echo "==============================================================="
exit $rc
