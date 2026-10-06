#!/usr/bin/env bash
# ROS 2 및 워크스페이스 환경 로드 후 명령어 실행 헬퍼
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ROS Distro 감지
if [[ -z "${ROS_DISTRO:-}" ]]; then
  if [[ -f "/opt/ros/humble/setup.bash" ]]; then
    ROS_DISTRO="humble"
  elif [[ -f "/opt/ros/jazzy/setup.bash" ]]; then
    ROS_DISTRO="jazzy"
  else
    ROS_DISTRO="jazzy"
  fi
fi

ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.bash"
WS_SETUP="${SCRIPT_DIR}/install/setup.bash"
VENV_DIR="${SCRIPT_DIR}/.venv"

load_venv() {
  if [[ -d "${VENV_DIR}" ]]; then
    # shellcheck source=/dev/null
    source "${VENV_DIR}/bin/activate"
  fi
}

resolve_venv_site_packages() {
  local matches=("${VENV_DIR}"/lib/python3.*/site-packages)
  if [[ -d "${matches[0]:-}" ]]; then
    printf '%s' "${matches[0]}"
  fi
}

MODE="ws"
if [[ "${1:-}" == "--ros-only" ]]; then
  MODE="ros-only"
  shift
elif [[ "${1:-}" == "--ws" ]]; then
  MODE="ws"
  shift
fi

if [[ "${MODE}" == "ros-only" ]]; then
  if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    export PATH=$(echo "$PATH" | sed -e "s|${VIRTUAL_ENV}/bin:||g")
    unset VIRTUAL_ENV
  fi
  unset PYTHONPATH
  set +u
  # shellcheck source=/dev/null
  source "${ROS_SETUP}"
  set -u
  cd "${SCRIPT_DIR}"
  exec "$@"
else
  set +u
  # shellcheck source=/dev/null
  source "${ROS_SETUP}"
  if [[ -f "${WS_SETUP}" ]]; then
    # shellcheck source=/dev/null
    source "${WS_SETUP}"
  fi
  load_venv
  set -u
  cd "${SCRIPT_DIR}"
  exec "$@"
fi
