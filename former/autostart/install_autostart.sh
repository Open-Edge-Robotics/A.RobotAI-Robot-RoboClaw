#!/bin/bash
# Former 로봇 부팅 자동 시작(systemd) 설치/해제 스크립트.
#
# 네 개의 unit 과 DDS 설정 하나를 다룬다.
#   base  former_bringup.service              로봇 베이스 (former_robot 컨테이너)
#   base  former_bringup_watchdog.service/.timer  base_controller 상태 감시 + 제한 재기동
#   nav   former_nav_bringup.service           Navigation2  (former00XX_nav 컨테이너)
#   claw  roboclaw_bringup.service             RoboClaw 에이전트 (robo_claw_container)
#   dds   cyclonedds_conf/cyclonedds.xml       CycloneDDS 인터페이스 설정(eth0)
#
#   sudo ./install_autostart.sh former0047 all            # 설치 + 즉시 기동
#   sudo ./install_autostart.sh former0045 base
#   sudo ./install_autostart.sh former0045 nav
#   sudo ./install_autostart.sh former0047 status         # 상태/최근 로그
#   sudo ./install_autostart.sh former0047 stop           # 지금만 정지
#   sudo ./install_autostart.sh former0047 disable        # 자동 시작 해제 + unit 제거
#
# RC_MAP_FILE 로 nav 기동 맵을, RC_NAV_DIR 로 nav 스크립트 디렉토리를,
# RC_WS 로 RoboClaw 워크스페이스를 바꿀 수 있다. 기존 cyclonedds.xml 은
# 타임스탬프 백업을 남기고 교체한다.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
ROBOT_HOME="${ROBOT_HOME:-/home/former}"
ROBOT_USER="${RC_ROBOT_USER:-former}"
FORMER_ROOT="${RC_FORMER_ROOT:-$ROBOT_HOME/konjiam/former}"
WORKSPACE="${RC_WS:-$ROBOT_HOME/workspace/A.RobotAI-Robot-RoboClaw}"
MAP_FILE="${RC_MAP_FILE:-map_konjiam_L_107-1.yaml}"

BASE_UNIT="former_bringup.service"
WATCHDOG_UNIT="former_bringup_watchdog.service"
WATCHDOG_TIMER="former_bringup_watchdog.timer"
NAV_UNIT="former_nav_bringup.service"
CLAW_UNIT="roboclaw_bringup.service"

DDS_SRC="$DIR/cyclonedds.xml"
DDS_DST="$ROBOT_HOME/cyclonedds_conf/cyclonedds.xml"

usage() {
    cat <<EOF
Usage: sudo $0 <former0045|former0047> [base|nav|claw|all] [install|status|stop|disable]

  base      로봇 베이스 ($BASE_UNIT) + DDS 설정 + health watchdog timer
  nav       Navigation2 자동 시작 ($NAV_UNIT)
  claw      RoboClaw 에이전트 자동 시작 ($CLAW_UNIT)
  all       모두 (기본값)

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

# 로봇별 베이스 launch 추가 인자
robot_base_args() {
    case "$1" in
        former0045) echo "use_namespace:=false" ;;
        former0047) echo "" ;;
        *) return 1 ;;
    esac
}

render_unit() {
    local template="$1" output="$2" robot="$3" tag="$4" nav_dir="$5" base_args="$6"
    sed -e "s|__ROBOT_ID__|$robot|g" \
        -e "s|__ROBOT_TAG__|$tag|g" \
        -e "s|__ROBOT_HOME__|$ROBOT_HOME|g" \
        -e "s|__NAV_DIR__|$nav_dir|g" \
        -e "s|__MAP_FILE__|$MAP_FILE|g" \
        -e "s|__AUTOSTART_DIR__|$DIR|g" \
        -e "s|__WS__|$WORKSPACE|g" \
        -e "s|__BASE_LAUNCH_ARGS__|$base_args|g" \
        "$template" > "$output"
    chmod 644 "$output"
}

install_unit() {
    local unit_name="$1" template="$2" robot="$3" tag="$4" nav_dir="$5" base_args="$6"
    local unit_path="/etc/systemd/system/${unit_name}"
    [ -f "$template" ] || { echo "unit 템플릿이 없습니다: $template" >&2; exit 1; }
    # 교체 전 기존 unit 을 남긴다. 베이스 unit 처럼 로봇을 움직이는 unit 은
    # 새 템플릿이 문제를 일으킬 때 바로 되돌릴 수 있어야 한다.
    if [ -f "$unit_path" ]; then
        local backup="${unit_path}.bak.$(date +%Y%m%d_%H%M%S)"
        cp -a "$unit_path" "$backup"
        echo "기존 unit 을 백업했습니다: $backup"
    fi
    render_unit "$template" "$unit_path" "$robot" "$tag" "$nav_dir" "$base_args"
    systemctl daemon-reload
    systemctl enable "$unit_name"
    # 이미 떠 있는 동명 컨테이너는 ExecStartPre 에서 정리되고 다시 뜬다.
    systemctl restart "$unit_name"
    sleep 5
    systemctl --no-pager --lines=20 status "$unit_name" || true
}

