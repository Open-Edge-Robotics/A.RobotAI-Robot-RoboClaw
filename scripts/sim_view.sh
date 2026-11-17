#!/usr/bin/env bash
# 시뮬레이터 + RViz2 시각화. sim_viz:=true 로 마커(/sim/world_markers)를 켠다.
# 사용법: ./scripts/sim_view.sh [kinematic|mujoco]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${SCRIPT_DIR}"
BACKEND="${1:-kinematic}"

if [[ -z "${DISPLAY:-}" ]]; then
  echo "[sim-view] DISPLAY 가 없습니다. X11 포워딩(ssh -X) 또는 로컬 데스크톱에서 실행하세요." >&2
  exit 1
fi

RViz_CFG="$(ros2 pkg prefix robo_claw_bringup)/share/robo_claw_bringup/config/sim.rviz"

ros2 launch robo_claw_bringup robo_claw_sim.launch.py \
  sim_engine:=stub sim_backend:="${BACKEND}" sim_viz:=true \
  use_channel:=false llm_provider:=none > /tmp/sim_view_launch.log 2>&1 &
SIM_PID=$!
trap 'kill ${SIM_PID} 2>/dev/null || true' EXIT

rviz2 -d "${RViz_CFG}"
