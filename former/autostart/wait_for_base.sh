#!/bin/bash
# nav2 를 띄우기 전에 로봇 베이스(former_robot)가 준비됐는지 확인하는 준비 게이트.
#
# `After=former_bringup.service` 만으로는 준비를 보장하지 못한다. 베이스 unit 은
# `docker run` 이 컨테이너를 만든 시점에 started 로 보고되고, 그 안의 ros2_control_node /
# robot_state_publisher 는 몇 초 뒤에야 뜬다. 2026-10-29 former0045 는 그 사이에
# 컨트롤러 spawner 가 죽어 /base_controller/odom 과 odom→base_link TF 가 아예 없는 채로
# nav2 가 올라왔고, amcl 이 모든 스캔을 버리며 조용히 멈춰 있었다.
#
# 사용법: wait_for_base.sh [timeout_seconds]
# 종료 코드: 항상 0. 베이스 지연으로 nav2 기동을 막지 않는다. 준비되지 않은 채로
#   기동해도 베이스가 늦게 살아나면 nav2 lifecycle 이 다시 활성화된다(실측).
#
# 주의: 확인용 컨테이너는 매 시도마다 이름을 정리한다. grep -q 로 파이프를 조기에 닫으면
#   docker run 이 SIGPIPE 로 끝나 `--rm` 정리가 누락되고, 다음 시도가 이름 충돌로 실패한다.
#   그래서 출력을 변수에 받은 뒤 here-string 으로 검사한다.
set -uo pipefail

ROBOT_HOME="${RC_ROBOT_HOME:-/home/former}"
IMAGE="${RC_IMAGE:-former_docker:1.0}"
CONTAINER="${RC_BASE_CONTAINER:-former_robot}"
PROBE_NAME="${RC_PROBE_NAME:-former_base_probe}"
DOMAIN="${ROS_DOMAIN_ID:-4}"
ODOM_TOPIC="${RC_ODOM_TOPIC:-/base_controller/odom}"
TIMEOUT="${1:-120}"

if ! command -v docker >/dev/null 2>&1; then
    echo "wait_for_base: docker 가 없어 준비 확인을 건너뜁니다" >&2
    exit 0
fi

container_running() {
    local state
    state="$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)"
    [ "$state" = "true" ]
}

if ! container_running; then
    echo "wait_for_base: $CONTAINER 가 아직 실행 중이 아닙니다. 기동을 기다립니다." >&2
fi

probe_output() {
    # 이전 시도가 남긴 확인용 컨테이너를 정리한다.
    docker rm -f "$PROBE_NAME" >/dev/null 2>&1 || true
    timeout 60 docker run --rm --net host --name "$PROBE_NAME" \
        -e ROS_DOMAIN_ID="$DOMAIN" \
        -e RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
        -e CYCLONEDDS_URI=file:///root/cyclonedds_conf/cyclonedds.xml \
        -v "$ROBOT_HOME/cyclonedds_conf:/root/cyclonedds_conf:z" \
        "$IMAGE" \
        bash -lc "source /opt/ros/humble/setup.bash; timeout 20 ros2 topic info $ODOM_TOPIC" 2>/dev/null || true
}

# 베이스 unit 은 Type=simple 이라 `docker run` 프로세스가 뜨는 순간 started 로 보고된다.
# 그래서 nav unit 의 ExecStartPre 시점에는 컨테이너가 아직 Running 이 아닐 수 있다.
# 이때 바로 포기하면 게이트가 무의미해진다(2026-10-29 재부팅 실측). 컨테이너 기동과
# /base_controller/odom 공개를 하나의 시간 상한 안에서 함께 기다린다.
container_seen="0"
for ((i = 0; i < TIMEOUT; i += 5)); do
    if container_running; then
        container_seen="1"
        out="$(probe_output)"
        if grep -qE 'Publisher count: [1-9]' <<< "$out"; then
            echo "wait_for_base: $ODOM_TOPIC publisher 준비됨 (${i}s)"
            exit 0
        fi
    fi
    sleep 5
done

if [ "$container_seen" = "0" ]; then
    echo "wait_for_base: 경고 — ${TIMEOUT}s 안에 $CONTAINER 가 실행되지 않았습니다." >&2
else
    echo "wait_for_base: 경고 — ${TIMEOUT}s 안에 $ODOM_TOPIC publisher 를 찾지 못했습니다." >&2
fi
echo "wait_for_base: 그대로 nav2 를 기동합니다. 베이스가 죽어 있으면 former_bringup_watchdog 가 복구합니다." >&2
exit 0
