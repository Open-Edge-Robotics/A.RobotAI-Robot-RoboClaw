#!/usr/bin/env bash
# 시뮬레이션 실행 전 의존성 점검.
#
# Gazebo 시뮬레이션은 ROS 2 Jazzy(Harmonic, ros_gz_sim)만 지원한다. Humble
# (Gazebo Classic, gazebo_ros) 지원은 제거되었으므로 두 스택을 함께 다루지 않는다.
# Gazebo 없이 도는 전용 시뮬레이터(stub)는 표준 메시지 패키지만 있으면 동작한다.
#
# 사용법:
#   ./scripts/check_sim_deps.sh                 # Gazebo Harmonic(ros_gz_sim) 점검
#   ./scripts/check_sim_deps.sh --engine stub   # Gazebo 없는 기본 시뮬레이터 점검
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUPPORTED_DISTRO="jazzy"

ENGINE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine) ENGINE="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,10p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) echo "[sim-check] 알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done

# ROS 배포판 검증. 시뮬레이션은 Jazzy 단일 지원이다.
if [[ -z "${ROS_DISTRO:-}" ]]; then
  if [[ -f "/opt/ros/${SUPPORTED_DISTRO}/setup.bash" ]]; then
    ROS_DISTRO="${SUPPORTED_DISTRO}"
  fi
fi
if [[ "${ROS_DISTRO:-}" != "${SUPPORTED_DISTRO}" ]]; then
  echo "[sim-check] 시뮬레이션은 ROS 2 ${SUPPORTED_DISTRO}만 지원합니다 (ROS_DISTRO=${ROS_DISTRO:-unset})." >&2
  echo "[sim-check] Humble(Gazebo Classic) 지원은 제거되었습니다. /opt/ros/${SUPPORTED_DISTRO}/setup.bash 를 소싱하세요." >&2
  exit 1
fi

if [[ -z "${ENGINE}" ]]; then
  ENGINE="gz"
fi

case "${ENGINE}" in
  gz)
    REQUIRED=(
      "ros_gz_sim"
      "turtlebot4_gz_bringup"
      "turtlebot4_navigation"
    )
    APT_HINT="ros-${ROS_DISTRO}-ros-gz-sim ros-${ROS_DISTRO}-turtlebot4-simulator ros-${ROS_DISTRO}-turtlebot4-navigation"
    ;;
  stub)
    # Gazebo 없는 기본 시뮬레이터는 표준 메시지 패키지만 있으면 동작한다.
    REQUIRED=()
    APT_HINT=""
    ;;
  *)
    echo "[sim-check] 지원하지 않는 엔진: ${ENGINE} (gz|stub). Humble(Gazebo Classic) 의 classic 엔진은 제거되었습니다." >&2
    exit 2
    ;;
esac

if [[ ! -f "${SCRIPT_DIR}/install/setup.bash" ]]; then
  echo "[sim-check] install/setup.bash 가 없습니다. 'task build'를 먼저 실행하세요." >&2
  exit 1
fi

# ROS setup.bash 는 미정의 변수를 참조하므로 sourcing 동안 set -u 를 해제한다.
set +u
# shellcheck source=/dev/null
source "/opt/ros/${ROS_DISTRO}/setup.bash"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/install/setup.bash"
set -u

missing=()
for pkg in "${REQUIRED[@]}"; do
  if ! ros2 pkg prefix "${pkg}" >/dev/null 2>&1; then
    missing+=("${pkg}")
  fi
done

echo "[sim-check] distro=${ROS_DISTRO} engine=${ENGINE}"
if [[ ${#missing[@]} -eq 0 ]]; then
  echo "[sim-check] OK — 필요한 패키지가 모두 있습니다."
  exit 0
fi

echo "[sim-check] 누락 패키지: ${missing[*]}" >&2
if [[ -n "${APT_HINT}" ]]; then
  echo "[sim-check] 설치 예: sudo apt install ${APT_HINT}" >&2
fi
echo "[sim-check] Gazebo 없이 검증만 하려면: ./scripts/check_sim_deps.sh --engine stub" >&2
exit 1
