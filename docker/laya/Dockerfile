# syntax=docker/dockerfile:1
#
# 단독 Laya(System 1) 서버 이미지 — 엣지 서버 등 robo-claw 와 다른 호스트에서 실행할 때 사용한다.
# robo-claw 컨테이너 안에서 실행하려면 저장소 루트 Dockerfile 의 INSTALL_LAYA=true 를 사용한다.
#
#   # CPU
#   docker build -f docker/laya/Dockerfile -t robo-claw-laya:cpu docker/laya
#   # NVIDIA GPU (x86 엣지 서버)
#   docker build -f docker/laya/Dockerfile -t robo-claw-laya:cu128 \
#     --build-arg LAYA_TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128 docker/laya
#
# 상세: docs/SYSTEM1_FAST_ROUTER.md
ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG LAYA_VERSION=0.3.28
ARG LAYA_TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG LAYA_PREFETCH_REPOS=""

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    HF_HOME=/opt/hf

COPY install_laya.sh /tmp/install_laya.sh
RUN LAYA_VERSION="${LAYA_VERSION}" \
    LAYA_TORCH_INDEX_URL="${LAYA_TORCH_INDEX_URL}" \
    LAYA_PREFETCH_REPOS="${LAYA_PREFETCH_REPOS}" \
    bash /tmp/install_laya.sh && rm -f /tmp/install_laya.sh

# 한국어 명령을 처리하므로 multilingual 체크포인트만 미리 올린다.
# LAYA_DEVICE 는 자동 감지(cuda → cpu). 엣지 서버를 외부에 열 때는 LAYA_API_KEY 를 지정하고
# robo-claw 의 SYSTEM1_API_KEY 에 같은 값을 넣는다(Authorization: Bearer).
ENV LAYA_HOST=0.0.0.0 \
    LAYA_PORT=8000 \
    LAYA_PRELOAD=1 \
    LAYA_MODELS=multilingual \
    LAYA_DEFAULT_MODEL=multilingual \
    LAYA_MAX_LOADED=1

EXPOSE 8000
CMD ["laya-serve"]
