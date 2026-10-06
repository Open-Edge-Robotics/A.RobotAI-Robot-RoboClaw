#!/usr/bin/env bash
#
# RAG 위치 지식 개선(1~5순위) 회귀 테스트 자동 실행 스크립트.
#
# 로봇/ROS 런타임 없이 순수 파이썬으로 검증한다. 임베딩 서버·Qdrant 서버도
# 필요 없다(더미 임베더 + in-memory 벡터스토어 사용).
#
# 사용법:
#   ./run_rag_tests.sh              # 전체 회귀 테스트 실행
#   ./run_rag_tests.sh -k p4        # 특정 순위만 (예: P4)
#   ./run_rag_tests.sh -v           # 추가 pytest 인자 전달
#
# 필요 도구: uv (https://docs.astral.sh/uv/). 미설치 시 아래 안내 참고.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"      # robo-claw/
AGENT_DIR="$REPO_ROOT/src/robo_claw_agent"

if ! command -v uv >/dev/null 2>&1; then
  echo "[ERROR] 'uv' 가 필요합니다. 설치: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  echo "        또는 수동 실행: PYTHONPATH=$AGENT_DIR python -m pytest $SCRIPT_DIR" >&2
  exit 1
fi

# tests 디렉터리 밖(별도 폴더)에서 실행하므로 패키지 경로를 명시한다.
export PYTHONPATH="$AGENT_DIR:${PYTHONPATH:-}"

# 쓰기 제한 환경(CI/샌드박스)에서는 아래 두 변수로 캐시/venv 위치를 옮길 수 있다.
#   export UV_CACHE_DIR="$TMPDIR/uv-cache"
#   export UV_PROJECT_ENVIRONMENT="$TMPDIR/rc-venv"

echo "[INFO] repo       : $REPO_ROOT"
echo "[INFO] agent pkg  : $AGENT_DIR"
echo "[INFO] test target: $SCRIPT_DIR/test_rag_regression.py"
echo

cd "$REPO_ROOT"
exec uv run --project "$AGENT_DIR" pytest \
  -p no:cacheprovider \
  "$SCRIPT_DIR/test_rag_regression.py" \
  -v "$@"
