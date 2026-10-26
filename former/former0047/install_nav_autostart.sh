#!/bin/bash
# former0047 nav2 부팅 자동 시작(systemd) 설치/해제 스크립트
#
# former_bringup.service / realsense_bringup.service 와 같은 방식으로, 부팅 시
# former0047_nav 컨테이너를 자동 기동한다. 설치는 root 권한이 필요하다.
#
#   sudo ./install_nav_autostart.sh            # 설치 + 즉시 기동
#   sudo ./install_nav_autostart.sh status     # 상태/로그 확인
#   sudo ./install_nav_autostart.sh stop       # 지금만 정지(자동 시작은 유지)
#   sudo ./install_nav_autostart.sh disable    # 자동 시작 해제 + unit 제거
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
UNIT_NAME="former_nav_bringup.service"
UNIT_TEMPLATE="$DIR/${UNIT_NAME}.in"
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"
ROBOT_HOME="${ROBOT_HOME:-/home/former}"
MAP_FILE="${RC_MAP_FILE:-map_konjiam_L_107-1.yaml}"

usage() {
    printf "Usage: sudo %s [install|status|stop|disable]\n\n" "$0"
    printf "  install   unit 을 설치하고 지금 바로 기동한다 (기본값)\n"
    printf "  status    서비스 상태와 최근 로그를 본다\n"
    printf "  stop      지금만 정지한다 (다음 부팅에는 다시 기동)\n"
    printf "  disable   자동 시작을 해제하고 unit 파일을 제거한다\n"
}

cmd="${1:-install}"

if [ "$cmd" = "-h" ] || [ "$cmd" = "--help" ]; then
    usage
    exit 0
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "root 권한이 필요합니다: sudo $0 $cmd" >&2
    exit 1
fi

case "$cmd" in
    install)
        [ -f "$UNIT_TEMPLATE" ] || { echo "unit 템플릿이 없습니다: $UNIT_TEMPLATE" >&2; exit 1; }
        sed -e "s|__NAV_DIR__|$DIR|g" \
            -e "s|__MAP_FILE__|$MAP_FILE|g" \
            -e "s|__ROBOT_HOME__|$ROBOT_HOME|g" \
            "$UNIT_TEMPLATE" > "$UNIT_PATH"
        chmod 644 "$UNIT_PATH"
        systemctl daemon-reload
        systemctl enable "$UNIT_NAME"
        # 지금 떠 있는 nav 컨테이너는 ExecStartPre 에서 정리되고 다시 뜬다.
        systemctl restart "$UNIT_NAME"
        sleep 5
        systemctl --no-pager --lines=30 status "$UNIT_NAME" || true
        ;;
    status)
        systemctl --no-pager --lines=50 status "$UNIT_NAME" || true
        docker ps --filter "name=former0047_nav" --format '{{.Names}}\t{{.Status}}'
        ;;
    stop)
        systemctl stop "$UNIT_NAME"
        ;;
    disable)
        systemctl disable --now "$UNIT_NAME" 2>/dev/null || true
        rm -f "$UNIT_PATH"
        systemctl daemon-reload
        echo "자동 시작을 해제했습니다: $UNIT_NAME"
        ;;
    *)
        usage
        exit 1
        ;;
esac
