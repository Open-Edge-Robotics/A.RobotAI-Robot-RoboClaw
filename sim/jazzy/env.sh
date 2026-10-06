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

# Gazebo 리소스 경로 (install 경로 사용)
_GZ_MODEL_PATH="${_PROJECT_ROOT}/install/robo_claw_bringup/share/robo_claw_bringup/models"
export GZ_SIM_RESOURCE_PATH="${GZ_SIM_RESOURCE_PATH:-}:${_GZ_MODEL_PATH}"

# ROS 공통
export RCUTILS_COLORIZED_OUTPUT=1
export PYTHONWARNINGS="ignore:setup.py install is deprecated"

# Gazebo GUI 캐시 정리 (충돌 방지)
rm -rf ~/.gz/sim/

unset _JAZZY_ENV_DIR _PROJECT_ROOT _GZ_MODEL_PATH
