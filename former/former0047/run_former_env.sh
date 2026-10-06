#!/bin/bash
set -euo pipefail

# ==============================================================================
# Former0047 Docker Environment Unified Controller (Jetson 특화)
# ==============================================================================
#
# former0047 로봇 환경 특화 기능:
#   1. Jetson GPU: nvidia-smi 유무와 무관하게 --runtime nvidia 및 NVIDIA 환경변수 자동 적용
#   2. SSH X11 포워딩: rviz 실행 시 로컬 :0 디스플레이 자동 전환 (GLX 가속)
#   3. fontconfig 손상: 컨테이너 시작 시 빈 urw-*.conf 파일 자동 삭제 후 exec
#   4. XDG_RUNTIME_DIR / QT_X11_NO_MITSHM 환경변수 자동 주입
#   5. 상위 디렉토리(params, config, maps) 자동 폴백 탐색 지원 (Self-contained)
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_ID="former0047"

usage() {
    printf "Usage: %s [COMMAND] [OPTIONS]\n\n" "$0"
    printf "Robot: %s (Jetson/NVIDIA GPU 특화)\n\n" "$ROBOT_ID"
    printf "Commands:\n"
    printf "  nav2           Launch Navigation2 bringup\n"
    printf "  nav2-rviz      Run Rviz2 for Nav2 visualization\n"
    printf "  slam           Launch SLAM toolbox for mapping\n"
    printf "  slam-rviz      Run Rviz2 for SLAM visualization\n"
    printf "  bash           Start interactive bash inside the container\n\n"
    printf "Options:\n"
    printf "  -m, --map <file>      Map file name or path (default: w2_5f.yaml)\n"
    printf "  -d, --daemon          Run container in daemon (background) mode (applies to 'nav2' and 'slam')\n"
    printf "  --domain-id <id>      Set ROS_DOMAIN_ID (default: 4)\n"
    printf "  --image <name>        Docker image name (default: former_docker)\n"
    printf "  --tag <tag>           Docker image tag (default: 1.0)\n"
    printf "  --home-dir <dir>      Override host former home directory (default: /home/former or \$HOME)\n"
    printf "  -h, --help            Show this help message\n"
}

# Handle help option before shift
if [ $# -lt 1 ] || [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

# Load .env (current dir -> parent dir -> repo root -> cwd)
ENV_PATH=""
if [[ -f "${SCRIPT_DIR}/.env" ]]; then
  ENV_PATH="${SCRIPT_DIR}/.env"
elif [[ -f "${SCRIPT_DIR}/../.env" ]]; then
  ENV_PATH="${SCRIPT_DIR}/../.env"
elif [[ -f "${SCRIPT_DIR}/../../.env" ]]; then
  ENV_PATH="${SCRIPT_DIR}/../../.env"
elif [[ -f .env ]]; then
  ENV_PATH=".env"
fi

if [[ -n "${ENV_PATH}" ]]; then
  set -o allexport
  source "${ENV_PATH}"
  set +o allexport
fi

# Default configurations
IMAGE_NAME="${RC_IMAGE_NAME:-former_docker}"
IMAGE_TAG="${RC_IMAGE_TAG:-1.0}"
ROS_DOMAIN_ID="${ROS_DOMAIN_ID:-4}" # ROS 표준 환경 변수
MAP_FILE="${RC_MAP_FILE:-w2_5f.yaml}"
DAEMON_MODE=false
EXTRA_ARGS=()

# Auto-detect former user home directory
if [ -d "/home/former" ]; then
    FORMER_HOME="/home/former"
else
    FORMER_HOME="$HOME"
fi

COMMAND="$1"
shift

# Parse options
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--map)
            MAP_FILE="$2"
            shift 2
            ;;
        -d|--daemon)
            DAEMON_MODE=true
            shift
            ;;
        --domain-id)
            ROS_DOMAIN_ID="$2"
            shift 2
            ;;
        --image)
            IMAGE_NAME="$2"
            shift 2
            ;;
        --tag)
            IMAGE_TAG="$2"
            shift 2
            ;;
        --home-dir)
            FORMER_HOME="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            EXTRA_ARGS+=("$1")
            shift
            ;;
    esac
done

# Resolve paths
FORMER_HOME=$(realpath "$FORMER_HOME")

