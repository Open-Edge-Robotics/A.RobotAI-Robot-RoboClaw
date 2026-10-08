#!/usr/bin/env bash
# Laya 서버 단독 검증: 라이브 테스트(pytest) + 라우터 평가(system1_eval.py).
#
#   ./validation/system1/run_laya_tests.sh                      # 기본: http://192.168.50.212:8000, 저장소 .env 의 LAYA_API_KEY
#   LAYA_ENDPOINT=http://127.0.0.1:8000 ./validation/system1/run_laya_tests.sh
#   ./validation/system1/run_laya_tests.sh -k latency           # pytest 인자 전달
#
# API 키는 LAYA_API_KEY/SYSTEM1_API_KEY 환경변수 또는 LAYA_ENV_FILE(기본 저장소 루트 .env)에서 읽으며 출력하지 않는다.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${ROOT}"

export LAYA_ENDPOINT="${LAYA_ENDPOINT:-http://192.168.50.212:8000}"
export LAYA_ENV_FILE="${LAYA_ENV_FILE:-${ROOT}/.env}"
REPORT_DIR="${LAYA_REPORT_DIR:-${TMPDIR:-/tmp}/laya_reports/$(date +%Y%m%d_%H%M%S)}"
export LAYA_REPORT_DIR="${REPORT_DIR}"
mkdir -p "${REPORT_DIR}"
PYTHON="${PYTHON:-python3}"

if ! "${PYTHON}" -m pytest --version >/dev/null 2>&1; then
  echo "pytest 가 없습니다: ${PYTHON} -m pip install pytest" >&2
  exit 1
fi

echo "== Laya endpoint : ${LAYA_ENDPOINT}"
echo "== env file      : ${LAYA_ENV_FILE} $([[ -f "${LAYA_ENV_FILE}" ]] && echo '(found)' || echo '(not found)')"
echo "== report dir    : ${REPORT_DIR}"

status=0
echo
echo "== 1/2 live tests (validation/system1/test_laya_live.py)"
"${PYTHON}" -m pytest -s -rA -p no:cacheprovider -o addopts="" \
  validation/system1/test_laya_live.py "$@" || status=$?

echo
echo "== 2/2 router evaluation (rule vs laya, readonly)"
"${PYTHON}" scripts/system1_eval.py --router both \
  --endpoint "${LAYA_ENDPOINT}" --env-file "${LAYA_ENV_FILE}" \
  --timeout-ms "${LAYA_TIMEOUT_MS:-5000}" \
  --json-out "${REPORT_DIR}/system1_eval.json" || status=$?

echo
echo "reports: ${REPORT_DIR}"
exit "${status}"
