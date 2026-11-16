#!/usr/bin/env bash
# Jazzy (Gazebo Harmonic) 시뮬레이션 환경변수
# source 방식으로 사용: source sim/jazzy/env.sh

_JAZZY_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PROJECT_ROOT="$(cd "${_JAZZY_ENV_DIR}/../.." && pwd)"

# DDS
# export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
# export CYCLONEDDS_URI="${_JAZZY_ENV_DIR}/cyclonedds_config.xml"

# Qt / Gazebo 렌더링
export QT_QPA_PLATFORM=xcb
export GZ_RENDERING_BACKEND=ogre2

# NVIDIA GPU 사용 (소프트웨어 렌더링 비활성화)
unset LIBGL_ALWAYS_SOFTWARE
# export __NV_PRIME_RENDER_OFFLOAD=1
# export __GLX_VENDOR_LIBRARY_NAME=nvidia

# Gazebo 리소스 경로: 이 저장소는 모델/world 자산을 포함하지 않는다.
# 이전에는 존재하지 않는 install/.../models 경로를 추가했고, 선행 ':' 때문에
# 빈 항목(=CWD)이 리소스 경로에 섞여 모델 탐색이 흔들렸다. 필요한 경우에만 확장한다.
if [[ -n "${RC_GZ_SIM_RESOURCE_PATH:-}" ]]; then
  export GZ_SIM_RESOURCE_PATH="${RC_GZ_SIM_RESOURCE_PATH}${GZ_SIM_RESOURCE_PATH:+:${GZ_SIM_RESOURCE_PATH}}"
fi

# ROS 공통
export RCUTILS_COLORIZED_OUTPUT=1
export PYTHONWARNINGS="ignore:setup.py install is deprecated"

# Gazebo GUI 설정 캐시 초기화는 기본 비활성화한다.
# 매 실행 시 ~/.gz/sim/ 을 지우면 사용자의 GUI 설정이 매번 소실된다.
if [[ "${RC_SIM_RESET_GZ_CACHE:-0}" == "1" ]]; then
  rm -rf ~/.gz/sim/
  echo "[rc] ~/.gz/sim/ 초기화 (RC_SIM_RESET_GZ_CACHE=1)"
fi

unset _JAZZY_ENV_DIR _PROJECT_ROOT