MAPS_DIR="$FORMER_HOME/workspace/nav2/maps"
RVIZ_DIR="$FORMER_HOME/workspace/nav2/rviz"
DEV_WS_SRC="$FORMER_HOME/dev_ws/src"
CYCLONEDDS_CONF="$FORMER_HOME/cyclonedds_conf"

# Directory resolver: checks script dir first, then parent dir
resolve_dir() {
    local rel_path="$1"
    if [ -d "$SCRIPT_DIR/$rel_path" ]; then
        echo "$SCRIPT_DIR/$rel_path"
    elif [ -d "$SCRIPT_DIR/../$rel_path" ]; then
        echo "$(cd "$SCRIPT_DIR/../$rel_path" && pwd)"
    else
        echo ""
    fi
}

# Resolve map file: checks absolute path, FORMER_HOME, script dir, and parent dir
resolve_map_file() {
    local target="$1"
    if [[ "$target" == /* ]]; then
        if [ -f "$target" ]; then
            echo "$target"
            return 0
        fi
        return 1
    fi

    # 1. Check FORMER_HOME workspace maps
    if [ -f "$MAPS_DIR/$target" ]; then
        echo "$MAPS_DIR/$target"
        return 0
    fi

    # 2. Check local script maps directory
    if [ -f "$SCRIPT_DIR/maps/$target" ]; then
        echo "$SCRIPT_DIR/maps/$target"
        return 0
    fi

    # 3. Check shared parent maps directory
    if [ -f "$SCRIPT_DIR/../maps/$target" ]; then
        echo "$(cd "$SCRIPT_DIR/../maps" && pwd)/$target"
        return 0
    fi

    return 1
}

# --- former0047 환경 감지 함수 ---
is_jetson() {
    [ -f /etc/nv_tegra_release ] || [ -d /proc/device-tree/nvidia,tegra ] || ls /usr/lib/*/tegra/libGLX_nvidia.so.0 >/dev/null 2>&1
}

has_local_display() {
    [ -S /tmp/.X11-unix/X0 ]
}

is_ssh_forwarding() {
    [[ "${DISPLAY:-}" == localhost:* ]] || [[ "${DISPLAY:-}" == 127.0.0.1:* ]]
}

has_nvidia_runtime() {
    docker info 2>/dev/null | grep -q "Runtimes:.*nvidia"
}

# XAuthority detection
XAUTH="$HOME/.Xauthority"
if [ -f "$FORMER_HOME/.Xauthority" ]; then
    XAUTH="$FORMER_HOME/.Xauthority"
fi

# Base Docker volumes
DOCKER_VOLUMES=(
    "-v" "/tmp/.X11-unix:/tmp/.X11-unix"
    "-v" "$XAUTH:/root/.Xauthority"
    "-v" "/dev:/dev"
)

# CycloneDDS Configuration path check & mount (only if directory exists)
if [ -d "$CYCLONEDDS_CONF" ]; then
    DOCKER_VOLUMES+=("-v" "$CYCLONEDDS_CONF:/root/cyclonedds_conf:z")
fi

# Dev Workspace source mount
if [ -d "$DEV_WS_SRC" ]; then
    DOCKER_VOLUMES+=("-v" "$DEV_WS_SRC:/root/dev_ws/src:z")
else
    echo "Notice: Dev workspace source directory not found at $DEV_WS_SRC (skipped)" >&2
fi

CONTAINER_CMD=""
CONTAINER_NAME="${ROBOT_ID}_container"

# Set parameters based on subcommand
case "$COMMAND" in
    nav2)
        CONTAINER_NAME="${ROBOT_ID}_nav"
        MAP_HOST_FILE=$(resolve_map_file "$MAP_FILE" || true)

        if [ -z "$MAP_HOST_FILE" ] || [ ! -f "$MAP_HOST_FILE" ]; then
            echo "Error: Map file not found: $MAP_FILE" >&2
            echo "Searched locations:" >&2
            echo "  - $MAPS_DIR/$MAP_FILE" >&2
            echo "  - $SCRIPT_DIR/maps/$MAP_FILE" >&2
            echo "  - $SCRIPT_DIR/../maps/$MAP_FILE" >&2

            SHARED_MAPS_DIR=$(resolve_dir "maps")
            if [ -n "$SHARED_MAPS_DIR" ] && [ -d "$SHARED_MAPS_DIR" ]; then
                echo "Available map files in $SHARED_MAPS_DIR:" >&2
                for map in "$SHARED_MAPS_DIR"/*.yaml; do
                    [ -e "$map" ] || continue
                    echo "  $(basename "$map")" >&2
                done
            fi
            exit 1
        fi

        MAP_HOST_DIR="$(dirname "$MAP_HOST_FILE")"
        MAP_HOST_BASENAME="$(basename "$MAP_HOST_FILE")"
        DOCKER_VOLUMES+=("-v" "$MAP_HOST_DIR:/ws/maps:ro")

        # Mount params directory
        HOST_PARAMS_DIR=$(resolve_dir "params")
        PARAMS_PARAM=""
        PARAMS_FILE="${RC_PARAMS_FILE:-nav2_params.yaml}"
        if [ -n "$HOST_PARAMS_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$HOST_PARAMS_DIR:/ws/params:ro")
            if [ -f "$HOST_PARAMS_DIR/$PARAMS_FILE" ]; then
                PARAMS_PARAM="/ws/params/$PARAMS_FILE"
            else
                echo "Warning: Params file not found: $HOST_PARAMS_DIR/$PARAMS_FILE. Using package default." >&2
            fi
        else
            echo "Warning: Params directory not found. Using package default." >&2
        fi

        MAP_PARAM="/ws/maps/$MAP_HOST_BASENAME"
        CONTAINER_CMD="ros2 launch former_navigation2 bringup_launch.py map:=$MAP_PARAM"
        if [ -n "$PARAMS_PARAM" ]; then
            CONTAINER_CMD="$CONTAINER_CMD params_file:=$PARAMS_PARAM"
        fi
        ;;
    nav2-rviz)
        CONTAINER_NAME="${ROBOT_ID}_nav_rviz"
        SHARED_MAPS_DIR=$(resolve_dir "maps")
        if [ -d "$MAPS_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$MAPS_DIR:/ws/maps")
        elif [ -n "$SHARED_MAPS_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$SHARED_MAPS_DIR:/ws/maps")
        fi

        if [ -d "$RVIZ_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$RVIZ_DIR:/ws/rviz")
        else
            echo "Notice: Host Rviz config directory not found at $RVIZ_DIR." >&2
        fi
        CONTAINER_CMD="rviz2 -d /ws/rviz/nav2_view.rviz"
        ;;
    slam)
        CONTAINER_NAME="${ROBOT_ID}_slam"
        SHARED_MAPS_DIR=$(resolve_dir "maps")
        if [ -d "$MAPS_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$MAPS_DIR:/ws/maps")
        elif [ -n "$SHARED_MAPS_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$SHARED_MAPS_DIR:/ws/maps")
        fi

        # Mount config directory (mapper params)
        HOST_CONFIG_DIR=$(resolve_dir "config")
        SLAM_PARAMS_PARAM=""
        SLAM_PARAMS_FILE="${RC_SLAM_PARAMS_FILE:-mapper_params.yaml}"
        if [ -n "$HOST_CONFIG_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$HOST_CONFIG_DIR:/ws/config:ro")
            if [ -f "$HOST_CONFIG_DIR/$SLAM_PARAMS_FILE" ]; then
                SLAM_PARAMS_PARAM="/ws/config/$SLAM_PARAMS_FILE"
            else
                echo "Warning: SLAM params file not found: $HOST_CONFIG_DIR/$SLAM_PARAMS_FILE. Using package default." >&2
            fi
        else
            echo "Warning: SLAM config directory not found. Using package default." >&2
        fi

        CONTAINER_CMD="ros2 launch former_navigation2 map_building.launch.py"
        if [ -n "$SLAM_PARAMS_PARAM" ]; then
            CONTAINER_CMD="$CONTAINER_CMD slam_params_file:=$SLAM_PARAMS_PARAM"
        fi
        ;;
    slam-rviz)
        CONTAINER_NAME="${ROBOT_ID}_slam_rviz"
        if [ -d "$RVIZ_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$RVIZ_DIR:/ws/rviz")
            CONTAINER_CMD="rviz2 -d /ws/rviz/map_building.rviz"
        else
            echo "Notice: Host Rviz config directory not found at $RVIZ_DIR. Using package default view." >&2
            CONTAINER_CMD="rviz2 -d /root/dev_ws/src/former_robot/former_bringup/view_robot.rviz"
        fi
        ;;
    bash)
        CONTAINER_NAME="${ROBOT_ID}_bash"
        CONTAINER_CMD="bash"
        ;;
    *)
        printf "Error: Unknown command '%s'\n\n" "$COMMAND" >&2
        usage
        exit 1
        ;;
esac

# --- GPU Acceleration options (Jetson / NVIDIA 특화) ---
GPU_ARGS=()
if is_jetson || has_nvidia_runtime; then
    GPU_ARGS=(
        "--runtime" "nvidia"
        "-e" "NVIDIA_DRIVER_CAPABILITIES=all"
        "-e" "NVIDIA_VISIBLE_DEVICES=all"
    )
    echo "NVIDIA Jetson/Runtime detected. Activating '--runtime nvidia'."
elif command -v nvidia-smi >/dev/null 2>&1; then
    GPU_ARGS=(
        "-e" "NVIDIA_DRIVER_CAPABILITIES=all"
        "-e" "NVIDIA_VISIBLE_DEVICES=all"
    )
    if docker run --help 2>&1 | grep -q "\-\-gpus"; then
        GPU_ARGS+=("--gpus" "all")
    fi
    echo "NVIDIA GPU detected. Activating GPU acceleration."
fi

# --- DISPLAY 환경 및 X11 포워딩 처리 (RViz SSH 대응) ---
TARGET_DISPLAY="${DISPLAY:-}"
IS_RVIZ=false
if [[ "$COMMAND" == *"rviz"* ]]; then
    IS_RVIZ=true
fi

if [ "$IS_RVIZ" = true ] && is_ssh_forwarding && has_local_display; then
    echo "SSH X11 forwarding detected. Switching RViz display to local :0 for OpenGL hardware acceleration."
    TARGET_DISPLAY=":0"
    if command -v xhost >/dev/null 2>&1; then
        DISPLAY=:0 xhost +local:docker > /dev/null 2>&1 || true
        DISPLAY=:0 xhost +SI:localuser:root > /dev/null 2>&1 || true
    fi
fi

# Docker execution mode (interactive vs daemon)
RUN_MODE="-it"
if [ "$DAEMON_MODE" = true ] && { [ "$COMMAND" = "nav2" ] || [ "$COMMAND" = "slam" ]; }; then
    RUN_MODE="-itd"
    echo "Starting container '$CONTAINER_NAME' in daemon (background) mode..."
else
    RUN_MODE="-it --rm"
    echo "Starting container '$CONTAINER_NAME' in interactive mode..."
fi

# X11 authorization setup
cleanup() {
    if command -v xhost >/dev/null 2>&1; then
        xhost -local:docker > /dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

if command -v xhost >/dev/null 2>&1; then
    xhost +local:docker > /dev/null 2>&1 || true
fi

# Display information
echo "Target Robot: $ROBOT_ID"
echo "Docker Image: $IMAGE_NAME:$IMAGE_TAG"
echo "ROS_DOMAIN_ID: $ROS_DOMAIN_ID"
echo "Target DISPLAY: $TARGET_DISPLAY"

# Fontconfig 정리 및 컨테이너 명령 exec 래핑 (컨테이너 내부 urw-*.conf 0바이트 파일 삭제)
FINAL_EXEC_CMD=(
    "bash" "-c"
    "find /usr/share/fontconfig/conf.avail/ -name 'urw-*.conf' -size 0 -delete 2>/dev/null; exec $CONTAINER_CMD \"\$@\""
    "_"
)

# Execute docker run
docker run $RUN_MODE \
    --name "$CONTAINER_NAME" \
    --net host \
    --env ROS_DOMAIN_ID="$ROS_DOMAIN_ID" \
    -e "DISPLAY=$TARGET_DISPLAY" \
    -e "XDG_RUNTIME_DIR=/tmp/runtime-root" \
    -e "QT_X11_NO_MITSHM=1" \
    "${GPU_ARGS[@]}" \
    "${DOCKER_VOLUMES[@]}" \
    "$IMAGE_NAME:$IMAGE_TAG" \
    "${FINAL_EXEC_CMD[@]}" "${EXTRA_ARGS[@]}"