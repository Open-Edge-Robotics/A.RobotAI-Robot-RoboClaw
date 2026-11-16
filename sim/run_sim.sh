#!/usr/bin/env bash
# ROS 배포판 자동 감지 후 distro별 런처로 위임
# 직접 실행: ./sim/run_sim.sh [--nav2] [--slam] [--world NAME]
set -euo pipefail

_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${ROS_DISTRO:-}" ]]; then
  # scripts/run_in_ros_env.sh, rclaw DetectRosDistro() 와 같은 순서를 유지한다.
  # (진입점마다 순서가 반대면 같은 호스트에서 서로 다른 배포판을 소상한다)
  [[ -f "/opt/ros/humble/setup.bash" ]] && ROS_DISTRO="humble"
  [[ -z "${ROS_DISTRO:-}" && -f "/opt/ros/jazzy/setup.bash" ]] && ROS_DISTRO="jazzy"
fi

case "${ROS_DISTRO:-}" in
  jazzy)  exec "${_DIR}/jazzy/run_simulation.sh" "$@" ;;
  humble) exec "${_DIR}/humble/run_simulation.sh" "$@" ;;
  *) echo "[rc] Error: ROS_DISTRO not set or unsupported (${ROS_DISTRO:-})"; exit 1 ;;
esac
