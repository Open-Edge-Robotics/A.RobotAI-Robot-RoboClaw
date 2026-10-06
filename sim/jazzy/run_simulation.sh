#!/usr/bin/env bash
# Jazzy (Gazebo Harmonic) 독립 실행 스크립트
# task/rclaw 래퍼 없이 직접 실행할 때 사용
set -euo pipefail

_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${_DIR}/../.." && pwd)"

# ROS + workspace 소싱
source /opt/ros/jazzy/setup.bash
[[ -f "${PROJECT_ROOT}/install/setup.bash" ]] && source "${PROJECT_ROOT}/install/setup.bash"

# 환경변수 적용
source "${_DIR}/env.sh"

# 인자 파싱
MODEL="${MODEL:-standard}"
WORLD="${WORLD:-warehouse}"
NAV2="false"
SLAM="false"
MAP=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --nav2)  NAV2="true"; shift ;;
    --slam)  SLAM="true"; NAV2="true"; shift ;;
    --world) WORLD="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    --map)   MAP="$2"; shift 2 ;;
    *) echo "[rc] Unknown option: $1"; exit 1 ;;
  esac
done

LAUNCH_ARGS=(
  "use_sim_time:=true"
  "sim_engine:=gz"
  "robot_model:=${MODEL}"
  "world:=${WORLD}"
  "use_nav2:=${NAV2}"
  "use_slam:=${SLAM}"
)
[[ -n "${MAP}" ]] && LAUNCH_ARGS+=("map:=${MAP}")

echo "[rc] Gazebo Sim(Jazzy) — Nav2=${NAV2}, SLAM=${SLAM}, World=${WORLD}, Model=${MODEL}"
ros2 launch robo_claw_bringup robo_claw_sim.launch.py "${LAUNCH_ARGS[@]}"
