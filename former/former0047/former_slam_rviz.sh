#!/bin/bash
set -euo pipefail

# former0047 전용 SLAM RViz 런처
DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/run_former_env.sh" slam-rviz "$@"