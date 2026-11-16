#!/usr/bin/env bash
# 시뮬레이션 실행 전 의존성 점검.
#
# Gazebo 기반 시뮬레이션 패키지는 배포판마다 이름이 다르고(humble=gazebo_ros,
# jazzy=ros_gz_sim) 서로 호환되지 않는다. package.xml 에 한쪽만 선언하면 다른
# 배포판에서 rosdep 해석이 실패하므로, 여기서 현재 배포판 기준으로 점검하고
# 설치 명령을 안내한다.
#
# 사용법:
#   ./scripts/check_sim_deps.sh              # 현재 배포판 기준 Gazebo 시뮬레이션 점검
#   ./scripts/check_sim_deps.sh --engine stub   # Gazebo 없는 기본 시뮬레이터 점검
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ENGINE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --engine) ENGINE="${2:-}"; shift 2 ;;
    -h|--help)
      sed -n '2,14p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *) echo "[sim-check] 알 수 없는 옵션: $1" >&2; exit 2 ;;
  esac
done

# ROS 배포판 감지 (scripts/run_in_ros_env.sh, sim/run_sim.sh 와 동일 순서)
if [[ -z "${ROS_DISTRO:-}" ]]; then
  [[ -f "/opt/ros/humble/setup.bash" ]] && ROS_DISTRO="humble"
  [[ -z "${ROS_DISTRO:-}" && -f "/opt/ros/jazzy/setup.bash" ]] && ROS_DISTRO="jazzy"
fi
ROS_DISTRO="${ROS_DISTRO:-jazzy}"

if [[ -z "${ENGINE}" ]]; then
  case "${ROS_DISTRO}" in
    jazzy) ENGINE="gz" ;;
    *) ENGINE="classic" ;;
  esac
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
  classic)
    REQUIRED=(
      "gazebo_ros"
      "turtlebot3_gazebo"
      "nav2_bringup"
    )
    APT_HINT="ros-${ROS_DISTRO}-gazebo-ros-pkgs ros-${ROS_DISTRO}-turtlebot3-gazebo ros-${ROS_DISTRO}-navigation2"
    ;;
  stub)
    # Gazebo 없는 기본 시뮬레이터는 표준 메시지 패키지만 있으면 동작한다.
    REQUIRED=()
    APT_HINT=""
    ;;
  *)
    echo "[sim-check] 지원하지 않는 엔진: ${ENGINE} (gz|classic|stub)" >&2
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
