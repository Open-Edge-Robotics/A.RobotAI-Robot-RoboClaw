# syntax=docker/dockerfile:1

# 1. 베이스 이미지 (ROS 2 Humble)
ARG ROS_DISTRO=humble
FROM ros:${ROS_DISTRO}-ros-base
ARG ROS_DISTRO=humble

# 2. 환경 변수
# TZ: 컨테이너 기본 타임존. 호스트의 /etc/localtime 마운트 또는
# -e TZ=... 로 런타임에 오버라이드 가능. tzdata 패키지가 필요함.
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive \
    ROS_DISTRO=${ROS_DISTRO} \
    RMW_IMPLEMENTATION=rmw_cyclonedds_cpp \
    CYCLONEDDS_URI=<CycloneDDS><Domain><General><MaxMessageSize>65535B</MaxMessageSize><FragmentSize>1300B</FragmentSize></General></Domain></CycloneDDS> \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Seoul

# 3. 필수 시스템 패키지
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    python3-pip \
    python3-dev \
    build-essential \
    cmake \
    git \
    wget \
    tzdata \
    libssl-dev \
    libffi-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libasound2-dev \
    libsdl2-dev \
    x11-apps \
    python3-vcstool \
    ros-${ROS_DISTRO}-cv-bridge \
    ros-${ROS_DISTRO}-vision-msgs \
    ros-${ROS_DISTRO}-rmw-cyclonedds-cpp \
    ros-${ROS_DISTRO}-nav2-msgs \
    ros-${ROS_DISTRO}-control-msgs \
    ros-${ROS_DISTRO}-rosidl-generator-c \
    ros-${ROS_DISTRO}-rosidl-generator-cpp \
    ros-${ROS_DISTRO}-rosidl-default-generators \
    ros-${ROS_DISTRO}-rosidl-typesupport-c \
    ros-${ROS_DISTRO}-rosidl-typesupport-cpp \
    ros-${ROS_DISTRO}-rosidl-typesupport-introspection-c \
    ros-${ROS_DISTRO}-rosidl-typesupport-introspection-cpp \
    ros-${ROS_DISTRO}-rosidl-typesupport-fastrtps-c \
    ros-${ROS_DISTRO}-rosidl-typesupport-fastrtps-cpp \
    ros-${ROS_DISTRO}-builtin-interfaces \
    && rm -rf /var/lib/apt/lists/*

# 3-1. 타임존 설정 — tzdata 설치 후 TZ 환경변수에 맞춰 심볼릭 링크 생성.
# 런타임에 /etc/localtime 마운트 또는 -e TZ=... 로 오버라이드 가능.
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 4. ONNX Runtime (amd64: x64 / arm64: aarch64)
ARG TARGETARCH
ARG ONNXRUNTIME_VERSION=1.19.2
RUN case "${TARGETARCH}" in \
      amd64) ORT_ARCH="x64" ;; \
      arm64) ORT_ARCH="aarch64" ;; \
      *) echo "Unsupported arch: ${TARGETARCH}" && exit 1 ;; \
    esac && \
    wget -q \
    "https://github.com/microsoft/onnxruntime/releases/download/v${ONNXRUNTIME_VERSION}/onnxruntime-linux-${ORT_ARCH}-${ONNXRUNTIME_VERSION}.tgz" \
    -O /tmp/ort.tgz && \
    tar -xzf /tmp/ort.tgz -C /usr/local && \
    rm /tmp/ort.tgz && \
    echo "/usr/local/onnxruntime-linux-${ORT_ARCH}-${ONNXRUNTIME_VERSION}/lib" \
         > /etc/ld.so.conf.d/onnxruntime.conf && \
    ldconfig

# 5. 작업 디렉토리
WORKDIR /ros2_ws

# 6. Python 의존성 (시스템 Python에 직접 설치)
# 주의: 이미지의 pip 해석 결과가 개발 환경(uv.lock)과 어긋나면 런타임 API가 바뀔 수 있다.
#        `mcp`처럼 API에 민감한 패키지는 lock 버전으로 정확히 고정한다
#        (`python3 scripts/check_docker_deps.py`로 검증).
COPY pyproject.toml ./
RUN pip3 install \
    "cmake>=3.24,<4" \
    "pytest>=7.4.0" \
    "openai>=1.0.0" \
    "anthropic>=0.39.0" \
    "langsmith>=0.1.100" \
    "ollama>=0.1.0" \
    "pyTelegramBotAPI>=4.12.0" \
    "slack-sdk>=3.21.0" \
    "discord.py>=2.3.0" \
    "grpcio>=1.59.0" \
    "grpcio-tools>=1.59.0" \
    "gTTS>=2.3.0" \
    "pygame>=2.5.0" \
    "python-dotenv>=1.0.0" \
    "PyYAML>=6.0.0" \
    "numpy>=1.24.0,<2.0.0" \
    "setuptools==65.7.0" \
    "empy==3.3.4" \
    "lark>=1.3.1" \
    "lxml>=6.0.2" \
    "mcp==1.27.0" \
    "qdrant-client>=1.17.1" \
    "sqlmodel>=0.0.21" \
    "aiosqlite>=0.19.0" \
    "psutil>=5.9.0"

# 6-1. (선택) Laya System 1 서버 — INSTALL_LAYA=true 일 때만 설치한다(기본 이미지 크기 유지).
# SYSTEM1_LOCAL_SERVER=true 로 기동하면 launch 가 같은 컨테이너에서 laya-serve 를 실행한다.
# GPU 사용 시 LAYA_TORCH_INDEX_URL 로 CUDA torch 인덱스를 지정하고 컨테이너를 GPU 옵션으로 실행한다
# (scripts/run_robo_claw_docker.sh --gpu). 상세: docs/SYSTEM1_FAST_ROUTER.md
ARG INSTALL_LAYA=false
ARG LAYA_VERSION=0.3.28
ARG LAYA_TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
ARG LAYA_PREFETCH_REPOS=""
ENV HF_HOME=/opt/hf
COPY docker/laya/install_laya.sh /tmp/install_laya.sh
RUN if [ "${INSTALL_LAYA}" = "true" ]; then \
      LAYA_VERSION="${LAYA_VERSION}" \
      LAYA_TORCH_INDEX_URL="${LAYA_TORCH_INDEX_URL}" \
      LAYA_EXTRA_CONSTRAINTS="numpy>=1.24.0,<2.0.0" \
      LAYA_PREFETCH_REPOS="${LAYA_PREFETCH_REPOS}" \
      bash /tmp/install_laya.sh; \
    fi && rm -f /tmp/install_laya.sh

# 7. ROS 패키지 manifest 선 복사 (rosdep 레이어 캐시)
COPY src/robo_claw_agent/package.xml src/robo_claw_agent/package.xml
COPY src/robo_claw_bringup/package.xml src/robo_claw_bringup/package.xml
COPY src/robo_claw_channel/package.xml src/robo_claw_channel/package.xml
COPY src/robo_claw_core/package.xml src/robo_claw_core/package.xml
COPY src/robo_claw_discovery/package.xml src/robo_claw_discovery/package.xml
COPY src/robo_claw_grpc/package.xml src/robo_claw_grpc/package.xml
COPY src/robo_claw_msgs/package.xml src/robo_claw_msgs/package.xml
COPY src/robo_claw_vision/package.xml src/robo_claw_vision/package.xml

# 8. ROS 의존성 설치 (rosdep)
RUN . /opt/ros/${ROS_DISTRO}/setup.sh && \
    apt-get update && \
    rosdep update && \
    rosdep install --from-paths src --ignore-src -y --rosdistro ${ROS_DISTRO} --skip-keys "pytest onnxruntime OpenCV" && \
    apt-get install -y --reinstall ros-${ROS_DISTRO}-builtin-interfaces ros-${ROS_DISTRO}-rosidl-generator-c ros-${ROS_DISTRO}-rosidl-typesupport-c && \
    rm -rf /var/lib/apt/lists/*

# 9. 소스 코드 복사
COPY src ./src
COPY generate_protos.sh ./generate_protos.sh

# Python gRPC 모듈은 git에서 제외되는 생성물이므로 이미지 빌드 시 생성한다.
# 생성 전에 실행하면 ament_python_install_package가 해당 모듈을 설치하지 않는다.
RUN bash ./generate_protos.sh && \
    test -s src/robo_claw_grpc/robo_claw_grpc/robo_pb2.py && \
    test -s src/robo_claw_grpc/robo_claw_grpc/robo_pb2_grpc.py && \
    test -s src/robo_claw_channel/robo_claw_channel/messenger_pb2.py && \
    test -s src/robo_claw_channel/robo_claw_channel/messenger_pb2_grpc.py && \
    PYTHONPATH=src/robo_claw_grpc:src/robo_claw_channel python3 -c \
      'from robo_claw_grpc import robo_pb2, robo_pb2_grpc; from robo_claw_channel import messenger_pb2, messenger_pb2_grpc'

# 10. Colcon 빌드
# buildx/QEMU에서 ROS export library 탐색이 깨져 메시지 패키지(robo_claw_msgs)는
# CMake로 먼저 설치한다. 같은 현상의 다운스트림 재발 방지를 위해 find_library
# 접두사/접미사 설정을 CMAKE_PROJECT_TOP_LEVEL_INCLUDES로 모든 패키지에 주입한다.
RUN . /opt/ros/${ROS_DISTRO}/setup.sh && \
    test -f /opt/ros/${ROS_DISTRO}/lib/libbuiltin_interfaces__rosidl_generator_c.so && \
    mkdir -p /tmp/ros_sanity && \
    printf 'cmake_minimum_required(VERSION 3.16)\nproject(ros_sanity)\nfind_package(std_msgs REQUIRED)\n' > /tmp/ros_sanity/CMakeLists.txt && \
    cmake -S /tmp/ros_sanity -B /tmp/ros_sanity_build && \
    rm -rf /tmp/ros_sanity /tmp/ros_sanity_build && \
    export CMAKE_LIBRARY_PATH=/opt/ros/${ROS_DISTRO}/lib:${CMAKE_LIBRARY_PATH:-} && \
    export LIBRARY_PATH=/opt/ros/${ROS_DISTRO}/lib:${LIBRARY_PATH:-} && \
    export LD_LIBRARY_PATH=/opt/ros/${ROS_DISTRO}/lib:${LD_LIBRARY_PATH:-} && \
    cmake -S src/robo_claw_msgs -B build/robo_claw_msgs \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_INSTALL_PREFIX=/ros2_ws/install/robo_claw_msgs \
      -DCMAKE_LIBRARY_PATH=/opt/ros/${ROS_DISTRO}/lib && \
    cmake --build build/robo_claw_msgs --target install -- -j1 && \
    printf 'COLCON_CURRENT_PREFIX="/ros2_ws/install/robo_claw_msgs"\n. "$COLCON_CURRENT_PREFIX/share/robo_claw_msgs/local_setup.sh"\n' > /ros2_ws/install/robo_claw_msgs/share/robo_claw_msgs/package.sh && \
    . /ros2_ws/install/robo_claw_msgs/share/robo_claw_msgs/local_setup.sh && \
    export AMENT_PREFIX_PATH=/ros2_ws/install/robo_claw_msgs:${AMENT_PREFIX_PATH:-} && \
    export CMAKE_PREFIX_PATH=/ros2_ws/install/robo_claw_msgs:${CMAKE_PREFIX_PATH:-} && \
    printf 'set(CMAKE_FIND_LIBRARY_PREFIXES "lib" "")\nset(CMAKE_FIND_LIBRARY_SUFFIXES ".so" ".a")\n' \
      > /ros2_ws/cmake_find_lib_init.cmake && \
    colcon build --parallel-workers 1 --packages-skip robo_claw_msgs --cmake-args \
      --no-warn-unused-cli \
      -DCMAKE_BUILD_TYPE=Release \
      -Drobo_claw_msgs_DIR=/ros2_ws/install/robo_claw_msgs/share/robo_claw_msgs/cmake \
      -DCMAKE_LIBRARY_PATH=/opt/ros/${ROS_DISTRO}/lib \
      -DCMAKE_PROJECT_TOP_LEVEL_INCLUDES=/ros2_ws/cmake_find_lib_init.cmake && \
    test -s install/robo_claw_grpc/local/lib/python3.10/dist-packages/robo_claw_grpc/robo_pb2.py && \
    test -s install/robo_claw_grpc/local/lib/python3.10/dist-packages/robo_claw_grpc/robo_pb2_grpc.py && \
    test -s install/robo_claw_channel/local/lib/python3.10/dist-packages/robo_claw_channel/messenger_pb2.py && \
    test -s install/robo_claw_channel/local/lib/python3.10/dist-packages/robo_claw_channel/messenger_pb2_grpc.py && \
    rm -rf /ros2_ws/build /ros2_ws/log /root/.ros/log

# 10-1. 빌드 버전 정보 (컨테이너 시작 로그에 표시 → 실행 중 이미지 식별)
# 매 빌드마다 값이 바뀌므로 캐시 무효화를 최소화하도록 빌드 후반부(무거운 레이어 뒤)에 둔다.
ARG BUILD_DATE=unknown
ARG GIT_COMMIT=unknown
ARG IMAGE_TAG=unknown
ENV RC_BUILD_DATE=${BUILD_DATE} \
    RC_GIT_COMMIT=${GIT_COMMIT} \
    RC_IMAGE_TAG=${IMAGE_TAG}
RUN printf 'build_date=%s\ngit_commit=%s\nimage_tag=%s\n' \
      "${BUILD_DATE}" "${GIT_COMMIT}" "${IMAGE_TAG}" > /etc/robo_claw_version

# 11. 런타임 환경 및 Entrypoint
# 시작 시 버전 배너를 stdout에 출력한다(docker logs 최상단에 노출).
RUN printf '#!/bin/bash\nset -e\necho "===== RoboClaw image ====="\ncat /etc/robo_claw_version 2>/dev/null || true\necho "=========================="\nsource /opt/ros/%s/setup.bash\nsource /ros2_ws/install/robo_claw_msgs/share/robo_claw_msgs/local_setup.bash\nsource /ros2_ws/install/setup.bash\nexec "$@"\n' "${ROS_DISTRO}" > /entrypoint.sh && \
    chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["ros2", "launch", "robo_claw_bringup", "robo_claw.launch.py", "use_channel:=true"]
