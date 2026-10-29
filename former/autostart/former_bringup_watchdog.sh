#!/bin/bash
# 로봇 베이스 bringup(former_robot)이 살아 있는지 확인하고, 죽어 있으면 제한적으로 재기동한다.
#
# 2026-10-29 former0045: `ros2 launch former_bringup bringup_robot.launch.py` 프로세스와
# 컨테이너는 살아 있는데 그 안의 ros2_control_node / robot_state_publisher /
# controller spawner 만 죽었고, systemd 는 이를 알 수 없어 nav2 가 /odom 과 TF 없이
# 몇 시간을 버텼다. 컨테이너 수명이 아니라 **컨트롤러 상태**를 본다.
#
# 사용법: former_bringup_watchdog.sh
# 환경변수: RC_BASE_CONTAINER, RC_BASE_UNIT, RC_ROBOT_HOME,
#           RC_WATCHDOG_WINDOW(기본 1800초), RC_WATCHDOG_MAX_RESTARTS(기본 3)
set -uo pipefail

ROBOT_HOME="${RC_ROBOT_HOME:-/home/former}"
CONTAINER="${RC_BASE_CONTAINER:-former_robot}"
UNIT="${RC_BASE_UNIT:-former_bringup.service}"
DOMAIN="${ROS_DOMAIN_ID:-4}"
WINDOW="${RC_WATCHDOG_WINDOW:-1800}"
MAX_RESTARTS="${RC_WATCHDOG_MAX_RESTARTS:-3}"
STATE_FILE="${RC_WATCHDOG_STATE:-/var/tmp/former_bringup_watchdog.state}"

log() { echo "former_bringup_watchdog: $*"; }

# 관리자가 의도적으로 내려 둔 경우에는 손대지 않는다.
if ! systemctl is-active --quiet "$UNIT"; then
    log "$UNIT 가 active 가 아니므로 검사를 건너뜁니다"
    exit 0
fi

if ! command -v docker >/dev/null 2>&1; then
    log "docker 가 없어 검사를 건너뜁니다"
    exit 0
fi

# grep -q 로 파이프를 조기에 닫으면 docker exec 가 SIGPIPE(141)로 끝나 pipefail 에 걸린다.
# 출력을 변수에 받은 뒤 here-string 으로 검사한다.
healthy_base_controller() {
    local out
    out="$(docker exec -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
        -e CYCLONEDDS_URI=file:///root/cyclonedds_conf/cyclonedds.xml \
        -e ROS_DOMAIN_ID="$DOMAIN" \
        "$CONTAINER" bash -lc \
        "source /opt/ros/humble/setup.bash; timeout 25 ros2 control list_controllers" 2>/dev/null || true)"
    grep -qE '^base_controller[^[:alnum:]_].*[[:space:]]active([[:space:]]|$)' <<< "$out"
}

container_running() {
    local state
    state="$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)"
    [ "$state" = "true" ]
}

count_recent_restarts() {
    if [ ! -f "$STATE_FILE" ]; then
        echo 0
        return
    fi
    local now
    now="$(date +%s)"
    awk -v now="$now" -v window="$WINDOW" '$1 ~ /^[0-9]+$/ && now - $1 < window' "$STATE_FILE" | wc -l
}

record_restart() {
    local now
    now="$(date +%s)"
    {
        [ -f "$STATE_FILE" ] && cat "$STATE_FILE"
        echo "$now"
    } | awk -v now="$now" -v window="$WINDOW" '$1 ~ /^[0-9]+$/ && now - $1 < window' > "$STATE_FILE.tmp"
    mv "$STATE_FILE.tmp" "$STATE_FILE"
}

if container_running; then
    if healthy_base_controller; then
        exit 0
    fi
    log "경고 — base_controller 가 active 가 아닙니다(컨테이너는 실행 중)"
else
    log "경고 — $CONTAINER 컨테이너가 실행 중이 아닙니다"
fi

recent="$(count_recent_restarts)"
if [ "$recent" -ge "$MAX_RESTARTS" ]; then
    log "오류 — 최근 $((WINDOW / 60))분 안에 ${recent}회 재기동했습니다. 자동 재기동을 멈춥니다."
    log "오류 — 수동 점검이 필요합니다: journalctl -u $UNIT -n 100"
    exit 1
fi

record_restart
log "$UNIT 를 재기동합니다 (최근 $((WINDOW / 60))분 ${recent}/${MAX_RESTARTS}회)"
if systemctl restart "$UNIT"; then
    log "$UNIT 재기동 요청 완료"
else
    log "오류 — $UNIT 재기동에 실패했습니다"
    exit 1
fi
