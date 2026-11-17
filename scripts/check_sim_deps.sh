#!/usr/bin/env bash
# 시뮬레이터 의존성 점검.
#
# Gazebo 경로(turtlebot4/ros_gz, gazebo_ros)는 제거되었다. 시뮬레이터는 Gazebo 없이
# 도는 robo_claw_sim(stub)만 사용하므로 표준 ROS 메시지 패키지만 있으면 된다.
#
# 사용법:
#   ./scripts/check_sim_deps.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUPPORTED_DISTRO="jazzy"

if [[ -z "${ROS_DISTRO:-}" && -f "/opt/ros/${SUPPORTED_DISTRO}/setup.bash" ]]; then
  ROS_DISTRO="${SUPPORTED_DISTRO}"
fi
if [[ "${ROS_DISTRO:-}" != "${SUPPORTED_DISTRO}" ]]; then
  echo "[sim-check] ROS 2 ${SUPPORTED_DISTRO} 가 필요합니다 (ROS_DISTRO=${ROS_DISTRO:-unset})." >&2
  exit 1
fi

if [[ ! -f "${SCRIPT_DIR}/install/setup.bash" ]]; then
  echo "[sim-check] install/setup.bash 가 없습니다. 'task build'를 먼저 실행하세요." >&2
  exit 1
fi

set +u
# shellcheck source=/dev/null
source "/opt/ros/${ROS_DISTRO}/setup.bash"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/install/setup.bash"
set -u

REQUIRED=(nav2_msgs vision_msgs tf2_ros robo_claw_sim)
missing=()
for pkg in "${REQUIRED[@]}"; do
  if ! ros2 pkg prefix "${pkg}" >/dev/null 2>&1; then
    missing+=("${pkg}")
  fi
done

echo "[sim-check] distro=${ROS_DISTRO} engine=stub"
if [[ ${#missing[@]} -eq 0 ]]; then
  echo "[sim-check] OK — 필요한 패키지가 모두 있습니다."
  exit 0
fi
echo "[sim-check] 누락 패키지: ${missing[*]}" >&2
echo "[sim-check] 빌드 예: task build" >&2
exit 1
