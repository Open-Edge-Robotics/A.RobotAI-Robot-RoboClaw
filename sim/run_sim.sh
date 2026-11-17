#!/usr/bin/env bash
# 시뮬레이션 런처. Gazebo 시뮬레이션은 ROS 2 Jazzy(Harmonic)만 지원한다.
# Humble(Gazebo Classic) 지원은 제거되었으므로, humble 환경에서는 조용히 다른
# 경로로 진행하지 않고 즉시 중단한다.
# 직접 실행: ./sim/run_sim.sh [--nav2] [--slam] [--world NAME]
set -euo pipefail

_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPORTED_DISTRO="jazzy"

if [[ "${ROS_DISTRO:-}" == "humble" ]]; then
  echo "[rc] Error: 시뮬레이션은 ROS 2 ${SUPPORTED_DISTRO}만 지원합니다. Humble(Gazebo Classic) 지원은 제거되었습니다." >&2
  echo "[rc]        /opt/ros/${SUPPORTED_DISTRO}/setup.bash 를 소싱한 뒤 다시 실행하세요." >&2
  exit 1
fi

if [[ -z "${ROS_DISTRO:-}" ]]; then
  if [[ -f "/opt/ros/${SUPPORTED_DISTRO}/setup.bash" ]]; then
    ROS_DISTRO="${SUPPORTED_DISTRO}"
  else
    echo "[rc] Error: /opt/ros/${SUPPORTED_DISTRO}/setup.bash 가 없습니다. 시뮬레이션은 ROS 2 ${SUPPORTED_DISTRO}가 필요합니다." >&2
    exit 1
  fi
fi

if [[ "${ROS_DISTRO}" != "${SUPPORTED_DISTRO}" ]]; then
  echo "[rc] Error: 시뮬레이션은 ROS 2 ${SUPPORTED_DISTRO}만 지원합니다 (ROS_DISTRO=${ROS_DISTRO})." >&2
  exit 1
fi

exec "${_DIR}/${SUPPORTED_DISTRO}/run_simulation.sh" "$@"
