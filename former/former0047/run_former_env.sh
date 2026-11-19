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
    printf "\nEnvironment (nav2 initial pose):\n"
    printf "  RC_INITPOSE_SOURCE     auto (default: last_pose.yaml, then initpose.md) | last_pose | initpose | none\n"
    printf "  RC_LAST_POSE_FILE      Host path of last_pose.yaml saved by the RoboClaw agent\n"
    printf "                         (default: \$RC_MEMORY_DIR, \$RC_WS or ~/workspace/A.RobotAI-Robot-RoboClaw/agent_workspace/memory)\n"
    printf "  RC_LAST_POSE_MAX_AGE_H Ignore last_pose.yaml older than N hours (default: 0 = no limit)\n"
    printf "  RC_INITPOSE_FILE       initpose.md file name or path (default: initpose.md)\n"
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

# Resolve last_pose.yaml written by the RoboClaw agent into its memory dir
# (container /ros2_ws/memory, host default <RoboClaw repo>/agent_workspace/memory).
# Priority: RC_LAST_POSE_FILE -> RC_MEMORY_DIR -> RC_WS -> default workspace path.
resolve_last_pose_file() {
    local candidates=()
    if [ -n "${RC_LAST_POSE_FILE:-}" ]; then
        candidates=("$RC_LAST_POSE_FILE")
    else
        if [ -n "${RC_MEMORY_DIR:-}" ]; then
            candidates+=("$RC_MEMORY_DIR/last_pose.yaml")
        fi
        if [ -n "${RC_WS:-}" ]; then
            candidates+=("$RC_WS/agent_workspace/memory/last_pose.yaml")
        fi
        candidates+=("$FORMER_HOME/workspace/A.RobotAI-Robot-RoboClaw/agent_workspace/memory/last_pose.yaml")
    fi

    local f
    for f in "${candidates[@]}"; do
        if [ -f "$f" ] && [ -r "$f" ]; then
            echo "$f"
            return 0
        fi
    done
    return 1
}

# Print the value of a flat "key: value" line (surrounding double quotes stripped).
last_pose_value() {
    local file="$1"
    local key="$2"
    sed -nE "s/^${key}:[[:space:]]*(.*)\$/\\1/p" "$file" | head -n1 \
        | sed -E 's/[[:space:]]+$//; s/^"(.*)"$/\1/'
}

