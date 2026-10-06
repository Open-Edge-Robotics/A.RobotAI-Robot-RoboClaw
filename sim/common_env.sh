#!/usr/bin/env bash
# ROS 배포판 자동 감지 후 distro별 env.sh 소싱
# sim/run_sim.sh 또는 외부 스크립트에서 사용

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${_COMMON_DIR}/.." && pwd)"

# ROS Distro 감지
if [[ -z "${ROS_DISTRO:-}" ]]; then
  [[ -f "/opt/ros/jazzy/setup.bash" ]]  && export ROS_DISTRO="jazzy"
  [[ -f "/opt/ros/humble/setup.bash" ]] && export ROS_DISTRO="${ROS_DISTRO:-humble}"
fi

# ROS + workspace 소싱
[[ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]] && source "/opt/ros/${ROS_DISTRO}/setup.bash"
[[ -f "${PROJECT_ROOT}/install/setup.bash" ]] && source "${PROJECT_ROOT}/install/setup.bash"

# 배포판별 환경변수 적용
case "${ROS_DISTRO:-}" in
  jazzy)  source "${_COMMON_DIR}/jazzy/env.sh" ;;
  humble) source "${_COMMON_DIR}/humble/env.sh" ;;
  *) echo "[rc] Warning: ROS_DISTRO not set or unsupported (${ROS_DISTRO:-})" ;;
esac

unset _COMMON_DIR PROJECT_ROOT
