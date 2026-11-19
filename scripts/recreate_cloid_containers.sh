#!/usr/bin/env bash
#
# recreate_cloid_containers.sh — cloid1/cloid2에서 비대해진 Bazel 기반 컨테이너를 재생성한다.
# ------------------------------------------------------------------
# cloi-secure-sys-bridge와 cloi-robot-hub는 Bazel distroless(py_binary) 구조로
# 재부팅/재시작마다 /tmp/Bazel.runfiles_* 가 writable layer에 누적된다.
# 이 스크립트는 SSH로 각 로봇에 접속해 `cloi.sh down` -> `cloi.sh up` 으로
# 두 컨테이너만 안전하게 재생성하고 전후 디스크/컨테이너 용량을 출력한다.
#
# 사용법:
#   ./recreate_cloid_containers.sh                          # 확인 후 cloid1, cloid2 모두 실행
#   ./recreate_cloid_containers.sh -y                       # 확인 없이 실행
#   ./recreate_cloid_containers.sh --dry-run                # 실행될 명령만 출력 (원격 변경 없음)
#   ./recreate_cloid_containers.sh --host cloi@192.168.50.131 -y  # 특정 로봇만 실행
#   ./recreate_cloid_containers.sh --host cloid01 --host cloid02 -y
#
# 환경변수(기본값):
#   CLOID1_HOST=cloi@192.168.50.131
#   CLOID2_HOST=cloi@192.168.50.106
#   CLOI_SCRIPT=/home/cloi/ws/cloi_entropos/scripts/cloi.sh
#   SERVICES="secure-sys-bridge robot-hub"
#   SSH_CONNECT_TIMEOUT=10
set -euo pipefail

CLOID1_HOST="${CLOID1_HOST:-cloi@192.168.50.131}"
CLOID2_HOST="${CLOID2_HOST:-cloi@192.168.50.106}"
CLOI_SCRIPT="${CLOI_SCRIPT:-/home/cloi/ws/cloi_entropos/scripts/cloi.sh}"
SERVICES="${SERVICES:-secure-sys-bridge robot-hub}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"

ASSUME_YES=0
NO_INPUT=0
DRY_RUN=0
HOSTS=()

print_help() {
  sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -y|--yes) ASSUME_YES=1 ;;
    --no-input) NO_INPUT=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --host)
      if [[ $# -lt 2 || -z "$2" ]]; then
        echo "오류: --host 옵션에는 대상 호스트가 필요합니다." >&2
        exit 2
      fi
      HOSTS+=("$2")
      shift
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "알 수 없는 인자: $1" >&2
      echo "사용법 확인: $0 --help" >&2
      exit 2
      ;;
  esac
  shift
done

if [[ ${#HOSTS[@]} -eq 0 ]]; then
  HOSTS=("$CLOID1_HOST" "$CLOID2_HOST")
fi

# shellcheck disable=SC2206
SERVICE_ARRAY=($SERVICES)
if [[ ${#SERVICE_ARRAY[@]} -eq 0 ]]; then
  echo "오류: SERVICES가 비어 있습니다." >&2
  exit 2
fi

if [[ "$DRY_RUN" -eq 0 && "$ASSUME_YES" -eq 0 ]]; then
  if [[ "$NO_INPUT" -eq 1 || ! -t 0 ]]; then
    echo "오류: 비대화형 환경에서는 -y(--yes) 옵션이 필요합니다." >&2
    exit 2
  fi
  echo "대상 호스트 : ${HOSTS[*]}"
  echo "대상 서비스 : ${SERVICE_ARRAY[*]}"
  echo "실행 작업   : cloi.sh down <services> -y -> cloi.sh up <services>"
  read -r -p "계속 진행할까요? [y/N] " reply
  if [[ ! "$reply" =~ ^[Yy]$ ]]; then
    echo "취소되었습니다."
    exit 0
  fi
fi

ssh_exec() {
  local host="$1"
  local remote_cmd="$2"
  ssh -o BatchMode=yes -o "ConnectTimeout=${SSH_CONNECT_TIMEOUT}" "$host" "$remote_cmd"
}

build_filter_args() {
  local args=""
  local svc
  for svc in "${SERVICE_ARRAY[@]}"; do
    args+=" --filter name=^/cloi-${svc}$"
  done
  printf '%s' "$args"
}

FILTER_ARGS="$(build_filter_args)"
SERVICES_STR="${SERVICE_ARRAY[*]}"
FAILED_HOSTS=()

for host in "${HOSTS[@]}"; do
  echo "============================================================"
  echo "[대상] ${host}"
  echo "============================================================"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "[DRY-RUN] ssh ${host} '${CLOI_SCRIPT} down ${SERVICES_STR} -y'"
    echo "[DRY-RUN] ssh ${host} '${CLOI_SCRIPT} up ${SERVICES_STR}'"
    continue
  fi

  echo "--- [재생성 전 상태] ---"
  if ! ssh_exec "$host" "df -h /home/cloi/ws; docker ps -s ${FILTER_ARGS} --format 'table {{.Names}}\t{{.Status}}\t{{.Size}}'"; then
    echo "[오류] ${host} 접속 또는 상태 확인 실패 — 다음 호스트로 넘어갑니다." >&2
    FAILED_HOSTS+=("$host")
    continue
  fi

  echo "--- [1/2] 컨테이너 중지 및 제거 (cloi down) ---"
  if ! ssh_exec "$host" "${CLOI_SCRIPT} down ${SERVICES_STR} -y"; then
    echo "[오류] ${host}에서 cloi down 실패" >&2
    FAILED_HOSTS+=("$host")
    continue
  fi

  echo "--- [2/2] 컨테이너 재생성 및 기동 (cloi up) ---"
  if ! ssh_exec "$host" "${CLOI_SCRIPT} up ${SERVICES_STR}"; then
    echo "[오류] ${host}에서 cloi up 실패" >&2
    FAILED_HOSTS+=("$host")
    continue
  fi

  echo "--- [재생성 후 상태] ---"
  ssh_exec "$host" "df -h /home/cloi/ws; docker ps -s ${FILTER_ARGS} --format 'table {{.Names}}\t{{.Status}}\t{{.Size}}'" || true
done

echo "============================================================"
if [[ ${#FAILED_HOSTS[@]} -gt 0 ]]; then
  echo "[완료 - 일부 실패] 실패 호스트: ${FAILED_HOSTS[*]}" >&2
  exit 1
fi
echo "[완료] 모든 대상 호스트 처리 완료: ${HOSTS[*]}"