install_dds_config() {
    [ -f "$DDS_SRC" ] || { echo "DDS 설정 템플릿이 없습니다: $DDS_SRC" >&2; exit 1; }
    mkdir -p "$(dirname "$DDS_DST")"
    if [ -f "$DDS_DST" ] && cmp -s "$DDS_SRC" "$DDS_DST"; then
        echo "DDS 설정이 이미 최신입니다: $DDS_DST"
        return 0
    fi
    if [ -f "$DDS_DST" ]; then
        local backup="${DDS_DST}.bak.$(date +%Y%m%d_%H%M%S)"
        cp -a "$DDS_DST" "$backup"
        echo "기존 DDS 설정을 백업했습니다: $backup"
    fi
    install -o "$ROBOT_USER" -m 644 "$DDS_SRC" "$DDS_DST"
    echo "DDS 설정을 설치했습니다: $DDS_DST (인터페이스 eth0)"
}

install_watchdog() {
    local robot="$1" tag="$2" nav_dir="$3" base_args="$4"
    install_unit "$WATCHDOG_UNIT" "$DIR/former_bringup_watchdog.service.in" "$robot" "$tag" "$nav_dir" "$base_args"
    render_unit "$DIR/former_bringup_watchdog.timer.in" "/etc/systemd/system/${WATCHDOG_TIMER}" "$robot" "$tag" "$nav_dir" "$base_args"
    systemctl daemon-reload
    systemctl enable --now "$WATCHDOG_TIMER"
    systemctl --no-pager --lines=10 status "$WATCHDOG_TIMER" || true
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

    local tag base_args
    if ! tag="$(robot_tag "$robot")"; then
        echo "지원하지 않는 로봇입니다: $robot (former0045, former0047)" >&2
        exit 1
    fi
    base_args="$(robot_base_args "$robot")"

    local nav_dir="${RC_NAV_DIR:-$FORMER_ROOT/$robot}"

    case "$target" in
        base|nav|claw|all) ;;
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

    if [ "$target" = "base" ] || [ "$target" = "all" ]; then
        case "$action" in
            install)
                # DDS 설정이 틀리면 베이스 노드가 통째로 죽는다. base 보다 먼저 적용한다.
                install_dds_config
                install_unit "$BASE_UNIT" "$DIR/former_bringup.service.in" "$robot" "$tag" "$nav_dir" "$base_args"
                install_watchdog "$robot" "$tag" "$nav_dir" "$base_args"
                ;;
            status)
                unit_status "$BASE_UNIT" "former_robot"
                unit_status "$WATCHDOG_UNIT" "former_robot"
                systemctl --no-pager --lines=10 status "$WATCHDOG_TIMER" || true
                journalctl -u "$WATCHDOG_UNIT" -n 20 --no-pager || true
                ;;
            stop)
                systemctl stop "$WATCHDOG_TIMER"
                systemctl stop "$BASE_UNIT"
                ;;
            disable)
                systemctl disable --now "$WATCHDOG_TIMER" 2>/dev/null || true
                systemctl disable --now "$BASE_UNIT" 2>/dev/null || true
                rm -f "/etc/systemd/system/${BASE_UNIT}" "/etc/systemd/system/${WATCHDOG_UNIT}" \
                    "/etc/systemd/system/${WATCHDOG_TIMER}"
                systemctl daemon-reload
                echo "자동 시작을 해제했습니다: $BASE_UNIT $WATCHDOG_UNIT $WATCHDOG_TIMER"
                ;;
        esac
    fi

    if [ "$target" = "nav" ] || [ "$target" = "all" ]; then
        case "$action" in
            install)
                if [ ! -x "$nav_dir/run_nav_stable.sh" ]; then
                    echo "nav 스크립트가 없습니다: $nav_dir/run_nav_stable.sh" >&2
                    exit 1
                fi
                install_unit "$NAV_UNIT" "$DIR/former_nav_bringup.service.in" "$robot" "$tag" "$nav_dir" "$base_args"
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
    fi

    if [ "$target" = "claw" ] || [ "$target" = "all" ]; then
        case "$action" in
            install)
                if [ ! -x "$WORKSPACE/rclaw" ]; then
                    echo "rclaw 실행 파일이 없습니다: $WORKSPACE/rclaw" >&2
                    exit 1
                fi
                install_unit "$CLAW_UNIT" "$DIR/roboclaw_bringup.service.in" "$robot" "$tag" "$nav_dir" "$base_args"
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
    fi
}

main "$@"
