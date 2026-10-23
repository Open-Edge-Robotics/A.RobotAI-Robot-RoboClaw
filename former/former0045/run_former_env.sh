#!/bin/bash
set -euo pipefail

# ==============================================================================
# Former0045 Docker Environment Unified Controller
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROBOT_ID="former0045"

usage() {
    printf "Usage: %s [COMMAND] [OPTIONS]\n\n" "$0"
    printf "Robot: %s\n\n" "$ROBOT_ID"
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
INITPOSE_OVERRIDE_DIR=""

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

# Resolve initpose.md: checks absolute path, script dir, parent dirs, and FORMER_HOME
resolve_initpose_file() {
    local target="${1:-initpose.md}"
    local candidates=()

    if [[ "$target" == /* ]]; then
        candidates=("$target")
    else
        candidates=(
            "$SCRIPT_DIR/$target"
            "$SCRIPT_DIR/../$target"
            "$SCRIPT_DIR/../../$target"
            "$FORMER_HOME/$target"
        )
    fi

    local f
    for f in "${candidates[@]}"; do
        if [ -f "$f" ]; then
            echo "$(cd "$(dirname "$f")" && pwd)/$(basename "$f")"
            return 0
        fi
    done
    return 1
}

# Parse initpose.md and print "x y yaw".
# Expected format:
#   Position(0.407429, 0.204412, 0), Orientation(0, 0, -0.797877, 0.60282) = Angle: -1.84753
parse_initpose() {
    local file="$1"
    local x y yaw

    x=$(sed -nE 's/.*Position\([[:space:]]*([-+]?[0-9]*\.?[0-9]+)[[:space:]]*,.*/\1/p' "$file" | head -n1)
    y=$(sed -nE 's/.*Position\([[:space:]]*[-+]?[0-9]*\.?[0-9]+[[:space:]]*,[[:space:]]*([-+]?[0-9]*\.?[0-9]+).*/\1/p' "$file" | head -n1)
    yaw=$(sed -nE 's/.*Angle:[[:space:]]*([-+]?[0-9]*\.?[0-9]+).*/\1/p' "$file" | head -n1)

    if [ -n "$x" ] && [ -n "$y" ] && [ -n "$yaw" ]; then
        printf '%s %s %s\n' "$x" "$y" "$yaw"
        return 0
    fi
    return 1
}

# Copy a Nav2 params file, replacing the AMCL initial_pose with the given values.
# Supports both dict form (initial_pose: {x,y,z,yaw}) and list form (initial_pose: [x,y,z]).
apply_initpose_to_params() {
    local src="$1"
    local dst="$2"
    local x="$3"
    local y="$4"
    local yaw="$5"

    awk -v x="$x" -v y="$y" -v yaw="$yaw" '
        /^[^[:space:]]/ { in_block = 0 }
        /^[[:space:]]*initial_pose:[[:space:]]*\[/ {
            sub(/\[.*\]/, "[" x ", " y ", 0.0]")
            print
            next
        }
        /^[[:space:]]*initial_pose:[[:space:]]*$/ {
            in_block = 1
            print
            next
        }
        in_block && /^[[:space:]]*x:[[:space:]]*/ {
            sub(/x:.*/, "x: " x)
            print
            next
        }
        in_block && /^[[:space:]]*y:[[:space:]]*/ {
            sub(/y:.*/, "y: " y)
            print
            next
        }
        in_block && /^[[:space:]]*yaw:[[:space:]]*/ {
            sub(/yaw:.*/, "yaw: " yaw)
            in_block = 0
            print
            next
        }
        { print }
    ' "$src" > "$dst"
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
        PARAMS_MOUNT_DIR=""
        if [ -n "$HOST_PARAMS_DIR" ]; then
            PARAMS_MOUNT_DIR="$HOST_PARAMS_DIR"
            if [ -f "$HOST_PARAMS_DIR/$PARAMS_FILE" ]; then
                PARAMS_PARAM="/ws/params/$PARAMS_FILE"
            else
                echo "Warning: Params file not found: $HOST_PARAMS_DIR/$PARAMS_FILE. Using package default." >&2
            fi
        else
            echo "Warning: Params directory not found. Using package default." >&2
        fi

        # Override the AMCL initial pose by reading initpose.md.
        # The params file is generated in a temp dir (bind-mounted at /ws/params)
        # so the original yaml stays untouched.
        if [ -n "$HOST_PARAMS_DIR" ] && [ -f "$HOST_PARAMS_DIR/$PARAMS_FILE" ]; then
            INITPOSE_FILE=$(resolve_initpose_file "${RC_INITPOSE_FILE:-initpose.md}" || true)
            if [ -n "$INITPOSE_FILE" ]; then
                if read -r IP_X IP_Y IP_YAW < <(parse_initpose "$INITPOSE_FILE"); then
                    # 고정 이름 디렉터리를 쓴다. 이전 실행(예: sudo 실행, docker
                    # bind-mount 자동 생성)이 이 경로를 root 소유로 남기면 sticky /tmp
                    # 에서는 삭제할 수 없고, set -e 때문에 기동 전체가 중단된다.
                    # 삭제에 실패하면 매 실행 고유한 임시 디렉터리로 대체한다.
                    INITPOSE_OVERRIDE_DIR="${TMPDIR:-/tmp}/former_initpose_${ROBOT_ID}"
                    if ! rm -rf "$INITPOSE_OVERRIDE_DIR" 2>/dev/null; then
                        INITPOSE_OVERRIDE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/former_initpose_${ROBOT_ID}.XXXXXX")"
                    fi
                    mkdir -p "$INITPOSE_OVERRIDE_DIR"
                    apply_initpose_to_params "$HOST_PARAMS_DIR/$PARAMS_FILE" \
                        "$INITPOSE_OVERRIDE_DIR/$PARAMS_FILE" "$IP_X" "$IP_Y" "$IP_YAW"
                    PARAMS_MOUNT_DIR="$INITPOSE_OVERRIDE_DIR"
                    PARAMS_PARAM="/ws/params/$PARAMS_FILE"
                    echo "Applied initial pose from $INITPOSE_FILE: x=$IP_X, y=$IP_Y, yaw=$IP_YAW"
                else
                    echo "Warning: Failed to parse initial pose from $INITPOSE_FILE. Keeping params file values." >&2
                fi
            fi
        fi

        if [ -n "$PARAMS_MOUNT_DIR" ]; then
            DOCKER_VOLUMES+=("-v" "$PARAMS_MOUNT_DIR:/ws/params:ro")
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
            DOCKER_VOLUMES+=("-v" "$RVIZ_DIR:/ws/rviz:ro")
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

# GPU Acceleration options auto-detection
GPU_ARGS=()
if command -v nvidia-smi >/dev/null 2>&1; then
    GPU_ARGS=(
        "-e" "NVIDIA_DRIVER_CAPABILITIES=all"
        "-e" "NVIDIA_VISIBLE_DEVICES=all"
    )
    if docker run --help 2>&1 | grep -q "\-\-gpus"; then
        GPU_ARGS+=("--gpus" "all")
    fi
    echo "NVIDIA GPU detected. Activating GPU acceleration."
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
    # Keep the generated params dir in daemon mode: the container reads it
    # asynchronously after `docker run` returns.
    if [ "$DAEMON_MODE" != true ] && [ -n "${INITPOSE_OVERRIDE_DIR:-}" ] && [ -d "$INITPOSE_OVERRIDE_DIR" ]; then
        rm -rf "$INITPOSE_OVERRIDE_DIR" 2>/dev/null || true
    fi
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

# Execute docker run
# 추가 마운트/옵션 주입 (디버깅·핫픽스용). 예:
#   RC_EXTRA_VOLUMES="-v /path/bringup_launch.py:/root/dev_ws/install/former_navigation2/share/former_navigation2/launch/bringup_launch.py:ro"
if [ -n "${RC_EXTRA_VOLUMES:-}" ]; then
    # shellcheck disable=SC2206
    EXTRA_VOL_ARGS=($RC_EXTRA_VOLUMES)
    DOCKER_VOLUMES+=("${EXTRA_VOL_ARGS[@]}")
fi

docker run $RUN_MODE \
    --name "$CONTAINER_NAME" \
    --net host \
    --env ROS_DOMAIN_ID="$ROS_DOMAIN_ID" \
    -e DISPLAY="${DISPLAY:-}" \
    "${GPU_ARGS[@]}" \
    "${DOCKER_VOLUMES[@]}" \
    "$IMAGE_NAME:$IMAGE_TAG" \
    $CONTAINER_CMD "${EXTRA_ARGS[@]}"
