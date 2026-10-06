#!/bin/bash
set -euo pipefail

# former0047 전용 Nav2 RViz 런처
DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/run_former_env.sh" nav2-rviz "$@"