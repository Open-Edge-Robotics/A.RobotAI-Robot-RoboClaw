#!/usr/bin/env bash
# Humble (Gazebo Classic) 시뮬레이션 환경변수
# source 방식으로 사용: source sim/humble/env.sh

# DDS (인라인 XML - Humble은 파일 방식 대신 직접 지정)
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
# 시뮬레이션 전용 도메인(실기기 기본 0 과 분리). rclaw 의 RC_SIM_DOMAIN_ID 기본값과 맞춘다.
export ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-42}"
# Gazebo Classic 시뮬레이션은 노드를 40개 이상 띄운다. CycloneDDS 기본 자동 인덱스
# 범위(실측 32개)를 넘으면 "Failed to find a free participant index" 로 노드가
# 기동 직후 abort 하므로 상한을 올린다(robo_claw_cli/launcher/dds.go 와 동일 값).
export CYCLONEDDS_URI="<CycloneDDS><Domain><General><MaxMessageSize>65535B</MaxMessageSize><FragmentSize>1300B</FragmentSize></General><Discovery><ParticipantIndex>auto</ParticipantIndex><MaxAutoParticipantIndex>200</MaxAutoParticipantIndex></Discovery></Domain></CycloneDDS>"

# 로봇 모델
export TURTLEBOT3_MODEL="${TURTLEBOT3_MODEL:-waffle}"

# Gazebo Classic 경로 (중복 추가 방지)
export GAZEBO_MODEL_DATABASE_URI=""
[[ ":${GAZEBO_RESOURCE_PATH:-}:" != *":/usr/share/gazebo-11:"* ]] && \
  export GAZEBO_RESOURCE_PATH="/usr/share/gazebo-11${GAZEBO_RESOURCE_PATH:+:${GAZEBO_RESOURCE_PATH}}"
[[ ":${GAZEBO_MODEL_PATH:-}:" != *":/opt/ros/humble/share/turtlebot3_gazebo/models:"* ]] && \
  export GAZEBO_MODEL_PATH="/opt/ros/humble/share/turtlebot3_gazebo/models${GAZEBO_MODEL_PATH:+:${GAZEBO_MODEL_PATH}}"
[[ ":${GAZEBO_PLUGIN_PATH:-}:" != *":/usr/lib/x86_64-linux-gnu/gazebo-11/plugins:"* ]] && \
  export GAZEBO_PLUGIN_PATH="/usr/lib/x86_64-linux-gnu/gazebo-11/plugins${GAZEBO_PLUGIN_PATH:+:${GAZEBO_PLUGIN_PATH}}"

# 소프트웨어 렌더링 (GPU 미사용)
export LIBGL_ALWAYS_SOFTWARE=1
export MESA_GL_VERSION_OVERRIDE=3.3
export MESA_GLSL_VERSION_OVERRIDE=330
export OGRE_RTT_MODE=Copy

# ROS 공통
export RCUTILS_COLORIZED_OUTPUT=1
export PYTHONWARNINGS="ignore:setup.py install is deprecated"
