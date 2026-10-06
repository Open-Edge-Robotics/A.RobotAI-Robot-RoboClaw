#!/bin/bash
set -euo pipefail

# former0047 전용 SLAM bringup 런처 (데몬 모드)
DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/run_former_env.sh" slam --daemon "$@"