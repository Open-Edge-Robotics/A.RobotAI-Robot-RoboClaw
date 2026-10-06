#!/bin/bash
#
# robo_talk 시나리오 실행 래퍼.
#
# 토큰은 환경변수(ROBOCLAW_CONFIG_TOKEN / ADMIN_TOKEN)를 우선 사용하고,
# 없으면 입력을 받는다(입력은 화면에 표시하지 않음 — read -s).
#
# 사용:
#   ./run_robo_talk_template.sh                    # 토큰 입력받아 실행
#   ROBOCLAW_CONFIG_TOKEN=xxx ./run_robo_talk_template.sh
#   HOST=10.159.172.70 ENVIRONMENT=w2_2f_3 ./run_robo_talk_template.sh
#   ./run_robo_talk_template.sh --only tc03,tc07   # 추가 인자는 robo_talk 로 전달
#
set -uo pipefail

# 스크립트 위치로 이동 — 상대 경로(../testcases, robo_talk.py)가 호출 위치와 무관하게 동작.
cd "$(dirname "$(readlink -f "$0")")" || exit 1

HOST="${HOST:-10.159.172.69}"
ENVIRONMENT="${ENVIRONMENT:-0045_w2_2f}"
SCENARIO="${SCENARIO:-../testcases/first-use-scenario-auto.json}"

TOKEN="${ROBOCLAW_CONFIG_TOKEN:-${ADMIN_TOKEN:-}}"
if [[ -z "$TOKEN" ]]; then
  read -r -s -p "please input the token for AI Config Server : " TOKEN
  echo   # read -s 는 개행을 남기지 않으므로 줄바꿈 추가
fi
if [[ -z "$TOKEN" ]]; then
  echo "[ERROR] 토큰이 비어 있습니다. AI Config 헤더 조회가 실패합니다." >&2
  echo "        토큰 없이 진행하려면: python3 robo_talk.py --host $HOST --scenario $SCENARIO --no-config" >&2
  exit 1
fi

if [[ ! -f "$SCENARIO" ]]; then
  echo "[ERROR] 시나리오 파일이 없습니다: $SCENARIO" >&2
  exit 1
fi

OUTPUT="report-$(date +%Y%m%d-%H%M%S).md"

echo "host=$HOST  env=$ENVIRONMENT  output=$OUTPUT"

python3 robo_talk.py --host "$HOST" \
  --scenario "$SCENARIO" \
  --env "$ENVIRONMENT" --config-token "$TOKEN" \
  --output "$OUTPUT" "$@"
rc=$?

# robo_talk 는 FAIL 이 하나라도 있으면 1을 반환한다. 리포트는 어느 쪽이든 생성된다.
if [[ -f "$OUTPUT" ]]; then
  echo "리포트: $(pwd)/$OUTPUT"
fi
exit "$rc"
