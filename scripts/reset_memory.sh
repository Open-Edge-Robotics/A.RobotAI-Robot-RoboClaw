#!/usr/bin/env bash
#
# RoboClaw 메모리 완전 초기화 — 로컬 미러 + 시맨틱 맵 + Qdrant 컬렉션을 함께 지운다.
# ------------------------------------------------------------------
# 왜 필요한가: rag_local_mirror=true 이면 DualVectorStore(Qdrant primary + 로컬 미러)를
# 쓰고, 백그라운드 reconcile 스레드가 양쪽을 "합집합 머지"한다. 따라서 Qdrant 컬렉션만
# 지우면 로컬 미러(memory.kb.json)에서 원래 timestamp 그대로 다시 복원된다.
# 진짜 초기화하려면 컨테이너를 먼저 멈추고(재푸시 차단) 양쪽을 모두 지워야 한다.
#
# 이 스크립트는 로봇 호스트(에이전트 컨테이너가 도는 곳)에서 실행하는 것을 기준으로 한다.
#
# 사용법:
#   ./reset_memory.sh                 # 확인 프롬프트 후 초기화
#   ./reset_memory.sh -y              # 확인 없이 실행
#   COLLECTION=robo_claw_former_w2_2f_2 QDRANT_URL=http://10.159.172.74:6333 ./reset_memory.sh
#
# 환경변수(기본값):
#   CONTAINER=robo_claw_container
#   MEMORY_DIR=<robo-claw>/agent_workspace/memory   (RC_MEMORY_DIR 있으면 그 값)
#   QDRANT_URL=http://10.159.172.74:6333
#   COLLECTION=robo_claw_former_w2_2f_2
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONTAINER="${CONTAINER:-robo_claw_container}"
QDRANT_URL="${QDRANT_URL:-http://10.159.172.74:6333}"
COLLECTION="${COLLECTION:-robo_claw_former_0045_w2_2f}"
MEMORY_DIR="${MEMORY_DIR:-${RC_MEMORY_DIR:-$REPO_ROOT/agent_workspace/memory}}"

ASSUME_YES=0
[[ "${1:-}" == "-y" || "${1:-}" == "--yes" ]] && ASSUME_YES=1

# 컨테이너가 떠 있으면 실제 마운트 경로를 우선 사용(설정 override 대비)
if docker inspect "$CONTAINER" >/dev/null 2>&1; then
  MNT="$(docker inspect "$CONTAINER" \
    --format '{{range .Mounts}}{{if eq .Destination "/ros2_ws/memory"}}{{.Source}}{{end}}{{end}}' 2>/dev/null || true)"
  [[ -n "$MNT" ]] && MEMORY_DIR="$MNT"
fi

echo "==============================================================="
echo " RoboClaw 메모리 초기화"
echo "  container  : $CONTAINER"
echo "  memory dir : $MEMORY_DIR   (memory.kb.json / .semantic.json / .cold.db)"
echo "  qdrant     : $QDRANT_URL"
echo "  collection : $COLLECTION"
echo "==============================================================="
echo "⚠️  로컬 미러·시맨틱 맵·Qdrant 컬렉션을 모두 삭제합니다 (되돌릴 수 없음)."

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "계속할까요? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "취소됨."; exit 0; }
fi

# 1) 컨테이너 정지 (reconcile 재푸시 차단). --rm 이면 정지 시 제거됨.
echo "[1/3] 컨테이너 정지: $CONTAINER"
docker stop "$CONTAINER" >/dev/null 2>&1 && echo "      정지됨" || echo "      (실행 중 아님 — 건너뜀)"

# 2) 로컬 미러/시맨틱/콜드 삭제
echo "[2/3] 로컬 메모리 파일 삭제: $MEMORY_DIR"
if [[ -d "$MEMORY_DIR" ]]; then
  removed=0
  for f in memory.kb.json memory.semantic.json memory.cold.db; do
    if [[ -e "$MEMORY_DIR/$f" ]]; then
      rm -f "$MEMORY_DIR/$f" && echo "      - $f 삭제" && removed=1
    fi
  done
  [[ "$removed" -eq 0 ]] && echo "      (삭제할 memory.* 파일 없음)"
else
  echo "      [WARN] 디렉터리 없음: $MEMORY_DIR (MEMORY_DIR 로 지정하세요)"
fi

# 3) Qdrant 컬렉션 삭제
echo "[3/3] Qdrant 컬렉션 삭제: $COLLECTION"
if command -v curl >/dev/null 2>&1; then
  code="$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$QDRANT_URL/collections/$COLLECTION" 2>/dev/null || true)"
  code="${code:-000}"
  case "$code" in
    200|202) echo "      삭제 완료 (HTTP $code)";;
    000)     echo "      [WARN] Qdrant($QDRANT_URL)에 연결 실패 — 네트워크/주소 확인";;
    *)       echo "      [WARN] 예상치 못한 응답 HTTP $code (이미 없거나 권한 문제일 수 있음)";;
  esac
else
  echo "      [WARN] curl 없음 — 수동 삭제: curl -X DELETE $QDRANT_URL/collections/$COLLECTION"
fi

echo "==============================================================="
echo " 초기화 완료. 이제 에이전트를 재기동하세요 (예):"
echo "   robo_claw_cli launch former w2_2f_2 --docker --image-tag <태그> --use-grpc"
echo "==============================================================="
