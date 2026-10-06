#!/bin/bash
set -euo pipefail
# robo_claw_grpc/robo_claw_channel용 proto 파일에서 pb2 코드 재생성 스크립트
# robo_claw_msgs/proto/robo.proto → robo_claw_grpc/robo_claw_grpc/robo_pb2*.py
# robo_claw_msgs/proto/messenger.proto → robo_claw_channel/robo_claw_channel/messenger_pb2*.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROTO_DIR="$SCRIPT_DIR/src/robo_claw_msgs/proto"
GRPC_OUTPUT_DIR="$SCRIPT_DIR/src/robo_claw_grpc/robo_claw_grpc"
CHANNEL_OUTPUT_DIR="$SCRIPT_DIR/src/robo_claw_channel/robo_claw_channel"

# grpcio-tools는 uv가 관리하는 .venv에만 설치되어 있으므로(pyproject.toml 의존성),
# 시스템 python3로 바로 실행하면 "No module named 'grpc_tools'"로 실패한다.
if [[ -f "$SCRIPT_DIR/.venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "$SCRIPT_DIR/.venv/bin/activate"
fi

echo "Proto 파일 생성 시작..."
echo "  입력: $PROTO_DIR/robo.proto"
echo "  출력: $GRPC_OUTPUT_DIR"

python3 -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$GRPC_OUTPUT_DIR" \
  --pyi_out="$GRPC_OUTPUT_DIR" \
  --grpc_python_out="$GRPC_OUTPUT_DIR" \
  "$PROTO_DIR/robo.proto"

# robo_pb2_grpc.py의 import 경로를 robo_claw_grpc 패키지 기준 절대 경로로 고정
# (grpc_tools.protoc 버전에 따라 "import ros_grpc.robo_pb2" 또는 "import robo_pb2"로 생성됨)
sed -i -E 's/^import .*robo_pb2 as robo__pb2$/import robo_claw_grpc.robo_pb2 as robo__pb2/' "$GRPC_OUTPUT_DIR/robo_pb2_grpc.py"

echo "완료: $GRPC_OUTPUT_DIR/robo_pb2.py, robo_pb2_grpc.py, robo_pb2.pyi"

echo "  입력: $PROTO_DIR/fleet_control.proto"
echo "  출력: $GRPC_OUTPUT_DIR"

python3 -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$GRPC_OUTPUT_DIR" \
  --pyi_out="$GRPC_OUTPUT_DIR" \
  --grpc_python_out="$GRPC_OUTPUT_DIR" \
  "$PROTO_DIR/fleet_control.proto"

sed -i -E 's/^import fleet_control_pb2 as fleet__control__pb2$/from . import fleet_control_pb2 as fleet__control__pb2/' "$GRPC_OUTPUT_DIR/fleet_control_pb2_grpc.py"

echo "완료: $GRPC_OUTPUT_DIR/fleet_control_pb2.py, fleet_control_pb2_grpc.py, fleet_control_pb2.pyi"

echo "  입력: $PROTO_DIR/messenger.proto"
echo "  출력: $CHANNEL_OUTPUT_DIR"

python3 -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$CHANNEL_OUTPUT_DIR" \
  --pyi_out="$CHANNEL_OUTPUT_DIR" \
  --grpc_python_out="$CHANNEL_OUTPUT_DIR" \
  "$PROTO_DIR/messenger.proto"

# messenger_pb2_grpc.py의 import 경로를 robo_claw_channel 패키지 기준 상대 경로로 고정
# (grpc_tools.protoc 버전에 따라 "import messenger_pb2"로 생성될 수 있음)
sed -i -E 's/^import .*messenger_pb2 as messenger__pb2$/from . import messenger_pb2 as messenger__pb2/' "$CHANNEL_OUTPUT_DIR/messenger_pb2_grpc.py"

echo "완료: $CHANNEL_OUTPUT_DIR/messenger_pb2.py, messenger_pb2_grpc.py, messenger_pb2.pyi"
