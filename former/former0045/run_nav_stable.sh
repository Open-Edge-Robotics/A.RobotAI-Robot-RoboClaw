#!/bin/bash
# nav2 를 노드별 개별 프로세스로 기동한다(use_composition:=False).
#
# 배경
# - 기본 bringup 은 모든 Nav2 노드를 component container 한 프로세스에 넣어 올리기 때문에,
#   AMCL SIGSEGV 한 번에 네비게이션 스택 전체가 사라진다. 노드를 분리하면 장애가 국소화되고,
#   크래시 시 launch 로그에 어떤 노드가 죽었는지 남는다.
# - vendor launch 는 velocity_smoother 의 최종 출력을 /cmd_vel 로 보내는데, 이 로봇의 베이스는
#   /base_controller/cmd_vel_unstamped 만 구독한다. 그래서 navigation_launch.py 사본을
#   바인드마운트해 cmd_vel_smoothed -> /base_controller/cmd_vel_unstamped 로 연결한다.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH_DIR="$DIR/patch"
LAUNCH_SRC="$HOME/dev_ws/src/former_robot/former_navigation2/launch/navigation_launch.py"
LAUNCH_DST="$PATCH_DIR/navigation_launch.py"

mkdir -p "$PATCH_DIR"
if [ ! -f "$LAUNCH_DST" ] || [ "$LAUNCH_SRC" -nt "$LAUNCH_DST" ]; then
    python3 - "$LAUNCH_SRC" "$LAUNCH_DST" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
old = "[('cmd_vel', 'cmd_vel_nav'), ('cmd_vel_smoothed', 'cmd_vel')]"
new = "[('cmd_vel', 'cmd_vel_nav'), ('cmd_vel_smoothed', '/base_controller/cmd_vel_unstamped')]"
if old not in s and "/base_controller/cmd_vel_unstamped" not in s:
    raise SystemExit("velocity_smoother remap 패턴을 찾지 못했습니다: " + src)
open(dst, "w").write(s.replace(old, new))
PY
fi

export RC_EXTRA_VOLUMES="-v $LAUNCH_DST:/root/dev_ws/install/former_navigation2/share/former_navigation2/launch/navigation_launch.py:ro"
exec "$DIR/run_former_env.sh" nav2 --daemon -m "${RC_MAP_FILE:-map_konjiam_L_107-1.yaml}" use_composition:=False "$@"
