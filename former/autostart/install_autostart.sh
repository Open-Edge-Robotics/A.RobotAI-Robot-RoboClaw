#!/bin/bash
# Former 로봇 부팅 자동 시작(systemd) 설치/해제 스크립트.
#
# 두 개의 unit 을 다룬다.
#   nav   former_nav_bringup.service   Navigation2  (former00XX_nav 컨테이너)
#   claw  roboclaw_bringup.service     RoboClaw 에이전트 (robo_claw_container)
#
#   sudo ./install_autostart.sh former0047 all            # 설치 + 즉시 기동
#   sudo ./install_autostart.sh former0045 nav
#   sudo ./install_autostart.sh former0047 status         # 상태/최근 로그
#   sudo ./install_autostart.sh former0047 stop           # 지금만 정지
#   sudo ./install_autostart.sh former0047 disable        # 자동 시작 해제 + unit 제거
#
# RC_MAP_FILE 로 nav 기동 맵을, RC_NAV_DIR 로 nav 스크립트 디렉토리를,
# RC_WS 로 RoboClaw 워크스페이스를 바꿀 수 있다.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROBOT_HOME="${ROBOT_HOME:-/home/former}"
FORMER_ROOT="${RC_FORMER_ROOT:-$ROBOT_HOME/konjiam/former}"
WORKSPACE="${RC_WS:-$ROBOT_HOME/workspace/A.RobotAI-Robot-RoboClaw}"
MAP_FILE="${RC_MAP_FILE:-map_konjiam_L_107-1.yaml}"

NAV_UNIT="former_nav_bringup.service"
CLAW_UNIT="roboclaw_bringup.service"

usage() {
    cat <<EOF
Usage: sudo $0 <former0045|former0047> [nav|claw|all] [install|status|stop|disable]

  nav       Navigation2 자동 시작 ($NAV_UNIT)
  claw      RoboClaw 에이전트 자동 시작 ($CLAW_UNIT)
  all       두 unit 모두 (기본값)

  install   unit 을 설치하고 지금 바로 기동한다 (기본 동작)
  status    서비스 상태와 최근 로그를 본다
  stop      지금만 정지한다 (다음 부팅에는 다시 기동)
  disable   자동 시작을 해제하고 unit 파일을 제거한다
EOF
}

# 로봇 id -> rclaw 실행 환경명 매핑
robot_tag() {
    case "$1" in
        former0045) echo "kon45" ;;
        former0047) echo "kon47" ;;
        *) return 1 ;;
    esac
}

render_unit() {
    local template="$1" output="$2" robot="$3" tag="$4" nav_dir="$5"
    sed -e "s|__ROBOT_ID__|$robot|g" \
        -e "s|__ROBOT_TAG__|$tag|g" \
        -e "s|__ROBOT_HOME__|$ROBOT_HOME|g" \
        -e "s|__NAV_DIR__|$nav_dir|g" \
        -e "s|__MAP_FILE__|$MAP_FILE|g" \
        -e "s|__AUTOSTART_DIR__|$DIR|g" \
        -e "s|__WS__|$WORKSPACE|g" \
        "$template" > "$output"
    chmod 644 "$output"
}

install_unit() {
    local unit_name="$1" template="$2" robot="$3" tag="$4" nav_dir="$5"
    local unit_path="/etc/systemd/system/${unit_name}"
    [ -f "$template" ] || { echo "unit 템플릿이 없습니다: $template" >&2; exit 1; }
    render_unit "$template" "$unit_path" "$robot" "$tag" "$nav_dir"
    systemctl daemon-reload
    systemctl enable "$unit_name"
    # 이미 떠 있는 동명 컨테이너는 ExecStartPre 에서 정리되고 다시 뜬다.
    systemctl restart "$unit_name"
    sleep 5
    systemctl --no-pager --lines=20 status "$unit_name" || true
}

unit_status() {
    local unit_name="$1" containers="$2"
    systemctl --no-pager --lines=30 status "$unit_name" || true
    # shellcheck disable=SC2086
    docker ps -a --filter "name=${containers}" --format '{{.Names}}\t{{.Status}}'
}

main() {
    local robot="${1:-}" target="${2:-all}" action="${3:-install}"

    case "$robot" in
        -h|--help) usage; exit 0 ;;
        "") usage; exit 1 ;;
    esac

    local tag
    if ! tag="$(robot_tag "$robot")"; then
        echo "지원하지 않는 로봇입니다: $robot (former0045, former0047)" >&2
        exit 1
    fi

    local nav_dir="${RC_NAV_DIR:-$FORMER_ROOT/$robot}"

    case "$target" in
        nav|claw|all) ;;
        *) usage; exit 1 ;;
    esac
    case "$action" in
        install|status|stop|disable) ;;
        *) usage; exit 1 ;;
    esac

    if [ "$(id -u)" -ne 0 ] && [ "$action" != "status" ]; then
        echo "root 권한이 필요합니다: sudo $0 $robot $target $action" >&2
        exit 1
    fi

    local units=()
    if [ "$target" = "nav" ] || [ "$target" = "all" ]; then
        units+=("$NAV_UNIT")
    fi
    if [ "$target" = "claw" ] || [ "$target" = "all" ]; then
        units+=("$CLAW_UNIT")
    fi

    local unit_name
    for unit_name in "${units[@]}"; do
        case "$unit_name" in
            "$NAV_UNIT")
                case "$action" in
                    install)
                        if [ ! -x "$nav_dir/run_nav_stable.sh" ]; then
                            echo "nav 스크립트가 없습니다: $nav_dir/run_nav_stable.sh" >&2
                            exit 1
                        fi
                        install_unit "$NAV_UNIT" "$DIR/former_nav_bringup.service.in" "$robot" "$tag" "$nav_dir"
                        ;;
                    status) unit_status "$NAV_UNIT" "${robot}_nav" ;;
                    stop) systemctl stop "$NAV_UNIT" ;;
                    disable)
                        systemctl disable --now "$NAV_UNIT" 2>/dev/null || true
                        rm -f "/etc/systemd/system/${NAV_UNIT}"
                        systemctl daemon-reload
                        echo "자동 시작을 해제했습니다: $NAV_UNIT"
                        ;;
                esac
                ;;
            "$CLAW_UNIT")
                case "$action" in
                    install)
                        if [ ! -x "$WORKSPACE/rclaw" ]; then
                            echo "rclaw 실행 파일이 없습니다: $WORKSPACE/rclaw" >&2
                            exit 1
                        fi
                        install_unit "$CLAW_UNIT" "$DIR/roboclaw_bringup.service.in" "$robot" "$tag" "$nav_dir"
                        ;;
                    status) unit_status "$CLAW_UNIT" "robo_claw_container" ;;
                    stop) systemctl stop "$CLAW_UNIT" ;;
                    disable)
                        systemctl disable --now "$CLAW_UNIT" 2>/dev/null || true
                        rm -f "/etc/systemd/system/${CLAW_UNIT}"
                        systemctl daemon-reload
                        echo "자동 시작을 해제했습니다: $CLAW_UNIT"
                        ;;
                esac
                ;;
        esac
    done
}

main "$@"
