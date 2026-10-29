#!/bin/bash
# RoboClaw 에이전트 컨테이너(robo_claw_container) 부팅 자동 시작용 래퍼.
#
# `./rclaw launch former <env> --docker` 는 내부에서 `docker run -it` 을 실행한다.
# systemd 에는 TTY 가 없어 그대로 부팅 기동하면 docker 가
# `the input device is not a TTY`(exit 1) 로 즉시 실패한다. script(1) 로 pty 를
# 붙여 전경 실행하면 docker 가 TTY 를 얻고, systemd 는 이 프로세스 수명으로
# 컨테이너 생존을 감시할 수 있다(종료 시 종료 코드로 Restart 를 판단).
#
# 사용법: run_roboclaw.sh <kon45|kon47>
set -euo pipefail

ROBOT_TAG="${1:-${RC_ROBOT_TAG:-}}"
if [ -z "$ROBOT_TAG" ]; then
    echo "사용법: $0 <kon45|kon47>  (또는 RC_ROBOT_TAG 환경변수)" >&2
    exit 1
fi

WS="${RC_WS:-/home/former/workspace/A.RobotAI-Robot-RoboClaw}"
if [ ! -x "$WS/rclaw" ]; then
    echo "rclaw 실행 파일이 없습니다: $WS/rclaw" >&2
    exit 1
fi

cd "$WS"

# `docker run --rm` 으로 뜨는 컨테이너라, 이전 실행이 남아 있으면 이름 충돌로
# 실패한다. 수동 기동분까지 정리하고 시작한다(없으면 무시).
docker rm -f robo_claw_container >/dev/null 2>&1 || true

# -q: 배너 숨김, -e: 자식 종료 코드를 그대로 반환, -c: 실행 명령, /dev/null: 기록 생략
exec script -q -e -c "exec ./rclaw launch former ${ROBOT_TAG} --docker" /dev/null
