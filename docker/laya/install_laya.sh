#!/usr/bin/env bash
# Laya(System 1) 서버 설치 스크립트.
#
# robo-claw 이미지(INSTALL_LAYA=true, Thor 내장 실행)와 단독 Laya 서버 이미지(엣지 서버)가
# 같은 설치 절차를 공유한다. GPU 사용 여부는 torch 휠 인덱스로 결정한다.
#
# 환경변수
#   LAYA_VERSION            laya 패키지 버전 (기본 0.3.28)
#   LAYA_TORCH_INDEX_URL    torch 휠 인덱스. 기본은 CPU 전용 인덱스.
#                           - CPU (amd64/arm64)        : https://download.pytorch.org/whl/cpu
#                           - CUDA (x86 엣지 서버 등)   : https://download.pytorch.org/whl/cu128 등
#                           - Jetson JetPack 6 (L4T R36, Python 3.10) : https://pypi.jetson-ai-lab.io/jp6/cu126
#                             (CUDA 12.8/12.9 설치 시 jp6/cu128, jp6/cu129)
#                           - Jetson JetPack 7 (L4T R38+, SBSA, Python 3.12) : https://pypi.jetson-ai-lab.io/sbsa/cu130
#                           - pypi                      : PyPI 기본 torch (amd64 에서는 CUDA 라이브러리 포함)
#                           Jetson 인덱스는 --index-url 로만 사용한다. PyPI 와 섞으면 CPU 용 torch 가 설치될 수 있다.
#   LAYA_TORCH_SPEC         torch 요구사항 (기본 "torch>=2.0", 예: "torch==2.8.0")
#   LAYA_EXTRA_CONSTRAINTS  laya 설치 시 함께 지킬 요구사항 (예: robo-claw 이미지의 "numpy>=1.24.0,<2.0.0")
#   LAYA_PREFETCH_REPOS     빌드 시 미리 받을 Hugging Face 저장소 ID(공백 구분). 폐쇄망 배포용. 기본 없음.
#                           HF_HOME 아래에 저장된다.
set -euo pipefail

LAYA_VERSION="${LAYA_VERSION:-0.3.28}"
LAYA_TORCH_INDEX_URL="${LAYA_TORCH_INDEX_URL:-https://download.pytorch.org/whl/cpu}"
LAYA_TORCH_SPEC="${LAYA_TORCH_SPEC:-torch>=2.0}"
LAYA_EXTRA_CONSTRAINTS="${LAYA_EXTRA_CONSTRAINTS:-}"
LAYA_PREFETCH_REPOS="${LAYA_PREFETCH_REPOS:-}"

# Ubuntu 24.04 계열(PEP 668) 시스템 Python 에도 설치되도록 한다. 구버전 pip 는 무시한다.
export PIP_BREAK_SYSTEM_PACKAGES=1
PIP=(python3 -m pip install --no-cache-dir)

echo "[install_laya] laya==${LAYA_VERSION}, torch='${LAYA_TORCH_SPEC}' from '${LAYA_TORCH_INDEX_URL}'"

# 1. torch 를 먼저 원하는 인덱스(CPU/CUDA)에서 설치한다. 이후 laya 설치 시 이미 만족된
#    torch 를 다시 받지 않으므로, PyPI 기본 torch 로 덮어쓰이지 않는다.
if [[ "${LAYA_TORCH_INDEX_URL}" == "pypi" ]]; then
  "${PIP[@]}" "${LAYA_TORCH_SPEC}"
else
  "${PIP[@]}" --index-url "${LAYA_TORCH_INDEX_URL}" "${LAYA_TORCH_SPEC}"
fi

# 2. laya 와 HTTP 서버 의존성(fastapi, uvicorn).
LAYA_REQS=("laya[serve]==${LAYA_VERSION}")
if [[ -n "${LAYA_EXTRA_CONSTRAINTS}" ]]; then
  # shellcheck disable=SC2206
  LAYA_REQS+=(${LAYA_EXTRA_CONSTRAINTS})
fi
"${PIP[@]}" "${LAYA_REQS[@]}"

# 3. 설치 결과 확인. 빌드 환경에는 GPU 가 없으므로 cuda_available 은 False 가 정상이다.
python3 - <<'PY'
import torch
import laya

print(
    "[install_laya] laya=%s torch=%s torch_cuda_build=%s"
    % (getattr(laya, "__version__", "?"), torch.__version__, torch.version.cuda)
)
PY
command -v laya-serve >/dev/null || { echo "[install_laya] laya-serve not found on PATH" >&2; exit 1; }

# 4. (선택) 모델 미리 받기 — 폐쇄망 로봇용.
if [[ -n "${LAYA_PREFETCH_REPOS}" ]]; then
  for repo in ${LAYA_PREFETCH_REPOS}; do
    echo "[install_laya] prefetch ${repo} -> ${HF_HOME:-~/.cache/huggingface}"
    python3 -c "from huggingface_hub import snapshot_download; snapshot_download('${repo}')"
  done
fi

echo "[install_laya] done"
