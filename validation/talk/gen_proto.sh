#!/usr/bin/env bash
# messenger.proto → messenger_pb2.py / messenger_pb2_grpc.py 생성 (최초 1회).
# 필요: pip install grpcio grpcio-tools
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! python3 -c "import grpc_tools.protoc" 2>/dev/null; then
  echo "[ERROR] grpcio-tools 가 필요합니다:  pip install grpcio grpcio-tools" >&2
  exit 1
fi

python3 -m grpc_tools.protoc -I "$DIR" \
  --python_out="$DIR" --grpc_python_out="$DIR" \
  "$DIR/messenger.proto"

echo "생성 완료: messenger_pb2.py, messenger_pb2_grpc.py"
