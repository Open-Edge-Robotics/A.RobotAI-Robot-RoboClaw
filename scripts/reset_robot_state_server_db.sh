#!/usr/bin/env bash
#
# reset_robot_state_server_db.sh — 로봇 상태 + 벡터 DB 완전 초기화 (호스트에서 SSH로 실행)
# ---------------------------------------------------------------------------
# scripts/reset_memory.sh (로봇에서 실행, Qdrant까지 삭제) 와
# scripts/reset_robot_state.sh (호스트에서 SSH, config 캐시 + 메모리 삭제) 를 합친 것.
# 호스트 한 곳에서 아래 3가지를 순서대로 처리한다.
#
#   1) 컨테이너 정지   — 먼저 멈춰야 한다. rag_local_mirror=true 면 DualVectorStore 의
#                        백그라운드 reconcile 스레드가 로컬 미러 ↔ Qdrant 를 "합집합 머지"
#                        하므로, 도는 상태에서 한쪽만 지우면 다른 쪽에서 되살아난다.
#   2) 로봇 파일 삭제  — config 캐시(soul/skill/limits/troubleshooting) + 에이전트 메모리
#                        (memory.kb.json 로컬 미러 / memory.semantic.json 시맨틱 맵 /
#                         memory.cold.db 콜드 아카이브)
#   3) Qdrant 컬렉션 삭제 — HTTP DELETE. 호스트에서 시도하고 실패 시 로봇 경유로 재시도.
#
# 사용법:
#   ./reset_robot_state_server_db.sh                       # 값 입력받아 실행
#   ./reset_robot_state_server_db.sh -y                    # 확인 없이 (기본값 사용)
#   ./reset_robot_state_server_db.sh --dry-run             # 실행될 명령만 출력
#   ./reset_robot_state_server_db.sh --restart             # 초기화 후 컨테이너 재시작
#   ./reset_robot_state_server_db.sh \
#       --robot former@10.159.172.69 \
#       --qdrant-url http://10.159.172.74:6333 \
#       --collection robo_claw_former_0045_w2_2f -y
#   # 위치 인자(순서: qdrant-url, collection, robot)도 허용
#   ./reset_robot_state_server_db.sh http://10.159.172.74:6333 robo_claw_former_0045_w2_2f former@10.159.172.69 -y
#
# 옵션:
#   --robot USER@IP     로봇 SSH 대상            (env ROBOT,      기본 former@10.159.172.69)
#   --qdrant-url URL    Qdrant 주소              (env QDRANT_URL, 기본 http://10.159.172.74:6333)
#   --collection NAME   Qdrant 컬렉션명          (env COLLECTION, 기본 robo_claw_former_0045_w2_2f)
#   --container NAME    에이전트 컨테이너명      (env CONTAINER,  기본 robo_claw_container)
#   --memory-dir PATH   메모리 경로 (미지정 시 docker inspect 로 자동 탐지)
#   --config-cache PATH config 캐시 경로         (env CONFIG_CACHE, 기본 ~/.robo_claw/config_cache)
#   --skip-config | --skip-memory | --skip-qdrant   해당 단계 생략
#   --restart           초기화 후 컨테이너 재시작
#   -y|--yes            확인 프롬프트 생략        --dry-run   삭제 없이 계획만 출력
#
set -uo pipefail

ROBOT="${ROBOT:-}"
QDRANT_URL="${QDRANT_URL:-}"
COLLECTION="${COLLECTION:-}"
CONTAINER="${CONTAINER:-robo_claw_container}"
CONFIG_CACHE="${CONFIG_CACHE:-~/.robo_claw/config_cache}"
MEMORY_DIR="${MEMORY_DIR:-}"

DEFAULT_ROBOT="former@10.159.172.69"
DEFAULT_QDRANT="http://10.159.172.74:6333"
DEFAULT_COLLECTION="robo_claw_former_0045_w2_2f"
DEFAULT_MEMORY_DIR="~/workspace/robo-claw/robo-claw/agent_workspace/memory"