# Validate last_pose.yaml against the map being launched and print "x y yaw".
# Rejects: non-numeric values, non-map frame, a different map, and (when
# RC_LAST_POSE_MAX_AGE_H > 0) a pose older than that many hours.
select_last_pose() {
    local file="$1"
    local current_map="$2"
    local num_re='^[-+]?[0-9]*\.?[0-9]+$'
    local x y yaw frame map updated

    x=$(last_pose_value "$file" x)
    y=$(last_pose_value "$file" y)
    yaw=$(last_pose_value "$file" yaw)
    frame=$(last_pose_value "$file" frame)
    map=$(last_pose_value "$file" map)
    updated=$(last_pose_value "$file" updated_at)

    if ! [[ "$x" =~ $num_re && "$y" =~ $num_re && "$yaw" =~ $num_re ]]; then
        echo "Warning: Invalid pose in $file. Ignoring last pose." >&2
        return 1
    fi
    if [ -n "$frame" ] && [ "$frame" != "map" ]; then
        echo "Warning: Last pose frame is '$frame' (not map). Ignoring last pose." >&2
        return 1
    fi
    if [ -z "$map" ]; then
        echo "Warning: Last pose has no map name; assuming it matches $current_map." >&2
    elif [ "$map" != "$current_map" ]; then
        echo "Notice: Last pose was saved on map '$map', launching '$current_map'. Ignoring last pose." >&2
        return 1
    fi

    local max_age_h="${RC_LAST_POSE_MAX_AGE_H:-0}"
    if [[ "$max_age_h" =~ ^[0-9]+$ ]] && [ "$max_age_h" -gt 0 ]; then
        local saved_ts now_ts
        if saved_ts=$(date -d "$updated" +%s 2>/dev/null); then
            now_ts=$(date +%s)
            if [ $((now_ts - saved_ts)) -gt $((max_age_h * 3600)) ]; then
                echo "Notice: Last pose is older than ${max_age_h}h ($updated). Ignoring last pose." >&2
                return 1
            fi
        else
            echo "Warning: Cannot parse last pose timestamp '$updated'; skipping age check." >&2
        fi
    fi

    printf '%s %s %s\n' "$x" "$y" "$yaw"
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

        # Override the AMCL initial pose (amcl.initial_pose in the params file).
        # RC_INITPOSE_SOURCE:
        #   auto (default) - last_pose.yaml saved by the RoboClaw agent when it is valid
        #                    and was recorded on the same map, otherwise initpose.md
        #   last_pose      - only last_pose.yaml
        #   initpose       - only initpose.md (previous behavior)
        #   none           - keep the params file values
        # The params file is generated in a temp dir (bind-mounted at /ws/params)
        # so the original yaml stays untouched.
        INITPOSE_SOURCE="${RC_INITPOSE_SOURCE:-auto}"
        case "$INITPOSE_SOURCE" in
            auto|last_pose|initpose|none) ;;
            *)
                echo "Warning: Unknown RC_INITPOSE_SOURCE='$INITPOSE_SOURCE'. Using auto." >&2
                INITPOSE_SOURCE="auto"
                ;;
        esac
        IP_X=""
        IP_Y=""
        IP_YAW=""
        IP_FROM=""
        if [ "$INITPOSE_SOURCE" != "none" ] && [ -n "$HOST_PARAMS_DIR" ] && [ -f "$HOST_PARAMS_DIR/$PARAMS_FILE" ]; then
            if [ "$INITPOSE_SOURCE" = "auto" ] || [ "$INITPOSE_SOURCE" = "last_pose" ]; then
                LAST_POSE_FILE=$(resolve_last_pose_file || true)
                if [ -n "$LAST_POSE_FILE" ]; then
                    if read -r IP_X IP_Y IP_YAW < <(select_last_pose "$LAST_POSE_FILE" "$MAP_HOST_BASENAME"); then
                        IP_FROM="$LAST_POSE_FILE"
                    fi
                else
                    echo "Notice: last_pose.yaml not found (set RC_LAST_POSE_FILE to its host path)." >&2
                fi
            fi
            if [ -z "$IP_FROM" ] && [ "$INITPOSE_SOURCE" != "last_pose" ]; then
                INITPOSE_FILE=$(resolve_initpose_file "${RC_INITPOSE_FILE:-initpose.md}" || true)
                if [ -n "$INITPOSE_FILE" ]; then
                    if read -r IP_X IP_Y IP_YAW < <(parse_initpose "$INITPOSE_FILE"); then
                        IP_FROM="$INITPOSE_FILE"
                    else
                        echo "Warning: Failed to parse initial pose from $INITPOSE_FILE. Keeping params file values." >&2
                    fi
                fi
            fi
            if [ -n "$IP_FROM" ]; then
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
                echo "Applied initial pose from $IP_FROM: x=$IP_X, y=$IP_Y, yaw=$IP_YAW"
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
#
# TTY 플래그는 실제 터미널이 붙어 있을 때만 쓴다. systemd 처럼 stdin/stdout 이
# TTY 가 아닌 환경에서 -t 를 붙이면 docker 가
#   the input device is not a TTY
# 로 즉시 실패하므로 -i 만 남긴다. (부팅 자동 시작 unit 이 이 경로를 탄다)
TTY_FLAGS="-it"
if [ ! -t 0 ] || [ ! -t 1 ]; then
    TTY_FLAGS="-i"
fi

RUN_MODE="$TTY_FLAGS"
if [ "$DAEMON_MODE" = true ] && { [ "$COMMAND" = "nav2" ] || [ "$COMMAND" = "slam" ]; }; then
    RUN_MODE="${TTY_FLAGS}d"
    echo "Starting container '$CONTAINER_NAME' in daemon (background) mode..."
else
    RUN_MODE="$TTY_FLAGS --rm"
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
echo "Target DISPLAY: $TARGET_DISPLAY"

# Fontconfig 정리 및 컨테이너 명령 exec 래핑 (컨테이너 내부 urw-*.conf 0바이트 파일 삭제)
FINAL_EXEC_CMD=(
    "bash" "-c"
    "find /usr/share/fontconfig/conf.avail/ -name 'urw-*.conf' -size 0 -delete 2>/dev/null; exec $CONTAINER_CMD \"\$@\""
    "_"
)

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
    -e "DISPLAY=$TARGET_DISPLAY" \
    -e "XDG_RUNTIME_DIR=/tmp/runtime-root" \
    -e "QT_X11_NO_MITSHM=1" \
    "${GPU_ARGS[@]}" \
    "${DOCKER_VOLUMES[@]}" \
    "$IMAGE_NAME:$IMAGE_TAG" \
    "${FINAL_EXEC_CMD[@]}" "${EXTRA_ARGS[@]}"