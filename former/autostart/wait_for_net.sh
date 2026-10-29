#!/bin/bash
# CycloneDDS 가 바인딩한 네트워크 인터페이스에 IPv4 주소가 붙을 때까지 상한 시간만큼 기다린다.
#
# 2026-10-29 former0045: USB 이더넷(eth1)이 부팅 3초 뒤에야 올라오는데 그보다 먼저 DDS
# 참가자를 만들려다 `rmw_create_node: failed to create domain` 으로 ros2_control_node 와
# robot_state_publisher 가 exit -6 으로 죽었고, 베이스 토픽(/odom, /tf)이 통째로 비었다.
# 컨테이너를 띄우기 전에 인터페이스 주소를 확인해 이 레이스를 없앤다.
#
# 사용법: wait_for_net.sh [timeout] [iface] [expected_address]
#   - 첫 인자가 숫자면 timeout 으로 해석한다(wait_for_net.sh 60).
#   - iface 생략 시 $ROBOT_HOME/cyclonedds_conf/cyclonedds.xml 의 첫 NetworkInterface 를 쓴다.
#   - expected_address 생략 시 "IPv4 주소가 하나라도 있는가" 만 본다.
#   - 인터페이스 이름을 알아내지 못하면 판단하지 않고 통과한다(설정 없는 로봇 보호).
#
# 종료 코드: 0 준비됨 또는 판단 불가, 3 시간 초과(WAIT_FOR_NET_STRICT=1 일 때만)
#   기본은 경고만 남기고 통과한다. 링크가 정말 없는 상태로 부팅해도 베이스 기동을
#   막지 않고, 죽은 노드는 former_bringup_watchdog.sh 가 나중에 복구한다.
set -uo pipefail

ROBOT_HOME="${RC_ROBOT_HOME:-/home/former}"
DDS_CONF="${RC_DDS_CONF:-$ROBOT_HOME/cyclonedds_conf/cyclonedds.xml}"

resolve_iface() {
    [ -f "$DDS_CONF" ] || return 1
    sed -n 's/.*<NetworkInterface[^>]*name="\([^"]*\)".*/\1/p' "$DDS_CONF" | head -1
}

TIMEOUT=""
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then
    TIMEOUT="$1"
    shift
fi
IFACE="${1:-}"
EXPECTED_ADDR="${2:-}"
TIMEOUT="${TIMEOUT:-30}"

if [ -z "$IFACE" ]; then
    IFACE="$(resolve_iface || true)"
fi
if [ -z "$IFACE" ]; then
    echo "wait_for_net: DDS 인터페이스를 확인할 수 없어 대기를 건너뜁니다 ($DDS_CONF)" >&2
    exit 0
fi

if ! command -v ip >/dev/null 2>&1; then
    echo "wait_for_net: ip 명령이 없어 대기를 건너뜁니다" >&2
    exit 0
fi

iface_has_address() {
    local out
    out="$(ip -4 -o addr show dev "$IFACE" 2>/dev/null || true)"
    if [ -n "$EXPECTED_ADDR" ]; then
        case "$out" in
            *"inet $EXPECTED_ADDR"*) return 0 ;;
            *) return 1 ;;
        esac
    fi
    case "$out" in
        *"inet "*) return 0 ;;
        *) return 1 ;;
    esac
}

for ((i = 0; i < TIMEOUT; i++)); do
    if iface_has_address; then
        echo "wait_for_net: $IFACE ${EXPECTED_ADDR:+($EXPECTED_ADDR) }준비됨 (${i}s)"
        exit 0
    fi
    sleep 1
done

TARGET="${EXPECTED_ADDR:-IPv4 주소}"
if [ "${WAIT_FOR_NET_STRICT:-0}" = "1" ]; then
    echo "wait_for_net: ${TIMEOUT}s 안에 $IFACE 에 $TARGET 가 붙지 않았습니다 (엄격 모드)" >&2
    exit 3
fi
echo "wait_for_net: 경고 — ${TIMEOUT}s 안에 $IFACE 에 $TARGET 가 붙지 않았습니다. 그대로 기동합니다." >&2
exit 0
