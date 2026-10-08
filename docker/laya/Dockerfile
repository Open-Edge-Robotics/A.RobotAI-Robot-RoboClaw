# syntax=docker/dockerfile:1
#
# 단독 Laya(System 1) 서버 이미지 — robo-claw 와 같은 호스트(별도 컨테이너) 또는 다른 호스트(엣지 서버)에서 실행한다.
# 상세: docs/SYSTEM1_LAYA_DOCKER.md
#
#   # CPU (amd64/arm64)
#   docker build -f docker/laya/Dockerfile -t robo-claw-laya:cpu docker/laya
#
#   # x86 엣지 서버 + NVIDIA GPU
#   docker build -f docker/laya/Dockerfile -t robo-claw-laya:cu128 \
#     --build-arg LAYA_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 docker/laya
#
#   # Jetson JetPack 6 (L4T R36.x, Ubuntu 22.04, Python 3.10, CUDA 12.6) — AGX Orin 등
#   docker build -f docker/laya/Dockerfile -t robo-claw-laya:jp6 \
#     --build-arg BASE_IMAGE=nvcr.io/nvidia/l4t-jetpack:<호스트 L4T 와 같은 r36 태그> \
#     --build-arg LAYA_TORCH_INDEX_URL=https://pypi.jetson-ai-lab.io/jp6/cu126 \
#     --build-arg LAYA_TORCH_SPEC=torch==2.8.0 docker/laya
#
#   # Jetson JetPack 7 (L4T R38 이상, SBSA, Ubuntu 24.04, Python 3.12, CUDA 13)
#   docker build -f docker/laya/Dockerfile -t robo-claw-laya:jp7 \
#     --build-arg BASE_IMAGE=<CUDA 13 + cuDNN, ubuntu24.04, arm64 이미지> \
#     --build-arg LAYA_TORCH_INDEX_URL=https://pypi.jetson-ai-lab.io/sbsa/cu130 docker/laya
#
# Jetson 용 torch 휠은 컨테이너 안의 CUDA 라이브러리를 사용하므로 BASE_IMAGE 는 CUDA 가 포함된 이미지여야 한다.
# CUDA 라이브러리가 없으면 torch 가 오류 없이 CPU 로만 동작한다.
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG LAYA_VERSION=0.3.28
ARG LAYA_TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# torch 요구사항. 인덱스의 최신 버전 대신 검증된 버전을 쓰려면 고정한다(예: torch==2.8.0).
ARG LAYA_TORCH_SPEC="torch>=2.0"
ARG LAYA_PREFETCH_REPOS=""

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HF_HOME=/opt/hf

# l4t-jetpack, nvidia/cuda 같은 베이스에는 python3/pip 가 없을 수 있다. 있으면 건너뛴다.
RUN if ! command -v python3 >/dev/null 2>&1 || ! python3 -m pip --version >/dev/null 2>&1; then \
      apt-get update && \
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3 python3-pip ca-certificates && \
      rm -rf /var/lib/apt/lists/*; \
    fi && \
    python3 --version && python3 -m pip --version

COPY install_laya.sh /tmp/install_laya.sh
RUN LAYA_VERSION="${LAYA_VERSION}" \
    LAYA_TORCH_INDEX_URL="${LAYA_TORCH_INDEX_URL}" \
    LAYA_TORCH_SPEC="${LAYA_TORCH_SPEC}" \
    LAYA_PREFETCH_REPOS="${LAYA_PREFETCH_REPOS}" \
    bash /tmp/install_laya.sh && rm -f /tmp/install_laya.sh

# 한국어 명령을 처리하므로 multilingual 체크포인트만 미리 올린다.
# LAYA_DEVICE 는 자동 감지(cuda → cpu). 외부에 열 때는 LAYA_API_KEY 를 지정하고
# robo-claw 의 SYSTEM1_API_KEY 에 같은 값을 넣는다(Authorization: Bearer).
# 같은 호스트에서만 쓰려면 실행 시 LAYA_HOST=127.0.0.1 로 바꾼다.
ENV LAYA_HOST=0.0.0.0 \
    LAYA_PORT=8000 \
    LAYA_PRELOAD=1 \
    LAYA_MODELS=multilingual \
    LAYA_DEFAULT_MODEL=multilingual \
    LAYA_MAX_LOADED=1

EXPOSE 8000
CMD ["laya-serve"]
