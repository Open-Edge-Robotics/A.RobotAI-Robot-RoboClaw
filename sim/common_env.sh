#!/usr/bin/env bash
# ROS + workspace 환경 소싱. 시뮬레이션은 ROS 2 Jazzy만 지원한다
# (Humble/Gazebo Classic 지원 제거).
# sim/run_sim.sh 또는 외부 스크립트에서 사용

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${_COMMON_DIR}/.." && pwd)"
SUPPORTED_DISTRO="jazzy"

if [[ -z "${ROS_DISTRO:-}" ]]; then
  export ROS_DISTRO="${SUPPORTED_DISTRO}"
elif [[ "${ROS_DISTRO}" != "${SUPPORTED_DISTRO}" ]]; then
  echo "[rc] Error: 시뮬레이션은 ROS 2 ${SUPPORTED_DISTRO}만 지원합니다 (ROS_DISTRO=${ROS_DISTRO}). Humble(Gazebo Classic) 지원은 제거되었습니다." >&2
  unset _COMMON_DIR PROJECT_ROOT SUPPORTED_DISTRO
  # source 로 불릴 때는 호출한 셸을 죽이지 않고 소싱만 중단한다.
  if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
    return 1
  fi
  exit 1
fi

# ROS + workspace 소싱
[[ -f "/opt/ros/${ROS_DISTRO}/setup.bash" ]] && source "/opt/ros/${ROS_DISTRO}/setup.bash"
[[ -f "${PROJECT_ROOT}/install/setup.bash" ]] && source "${PROJECT_ROOT}/install/setup.bash"

# 배포판별 환경변수 적용
source "${_COMMON_DIR}/${SUPPORTED_DISTRO}/env.sh"

unset _COMMON_DIR PROJECT_ROOT SUPPORTED_DISTRO