ASSUME_YES=0; DRY_RUN=0; RESTART=0
SKIP_CONFIG=0; SKIP_MEMORY=0; SKIP_QDRANT=0
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --robot)        ROBOT="${2:-}"; shift ;;
    --qdrant-url)   QDRANT_URL="${2:-}"; shift ;;
    --collection)   COLLECTION="${2:-}"; shift ;;
    --container)    CONTAINER="${2:-}"; shift ;;
    --memory-dir)   MEMORY_DIR="${2:-}"; shift ;;
    --config-cache) CONFIG_CACHE="${2:-}"; shift ;;
    --skip-config)  SKIP_CONFIG=1 ;;
    --skip-memory)  SKIP_MEMORY=1 ;;
    --skip-qdrant)  SKIP_QDRANT=1 ;;
    --restart)      RESTART=1 ;;
    -y|--yes)       ASSUME_YES=1 ;;
    --dry-run)      DRY_RUN=1 ;;
    -h|--help)      sed -n '2,52p' "$0" | sed -e 's/^# \{0,1\}//'; exit 0 ;;
    -*)             echo "알 수 없는 옵션: $1" >&2; exit 2 ;;
    *)              POSITIONAL+=("$1") ;;
  esac
  shift
done

# 위치 인자(순서: qdrant-url, collection, robot) — 플래그가 없을 때만 채운다.
[[ -z "$QDRANT_URL" && ${#POSITIONAL[@]} -ge 1 ]] && QDRANT_URL="${POSITIONAL[0]}"
[[ -z "$COLLECTION" && ${#POSITIONAL[@]} -ge 2 ]] && COLLECTION="${POSITIONAL[1]}"
[[ -z "$ROBOT"      && ${#POSITIONAL[@]} -ge 3 ]] && ROBOT="${POSITIONAL[2]}"

# 비어 있으면 입력받는다(비대화형이면 기본값 사용).
_ask() {  # $1=현재값  $2=프롬프트  $3=기본값 → stdout 에 결정된 값
  local cur="$1" prompt="$2" def="$3" ans=""
  if [[ -n "$cur" ]]; then printf '%s' "$cur"; return; fi
  if [[ -t 0 ]]; then
    read -r -p "$prompt [$def]: " ans </dev/tty
  fi
  printf '%s' "${ans:-$def}"
}
QDRANT_URL="$(_ask "$QDRANT_URL" "Qdrant URL"        "$DEFAULT_QDRANT")"
COLLECTION="$(_ask "$COLLECTION" "Qdrant collection" "$DEFAULT_COLLECTION")"
ROBOT="$(_ask      "$ROBOT"      "로봇 SSH(user@ip)" "$DEFAULT_ROBOT")"

[[ "$ROBOT" == *@* ]] || { echo "[ERROR] --robot 은 user@ip 형식이어야 합니다: '$ROBOT'" >&2; exit 2; }
[[ "$QDRANT_URL" == http*://* ]] || { echo "[ERROR] --qdrant-url 형식 오류: '$QDRANT_URL'" >&2; exit 2; }
[[ -n "$COLLECTION" ]] || { echo "[ERROR] --collection 이 비어 있습니다." >&2; exit 2; }

SSH=(ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "$ROBOT")

# ── 메모리 경로 자동 탐지 ─────────────────────────────────────────
# 컨테이너의 /ros2_ws/memory 마운트 소스를 쓰면 설정 override 를 그대로 따라간다.
if [[ -z "$MEMORY_DIR" ]]; then
  MEMORY_DIR="$("${SSH[@]}" \
    "docker inspect $CONTAINER --format '{{range .Mounts}}{{if eq .Destination \"/ros2_ws/memory\"}}{{.Source}}{{end}}{{end}}'" \
    2>/dev/null | tr -d '\r')"
  if [[ -n "$MEMORY_DIR" ]]; then
    echo "[INFO] 메모리 경로 자동 탐지(docker mount): $MEMORY_DIR"
  else
    MEMORY_DIR="$DEFAULT_MEMORY_DIR"
    echo "[INFO] 마운트 탐지 실패 → 기본 경로 사용: $MEMORY_DIR"
  fi
fi

# ── 안전 가드: rm 대상이 상위/위험 경로면 중단 ────────────────────
_guard() {
  local p="$1" label="$2"
  case "$p" in
    ""|"~"|"~/"|"/"|"."|".."|'$HOME'|'${HOME}')
      echo "[ABORT] $label 경로가 안전하지 않습니다: '$p'" >&2; exit 3 ;;
  esac
  local trimmed="${p#\~/}"; trimmed="${trimmed#/}"
  if [[ "$trimmed" != */* ]]; then
    echo "[ABORT] $label 경로가 너무 상위입니다(하위 폴더까지 지정): '$p'" >&2; exit 3
  fi
}
[[ "$SKIP_CONFIG" -eq 1 ]] || _guard "$CONFIG_CACHE" "CONFIG_CACHE"
[[ "$SKIP_MEMORY" -eq 1 ]] || _guard "$MEMORY_DIR"  "MEMORY_DIR"

# 메모리는 **디렉터리 자체가 bind mount 소스**다. 디렉터리를 지우면 다음 기동 때
# docker 가 root 소유로 재생성해 권한 문제가 생길 수 있으므로 **내용만** 지운다.
MEM_RM="rm -rf $MEMORY_DIR/memory.* $MEMORY_DIR/places.db*"

echo "==============================================================="
echo " RoboClaw 초기화 (로봇 상태 + 벡터 DB)"
echo "  robot        : $ROBOT"
echo "  container    : $CONTAINER"
echo "  config cache : $CONFIG_CACHE $([[ $SKIP_CONFIG -eq 1 ]] && echo '(생략)')"
echo "  memory dir   : $MEMORY_DIR $([[ $SKIP_MEMORY -eq 1 ]] && echo '(생략)')"
echo "  qdrant       : $QDRANT_URL"
echo "  collection   : $COLLECTION $([[ $SKIP_QDRANT -eq 1 ]] && echo '(생략)')"
[[ "$RESTART" -eq 1 ]] && echo "  restart      : 초기화 후 $CONTAINER 재시작"
echo "==============================================================="
echo "⚠️  로컬 미러·시맨틱 맵·config 캐시·Qdrant 컬렉션을 삭제합니다 (되돌릴 수 없음)."

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] 실행될 명령:"
  echo "  ssh $ROBOT 'docker stop $CONTAINER'"
  [[ "$SKIP_CONFIG" -eq 1 ]] || echo "  ssh $ROBOT 'rm -rf $CONFIG_CACHE'"
  [[ "$SKIP_MEMORY" -eq 1 ]] || echo "  ssh $ROBOT '$MEM_RM'"
  [[ "$SKIP_QDRANT" -eq 1 ]] || echo "  curl -X DELETE $QDRANT_URL/collections/$COLLECTION"
  [[ "$RESTART" -eq 1 ]] && echo "  ssh $ROBOT 'docker restart $CONTAINER'"
  echo "[DRY-RUN] 아무것도 삭제하지 않았습니다."
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "계속할까요? [y/N] " ans </dev/tty
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "취소됨."; exit 0; }
fi

rc=0

# 1) 컨테이너 정지 (reconcile 재푸시 차단)
echo "[1/4] 컨테이너 정지: $CONTAINER"
if "${SSH[@]}" "docker stop $CONTAINER" >/dev/null 2>&1; then
  echo "      정지됨"
else
  echo "      (실행 중 아님 또는 정지 실패 — 계속 진행)"
fi

# 2) config 캐시 삭제
if [[ "$SKIP_CONFIG" -eq 1 ]]; then
  echo "[2/4] config 캐시: 생략"
else
  echo "[2/4] config 캐시 삭제: $CONFIG_CACHE"
  if "${SSH[@]}" "rm -rf $CONFIG_CACHE"; then
    echo "      완료 (다음 launch 시 config 서버에서 재-fetch)"
  else
    echo "      [WARN] 삭제 실패" >&2; rc=1
  fi
fi

# 3) 로컬 메모리(미러/시맨틱/콜드) 삭제
if [[ "$SKIP_MEMORY" -eq 1 ]]; then
  echo "[3/4] 메모리 파일: 생략"
else
  echo "[3/4] 메모리 파일 삭제: $MEMORY_DIR"
  if "${SSH[@]}" "$MEM_RM"; then
    left="$("${SSH[@]}" "ls -1 $MEMORY_DIR 2>/dev/null | head -5" 2>/dev/null | tr -d '\r' | tr '\n' ' ')"
    echo "      완료 (남은 항목: ${left:-없음})"
  else
    echo "      [WARN] 삭제 실패 — 경로 확인: $MEMORY_DIR" >&2; rc=1
  fi
fi

# 4) Qdrant 컬렉션 삭제 — 호스트에서 먼저, 안 되면 로봇 경유
_qdrant_delete() {
  local code
  if command -v curl >/dev/null 2>&1; then
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 15 -X DELETE \
      "$QDRANT_URL/collections/$COLLECTION" 2>/dev/null || true)"
    case "${code:-000}" in
      200|202) echo "      삭제 완료 (호스트에서, HTTP $code)"; return 0 ;;
      000)     echo "      호스트에서 Qdrant 연결 실패 → 로봇 경유로 재시도" ;;
      *)       echo "      [WARN] 호스트 응답 HTTP $code → 로봇 경유로 재시도" ;;
    esac
  else
    echo "      호스트에 curl 없음 → 로봇 경유로 시도"
  fi
  code="$("${SSH[@]}" \
    "curl -s -o /dev/null -w '%{http_code}' -m 15 -X DELETE $QDRANT_URL/collections/$COLLECTION" \
    2>/dev/null | tr -d '\r')"
  case "${code:-000}" in
    200|202) echo "      삭제 완료 (로봇 경유, HTTP $code)"; return 0 ;;
    000)     echo "      [WARN] 로봇에서도 Qdrant 연결 실패 — 주소/네트워크 확인" >&2; return 1 ;;
    *)       echo "      [WARN] 로봇 응답 HTTP $code (이미 없거나 권한 문제일 수 있음)" >&2; return 1 ;;
  esac
}

if [[ "$SKIP_QDRANT" -eq 1 ]]; then
  echo "[4/4] Qdrant 컬렉션: 생략"
else
  echo "[4/4] Qdrant 컬렉션 삭제: $COLLECTION"
  _qdrant_delete || rc=1
  # 검증: 컬렉션이 실제로 사라졌는지 확인(404 기대)
  vcode="$(curl -s -o /dev/null -w '%{http_code}' -m 10 \
    "$QDRANT_URL/collections/$COLLECTION" 2>/dev/null || true)"
  case "${vcode:-000}" in
    404) echo "      검증: 컬렉션 없음 확인 (HTTP 404) ✅" ;;
    200) echo "      [WARN] 검증: 컬렉션이 아직 존재합니다 (HTTP 200)" >&2; rc=1 ;;
    *)   echo "      검증 생략 (호스트에서 조회 불가, HTTP ${vcode:-000})" ;;
  esac
fi

if [[ "$RESTART" -eq 1 ]]; then
  echo "[+] 컨테이너 재시작: $CONTAINER"
  "${SSH[@]}" "docker restart $CONTAINER" >/dev/null 2>&1 \
    && echo "    재시작됨" || { echo "    [WARN] 재시작 실패" >&2; rc=1; }
fi

echo "==============================================================="
if [[ "$rc" -eq 0 ]]; then
  echo " 초기화 완료."
else
  echo " 초기화 완료 (일부 경고 발생 — 위 [WARN] 확인)"
fi
if [[ "$RESTART" -ne 1 ]]; then
  echo " 에이전트를 재기동하세요. 예:"
  echo "   ssh $ROBOT 'docker start $CONTAINER'"
  echo "   또는 robo_claw_cli launch former <env> --docker --image-tag <태그> --use-grpc"
fi
echo " 컬렉션은 첫 저장 시 자동 재생성됩니다(차원은 임베더 기준)."
echo "==============================================================="
exit "$rc"
