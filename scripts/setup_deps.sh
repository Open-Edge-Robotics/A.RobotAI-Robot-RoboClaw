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
ort_tgz="${SCRIPT_DIR}/vendor/${ort_dir}.tgz"

if [[ ! -d "${SCRIPT_DIR}/${ort_dir}" && ! -d "/usr/local/${ort_dir}" && ! -d "${HOME}/.local/${ort_dir}" ]]; then
  if [[ -f "${ort_tgz}" ]]; then
    # 업스트림 릴리스 자산이 사라져도 빌드가 막히지 않도록 저장소에 사본을 보관한다.
    echo "[rc] 보관된 ONNX Runtime 사용: vendor/${ort_dir}.tgz"
    tar -xzf "${ort_tgz}" -C "${SCRIPT_DIR}"
  else
    echo "[rc] ONNX Runtime 1.19.2 C++ SDK 다운로드 중..."
    tgz_url="https://github.com/microsoft/onnxruntime/releases/download/v1.19.2/${ort_dir}.tgz"
    wget -q "${tgz_url}" -O /tmp/ort.tgz && tar -xzf /tmp/ort.tgz -C "${SCRIPT_DIR}" && rm -f /tmp/ort.tgz
  fi
  echo "[rc] ONNX Runtime 설치 완료 (${SCRIPT_DIR}/${ort_dir})"
fi
