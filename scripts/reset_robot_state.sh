#!/usr/bin/env bash
#
# reset_robot_state.sh — 호스트에서 SSH로 로봇의 config 캐시 + 에이전트 메모리를 초기화한다.
# ------------------------------------------------------------------
# 매 테스트 전에 로봇(former@10.159.172.69)에서 수동으로 하던 아래 두 가지를 호스트에서 한 번에 실행:
#   rm -rf ~/.robo_claw/config_cache/                                  # soul/skill/troubleshooting/limits 캐시
#   rm -rf ~/workspace/robo-claw/robo-claw/agent_workspace/memory      # 에이전트 메모리(로컬 벡터/시맨틱)
#
# config 캐시를 지우면 다음 launch 시 config 서버에서 프로파일을 다시 내려받고,
# 메모리를 지우면 위치/지식 기억이 초기화된다.
#
# 사용법:
#   ./reset_robot_state.sh                # 확인 후 실행 (기본 로봇 former@10.159.172.69)
#   ./reset_robot_state.sh -y             # 확인 없이 실행
#   ./reset_robot_state.sh --dry-run      # 실제 삭제 없이 실행될 명령만 출력
#   ./reset_robot_state.sh --restart      # 초기화 후 에이전트 컨테이너 재시작
#   ROBOT=former@10.159.172.69 ./reset_robot_state.sh
#
# 환경변수(기본값):
#   ROBOT=former@10.159.172.69
#   CONFIG_CACHE=~/.robo_claw/config_cache
#   MEMORY_DIR=~/workspace/robo-claw/robo-claw/agent_workspace/memory
#   CONTAINER=robo_claw_container
#
# 주의: Qdrant 컬렉션까지 지우는 더 깊은 초기화는 로봇에서 validation/reset_memory.sh 를 쓰세요.
set -euo pipefail

ROBOT="${ROBOT:-former@10.159.172.69}"
CONFIG_CACHE="${CONFIG_CACHE:-~/.robo_claw/config_cache}"
MEMORY_DIR="${MEMORY_DIR:-~/workspace/robo-claw/robo-claw/agent_workspace/memory}"
CONTAINER="${CONTAINER:-robo_claw_container}"

ASSUME_YES=0
DRY_RUN=0
RESTART=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --restart) RESTART=1 ;;
    --robot) ROBOT="$2"; shift ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "알 수 없는 인자: $1" >&2; exit 2 ;;
  esac
  shift
done

# 안전 가드: rm -rf 대상이 비었거나 위험 경로(~, ~/, /, $HOME 등)이면 중단한다.
_guard() {
  local p="$1" label="$2"
  case "$p" in
    ""|"~"|"~/"|"/"|"."|".."|'$HOME'|'${HOME}')
      echo "[ABORT] $label 경로가 안전하지 않습니다: '$p'" >&2; exit 3 ;;
  esac
  # 경로 깊이가 최소 2단계는 되어야 함(예: ~/.robo_claw/config_cache)
  local trimmed="${p#\~/}"; trimmed="${trimmed#/}"
  if [[ "$trimmed" != */* ]]; then
    echo "[ABORT] $label 경로가 너무 상위입니다(하위 폴더까지 지정 필요): '$p'" >&2; exit 3
  fi
}
_guard "$CONFIG_CACHE" "CONFIG_CACHE"
_guard "$MEMORY_DIR" "MEMORY_DIR"

REMOTE_RM="rm -rf $CONFIG_CACHE $MEMORY_DIR"

echo "==============================================================="
echo " 로봇 상태 초기화 (호스트 → SSH)"
echo "  robot        : $ROBOT"
echo "  config cache : $CONFIG_CACHE"
echo "  memory dir   : $MEMORY_DIR"
[[ "$RESTART" -eq 1 ]] && echo "  restart      : $CONTAINER (초기화 후 재시작)"
echo "  실행 명령    : ssh $ROBOT '$REMOTE_RM'"
echo "==============================================================="

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "[DRY-RUN] 아무것도 삭제하지 않았습니다."
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
  read -r -p "위 경로를 로봇에서 삭제할까요? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "취소됨."; exit 0; }
fi

echo "[*] config 캐시·메모리 삭제..."
# 원격 셸이 ~ 를 확장하도록 경로에 ~ 를 그대로 두고 전달한다(로컬 확장 방지 목적의 변수 사용).
ssh "$ROBOT" "$REMOTE_RM"
echo "    완료: $CONFIG_CACHE, $MEMORY_DIR"

if [[ "$RESTART" -eq 1 ]]; then
  echo "[*] 컨테이너 재시작: $CONTAINER"
  ssh "$ROBOT" "docker restart $CONTAINER" >/dev/null 2>&1 \
    && echo "      재시작됨" \
    || echo "      [WARN] 재시작 실패(컨테이너명 확인: $CONTAINER)"
fi

echo "==============================================================="
echo " 초기화 완료. 다음 launch 시 config가 재-fetch되고 메모리가 비어 시작됩니다."
echo "   (Qdrant 컬렉션까지 지우려면 로봇에서: bash validation/reset_memory.sh -y)"
echo "==============================================================="
