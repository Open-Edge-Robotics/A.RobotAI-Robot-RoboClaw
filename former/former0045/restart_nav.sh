#!/bin/bash
# nav2 스택만 재기동한다(로봇 베이스/센서 컨테이너는 건드리지 않음).
# AMCL 크래시 등으로 네비게이션이 멈췄을 때 사용한다.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
NAME="$(basename "$DIR")_nav"
docker rm -f "$NAME" >/dev/null 2>&1 || true
exec "$DIR/run_nav_stable.sh" "$@"
