#!/usr/bin/env bash
# 테스트 실행 전 환경 점검.
#
# CHANGE_LOG 에 "collection 단계에서 중단"이 반복 기록된 원인은 대부분
# (1) 워크스페이스 미빌드, (2) proto 생성물 누락이었다. 원인을 코드 오류로
# 오해하지 않도록, 테스트 명령이 실행되기 전에 무엇이 없는지 먼저 알려준다.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
missing=()

if [[ ! -f "${SCRIPT_DIR}/install/setup.bash" ]]; then
  missing+=("install/setup.bash — 워크스페이스가 빌드되지 않았습니다. 'task build'를 실행하세요.")
fi

for generated in \
  "src/robo_claw_grpc/robo_claw_grpc/robo_pb2.py" \
  "src/robo_claw_grpc/robo_claw_grpc/robo_pb2_grpc.py" \
  "src/robo_claw_grpc/robo_claw_grpc/robo_pb2.pyi" \
  "src/robo_claw_grpc/robo_claw_grpc/fleet_control_pb2.py" \
  "src/robo_claw_grpc/robo_claw_grpc/fleet_control_pb2_grpc.py" \
  "src/robo_claw_grpc/robo_claw_grpc/fleet_control_pb2.pyi" \
  "src/robo_claw_channel/robo_claw_channel/messenger_pb2.py" \
  "src/robo_claw_channel/robo_claw_channel/messenger_pb2_grpc.py" \
  "src/robo_claw_channel/robo_claw_channel/messenger_pb2.pyi"; do
  if [[ ! -f "${SCRIPT_DIR}/${generated}" ]]; then
    missing+=("${generated} — proto 생성물이 없습니다. 'task proto'를 실행하세요.")
  fi
done

if ! command -v colcon >/dev/null 2>&1 && [[ ! -x "/opt/ros/${ROS_DISTRO:-jazzy}/bin/colcon" ]]; then
  missing+=("colcon 이 없습니다. ROS 2 개발 환경(ros-dev-tools)을 설치하세요.")
fi

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "[preflight] 테스트 환경이 준비되지 않았습니다:" >&2
  for item in "${missing[@]}"; do
    echo "  - ${item}" >&2
  done
  exit 1
fi

echo "[preflight] 테스트 환경 OK"
