#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v uv &> /dev/null; then
  echo "[rc] uv가 설치되어 있지 않습니다. (curl -LsSf https://astral.sh/uv/install.sh | sh)" >&2
  exit 1
fi

echo "[rc] uv를 사용한 의존성 및 가상환경 설정 시작..."
uv venv --python /usr/bin/python3 --system-site-packages
uv sync
echo "[rc] 가상환경 설정 완료 (.venv)"

arch=$(uname -m)
ort_arch="x64"
if [[ "${arch}" == "aarch64" ]]; then
  ort_arch="aarch64"
fi
ort_dir="onnxruntime-linux-${ort_arch}-1.19.2"

if [[ ! -d "${SCRIPT_DIR}/${ort_dir}" && ! -d "/usr/local/${ort_dir}" && ! -d "${HOME}/.local/${ort_dir}" ]]; then
  echo "[rc] ONNX Runtime 1.19.2 C++ SDK 다운로드 중..."
  tgz_url="https://github.com/microsoft/onnxruntime/releases/download/v1.19.2/${ort_dir}.tgz"
  wget -q "${tgz_url}" -O /tmp/ort.tgz && tar -xzf /tmp/ort.tgz -C "${SCRIPT_DIR}" && rm /tmp/ort.tgz
  echo "[rc] ONNX Runtime 설치 완료 (${SCRIPT_DIR}/${ort_dir})"
fi
