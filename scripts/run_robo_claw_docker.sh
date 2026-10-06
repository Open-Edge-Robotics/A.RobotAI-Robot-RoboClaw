#!/bin/bash
set -euo pipefail

IMAGE_NAME="lgecloudroboticstask/robo-claw"
TAG="latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 이 스크립트는 저장소 루트의 하위 scripts/에 있으므로 REPO_ROOT는 한 단계 위.
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# .env 파일 로드 (호스트에서 지정한 RC_ENV_FILE 우선 사용)
ENV_PATH="${RC_ENV_FILE:-}"
if [[ -z "${ENV_PATH}" ]]; then
  if [[ -f "${SCRIPT_DIR}/.env" ]]; then
    ENV_PATH="${SCRIPT_DIR}/.env"
  elif [[ -f "${SCRIPT_DIR}/../.env" ]]; then
    ENV_PATH="${SCRIPT_DIR}/../.env"
  elif [[ -f .env ]]; then
    ENV_PATH=".env"
  fi
fi

if [[ -n "${ENV_PATH}" && -f "${ENV_PATH}" ]]; then
  set -o allexport
  source "${ENV_PATH}"
  set +o allexport
fi

# Default Variables
ROBOT_CONFIG="${RC_ROBOT_CONFIG:-}"
CAMERA_TOPIC="${RC_CAMERA_TOPIC:-}"
USE_GRPC="${USE_GRPC:-${RC_USE_GRPC:-false}}"
USE_VISION="${USE_VISION:-${RC_USE_VISION:-true}}"
MODEL_NAME="${RC_MODEL_NAME:-yolov8n}"
# download_model.sh 기본 저장 위치(저장소 루트 models/)를 그대로 마운트한다.
# RC_MODELS_DIR로 오버라이드 가능 (download_model.sh와 동일한 변수명).
MODELS_DIR="${RC_MODELS_DIR:-${REPO_ROOT}/models}"
SOUL_FILE="${RC_ROBOT_SOUL_FILE:-}"
SKILLS_GUIDE="${RC_SKILLS_GUIDE_FILE:-}"
ROBOT_LIMITS="${RC_ROBOT_LIMITS_FILE:-}"
TROUBLESHOOTING="${RC_TROUBLESHOOTING_GUIDE_FILE:-}"
BUTLER_SCRIPTS_DIR_ARG="${RC_BUTLER_SCRIPTS_DIR:-}"
BUTLER_SOURCE_DIR_ARG="${RC_BUTLER_SOURCE_DIR:-}"
AGENT_WORKSPACE_DIR_ARG="${RC_AGENT_WORKSPACE_DIR:-}"
CONFIG_DIR_ARG="${RC_CONFIG_DIR:-}"
LAUNCH_DIR_ARG="${RC_LAUNCH_DIR:-}"
USER_CMD=()

usage() {
  printf 'Usage: %s [OPTIONS] [-- <command> ...]\n\n' "$0"
  printf 'Options:\n'
  printf '  --robot-config <name>      Set robot_config for launch.\n'
  printf '  --camera-topic <topic>     Override camera_topic for launch.\n'
  printf '  --use-grpc, --grpc         Enable gRPC node.\n'
  printf '  --use-vision, --vision     Enable vision node.\n'
  printf '  --soul-file, --soul <path> Host path to agent soul (e.g., ROBOT.md).\n'
  printf '  --skills-guide, --guide-file <path> Host path to skills guide (e.g., SKILLS.md).\n'
  printf '  --robot-limits <path>      Host path to robot limits specification (e.g., ROBOT_LIMITS.json).\n'
  printf '  --troubleshooting <path>   Host path to troubleshooting guide (e.g., TROUBLESHOOTING.md).\n'
  printf '  --butler-scripts <path>    Host path to Butler scripts for LLM.\n'
  printf '  --butler-source <path>     Host path to Butler ROS 2 workspace (for sourcing).\n'
  printf '  --agent-workspace <path>   Host path to agent workspace.\n'
  printf '  --config-dir <path>        Host path to robo_claw_bringup config dir.\n'
  printf '  --launch-dir <path>        Host path to robo_claw_bringup launch dir.\n'
  printf '  --help, -h                 Show this help message.\n'
}

# Parse Arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
  --robot-config)
    ROBOT_CONFIG="${2:-}"
    shift 2
    ;;
  --camera-topic)
    CAMERA_TOPIC="${2:-}"
    shift 2
    ;;
  --soul-file | --soul)
    SOUL_FILE="${2:-}"
    shift 2
    ;;
  --skills-guide | --guide-file)
    SKILLS_GUIDE="${2:-}"
    shift 2
    ;;
  --robot-limits)
    ROBOT_LIMITS="${2:-}"
    shift 2
    ;;
  --troubleshooting)
    TROUBLESHOOTING="${2:-}"
    shift 2
    ;;
  --butler-scripts)
    BUTLER_SCRIPTS_DIR_ARG="${2:-}"
    shift 2
    ;;
  --butler-source)
    BUTLER_SOURCE_DIR_ARG="${2:-}"
    shift 2
    ;;
  --agent-workspace)
    AGENT_WORKSPACE_DIR_ARG="${2:-}"
    shift 2
    ;;
  --config-dir)
    CONFIG_DIR_ARG="${2:-}"
    shift 2
    ;;
  --launch-dir)
    LAUNCH_DIR_ARG="${2:-}"
    shift 2
    ;;
  --use-grpc | --grpc)
    USE_GRPC="true"
    shift
    ;;
  --use-vision | --vision)
    USE_VISION="true"
    shift
    ;;
  --help | -h)
    usage
    exit 0
    ;;
  --)
    shift
    USER_CMD=("$@")
    break
    ;;
  -*)
    printf "Unknown option: %s\n" "$1" >&2
    usage
    exit 1
    ;;
  *)
    USER_CMD=("$@")
    break
    ;;
  esac
done

# 경로 및 변수 설정
# 1. SOUL_FILE
DEFAULT_SOUL="$(pwd)/src/robo_claw_bringup/config/ROBOT.md"
SOUL_FILE="${SOUL_FILE:-${RC_ROBOT_SOUL_FILE:-}}"
if [[ -z "$SOUL_FILE" && -f "$DEFAULT_SOUL" ]]; then
  SOUL_FILE="$DEFAULT_SOUL"
fi

if [[ -n "$SOUL_FILE" ]]; then
  if [[ -f "$SOUL_FILE" ]]; then
    SOUL_FILE=$(realpath "$SOUL_FILE")
  else
    echo "Warning: Robot soul file not found at $SOUL_FILE" >&2
    SOUL_FILE=""
  fi
fi

# 1.5 SKILLS_GUIDE
DEFAULT_SKILLS_GUIDE="$(pwd)/src/robo_claw_bringup/config/SKILLS.md"
SKILLS_GUIDE="${SKILLS_GUIDE:-${RC_SKILLS_GUIDE_FILE:-}}"
if [[ -z "$SKILLS_GUIDE" && -f "$DEFAULT_SKILLS_GUIDE" ]]; then
  SKILLS_GUIDE="$DEFAULT_SKILLS_GUIDE"
fi

if [[ -n "$SKILLS_GUIDE" ]]; then
  if [[ -f "$SKILLS_GUIDE" ]]; then
    SKILLS_GUIDE=$(realpath "$SKILLS_GUIDE")
  else
    echo "Warning: Skills guide file not found at $SKILLS_GUIDE" >&2
    SKILLS_GUIDE=""
  fi
fi

# 1.6 ROBOT_LIMITS
DEFAULT_ROBOT_LIMITS="$(pwd)/src/robo_claw_bringup/config/ROBOT_LIMITS.json"
ROBOT_LIMITS="${ROBOT_LIMITS:-${RC_ROBOT_LIMITS_FILE:-}}"
if [[ -z "$ROBOT_LIMITS" && -f "$DEFAULT_ROBOT_LIMITS" ]]; then
  ROBOT_LIMITS="$DEFAULT_ROBOT_LIMITS"
fi

if [[ -n "$ROBOT_LIMITS" ]]; then
  if [[ -f "$ROBOT_LIMITS" ]]; then
    ROBOT_LIMITS=$(realpath "$ROBOT_LIMITS")
  else
    echo "Warning: Robot limits file not found at $ROBOT_LIMITS" >&2
    ROBOT_LIMITS=""
  fi
fi

# 1.7 TROUBLESHOOTING
DEFAULT_TROUBLESHOOTING="$(pwd)/src/robo_claw_bringup/config/TROUBLESHOOTING.md"
TROUBLESHOOTING="${TROUBLESHOOTING:-${RC_TROUBLESHOOTING_GUIDE_FILE:-}}"
if [[ -z "$TROUBLESHOOTING" && -f "$DEFAULT_TROUBLESHOOTING" ]]; then
  TROUBLESHOOTING="$DEFAULT_TROUBLESHOOTING"
fi

if [[ -n "$TROUBLESHOOTING" ]]; then
  if [[ -f "$TROUBLESHOOTING" ]]; then
    TROUBLESHOOTING=$(realpath "$TROUBLESHOOTING")
  else
    echo "Warning: Troubleshooting file not found at $TROUBLESHOOTING" >&2
    TROUBLESHOOTING=""
  fi
fi

# 2. BUTLER_SCRIPTS_DIR (LLM 참조용)
DEFAULT_SCRIPTS_DIR="$(pwd)/butler_scripts"
FALLBACK_SCRIPTS_DIR="/home/seoyc/Workspace/ros/butler/products/prd_butler_v01_magok_w02/script"
BUTLER_SCRIPTS_DIR="${BUTLER_SCRIPTS_DIR_ARG:-${BUTLER_SCRIPTS_DIR:-}}"
if [[ -z "$BUTLER_SCRIPTS_DIR" ]]; then
  if [[ -d "$DEFAULT_SCRIPTS_DIR" ]]; then
    BUTLER_SCRIPTS_DIR="$DEFAULT_SCRIPTS_DIR"
  else
    BUTLER_SCRIPTS_DIR="$FALLBACK_SCRIPTS_DIR"
  fi
fi

if [[ -n "$BUTLER_SCRIPTS_DIR" ]]; then
  if [[ -d "$BUTLER_SCRIPTS_DIR" ]]; then
    BUTLER_SCRIPTS_DIR=$(realpath "$BUTLER_SCRIPTS_DIR")
  else
    echo "Warning: Butler scripts directory not found at $BUTLER_SCRIPTS_DIR" >&2
    BUTLER_SCRIPTS_DIR=""
  fi
fi

# 2.5 AGENT_WORKSPACE_DIR (LLM 전용 작업 공간)
AGENT_WORKSPACE_DIR="${AGENT_WORKSPACE_DIR_ARG:-${RC_AGENT_WORKSPACE_DIR:-}}"
if [[ -z "$AGENT_WORKSPACE_DIR" ]]; then
  AGENT_WORKSPACE_DIR="$(pwd)/agent_workspace"
fi

if [[ -n "$AGENT_WORKSPACE_DIR" ]]; then
  mkdir -p "$AGENT_WORKSPACE_DIR"
  AGENT_WORKSPACE_DIR=$(realpath "$AGENT_WORKSPACE_DIR")
fi

# 3. BUTLER_SOURCE_DIR (빌드/소싱용 워크스페이스)
BUTLER_SOURCE_DIR="${BUTLER_SOURCE_DIR_ARG:-${RC_BUTLER_SOURCE_DIR:-}}"
if [[ -n "$BUTLER_SOURCE_DIR" ]]; then
  if [[ -d "$BUTLER_SOURCE_DIR" ]]; then
    BUTLER_SOURCE_DIR=$(realpath "$BUTLER_SOURCE_DIR")
  else
    echo "Warning: Butler source directory not found at $BUTLER_SOURCE_DIR" >&2
    BUTLER_SOURCE_DIR=""
  fi
fi

# 4. CONFIG_DIR
DEFAULT_CONFIG_DIR="${REPO_ROOT}/src/robo_claw_bringup/config"
CONFIG_DIR="${CONFIG_DIR_ARG:-${RC_CONFIG_DIR:-$DEFAULT_CONFIG_DIR}}"

if [[ -n "$CONFIG_DIR" ]]; then
  if [[ -d "$CONFIG_DIR" ]]; then
    CONFIG_DIR=$(realpath "$CONFIG_DIR")
  else
    echo "Warning: Config directory not found at $CONFIG_DIR" >&2
    CONFIG_DIR=""
  fi
fi

# 5. LAUNCH_DIR — 런치 파일 수정을 이미지 재빌드 없이 컨테이너에 반영
DEFAULT_LAUNCH_DIR="${REPO_ROOT}/src/robo_claw_bringup/launch"
LAUNCH_DIR="${LAUNCH_DIR_ARG:-${RC_LAUNCH_DIR:-$DEFAULT_LAUNCH_DIR}}"

if [[ -n "$LAUNCH_DIR" ]]; then
  if [[ -d "$LAUNCH_DIR" ]]; then
    LAUNCH_DIR=$(realpath "$LAUNCH_DIR")
  else
    echo "Warning: Launch directory not found at $LAUNCH_DIR" >&2
    LAUNCH_DIR=""
  fi
fi

# 도커 옵션 및 실행 명령어 구성
DOCKER_VOLUMES=("-v" "$MODELS_DIR:/ros2_ws/models:ro")

if [[ -n "$CONFIG_DIR" ]]; then
  DOCKER_VOLUMES+=("-v" "$CONFIG_DIR:/ros2_ws/install/robo_claw_bringup/share/robo_claw_bringup/config:ro")
fi
if [[ -n "$LAUNCH_DIR" ]]; then
  DOCKER_VOLUMES+=("-v" "$LAUNCH_DIR:/ros2_ws/install/robo_claw_bringup/share/robo_claw_bringup/launch:ro")
fi
if [[ -n "$BUTLER_SCRIPTS_DIR" ]]; then
  DOCKER_VOLUMES+=("-v" "$BUTLER_SCRIPTS_DIR:$BUTLER_SCRIPTS_DIR:rw")
fi
if [[ -n "$AGENT_WORKSPACE_DIR" ]]; then
  DOCKER_VOLUMES+=("-v" "$AGENT_WORKSPACE_DIR:/ros2_ws/agent_workspace:rw")
fi
if [[ -n "$BUTLER_SOURCE_DIR" ]]; then
  DOCKER_VOLUMES+=("-v" "$BUTLER_SOURCE_DIR:$BUTLER_SOURCE_DIR:rw")
fi

if [[ -n "$SOUL_FILE" ]]; then
  DOCKER_VOLUMES+=("-v" "$SOUL_FILE:/ros2_ws/config/ROBOT.md:ro")
fi

if [[ -n "$SKILLS_GUIDE" ]]; then
  DOCKER_VOLUMES+=("-v" "$SKILLS_GUIDE:/ros2_ws/config/SKILLS.md:ro")
fi

if [[ -n "$ROBOT_LIMITS" ]]; then
  DOCKER_VOLUMES+=("-v" "$ROBOT_LIMITS:/ros2_ws/config/ROBOT_LIMITS.json:ro")
fi

if [[ -n "$TROUBLESHOOTING" ]]; then
  DOCKER_VOLUMES+=("-v" "$TROUBLESHOOTING:/ros2_ws/config/TROUBLESHOOTING.md:ro")
fi

if [[ -n "${RC_SYSTEM_PROMPT_FILE:-}" && -f "${RC_SYSTEM_PROMPT_FILE}" ]]; then
  DOCKER_VOLUMES+=("-v" "${RC_SYSTEM_PROMPT_FILE}:/ros2_ws/config/system_prompt.md:ro")
fi

# Launch 명령어 구성 (USER_CMD가 없으면 기본 launch)
BASE_CMD=()
if [[ ${#USER_CMD[@]} -gt 0 ]]; then
  BASE_CMD=("${USER_CMD[@]}")
else
  BASE_CMD=("ros2" "launch" "robo_claw_bringup" "robo_claw.launch.py" "use_channel:=true")

  if [[ -n "$ROBOT_CONFIG" ]]; then BASE_CMD+=("robot_config:=$ROBOT_CONFIG"); fi
  if [[ -n "$CAMERA_TOPIC" ]]; then BASE_CMD+=("camera_topic:=$CAMERA_TOPIC"); fi
  if [[ "$USE_GRPC" == "true" ]]; then BASE_CMD+=("use_grpc:=true"); fi
  if [[ "$USE_VISION" == "true" ]]; then
    BASE_CMD+=("use_vision:=true" "vision_model_path:=/ros2_ws/models/${MODEL_NAME}.onnx")
  fi

  # 환경 변수를 런치 파라미터로 매핑하는 헬퍼 함수
  add_param() {
    local env_val="${1:-}"
    local param_name="${2:-}"
    if [[ -n "$env_val" && -n "$param_name" ]]; then
      BASE_CMD+=("${param_name}:=${env_val}")
    fi
  }

  add_param "${RC_LLM_PROVIDER:-}" "llm_provider"
  add_param "${RC_LLM_MODEL:-}" "llm_model"
  add_param "${RC_LLM_EMBEDDING_MODEL:-}" "llm_embedding_model"
  add_param "${RC_LLM_EMBEDDING_PROVIDER:-}" "llm_embedding_provider"
  add_param "${RC_LLM_EMBEDDING_BASE_URL:-}" "llm_embedding_base_url"
  add_param "${RC_LLM_EMBEDDING_API_KEY:-}" "llm_embedding_api_key"
  add_param "${RC_OLLAMA_BASE_URL:-}" "llm_base_url"
  add_param "${RC_OLLAMA_OPTIONS_JSON:-}" "ollama_options_json"
  add_param "${RC_ENABLE_RAG:-}" "enable_rag"
  add_param "${RC_RAG_VECTOR_BACKEND:-}" "rag_vector_backend"
  add_param "${RC_RAG_TOP_K:-}" "rag_top_k"
  add_param "${RC_RAG_SCORE_THRESHOLD:-}" "rag_score_threshold"
  add_param "${RC_RAG_LOCAL_MIRROR:-}" "rag_local_mirror"
  add_param "${RC_ENABLE_SKILL_LEARNING:-}" "enable_skill_learning"
  add_param "${RC_SKILL_LEARNING_SUCCESS_SAMPLE_RATE:-}" "skill_learning_success_sample_rate"
  add_param "${RC_SKILL_LEARNING_REFLECT_INTERVAL_SEC:-}" "skill_learning_reflect_interval_sec"
  add_param "${RC_TASK_QUEUE_MAX_SIZE:-}" "task_queue_max_size"
  add_param "${RC_LLM_FAIL_FAST:-}" "llm_fail_fast"
  add_param "${RC_STRICT_CONFIG:-}" "strict_config"
  add_param "${RC_ENABLE_TASK_DECOMPOSITION:-}" "enable_task_decomposition"
  add_param "${RC_TASK_DECOMPOSITION_MAX_STEPS:-}" "task_decomposition_max_steps"
  add_param "${RC_TASK_STEP_MAX_RETRIES:-}" "task_step_max_retries"
  add_param "${RC_TASK_DECOMPOSITION_WAIT_MARGIN_CAP_SEC:-}" "task_decomposition_wait_margin_cap_sec"
  add_param "${QDRANT_URL:-}" "qdrant_url"
  add_param "${QDRANT_API_KEY:-}" "qdrant_api_key"
  add_param "${RC_QDRANT_COLLECTION:-}" "qdrant_collection"
  add_param "${RC_QDRANT_TIMEOUT_SEC:-}" "qdrant_timeout_sec"
  add_param "${AZURE_OPENAI_ENDPOINT:-}" "azure_endpoint"
  add_param "${AZURE_OPENAI_API_KEY:-}" "azure_api_key"
  add_param "${OPENAI_API_KEY:-}" "openai_api_key"
  add_param "${ANTHROPIC_API_KEY:-}" "anthropic_api_key"
  add_param "${RC_HTTP_HOST:-}" "http_host"
  add_param "${RC_HTTP_PORT:-}" "http_port"
  add_param "${RC_HTTP_READONLY_TOKEN:-}" "http_readonly_token"
  add_param "${RC_HTTP_CONTROL_TOKEN:-}" "http_control_token"
  add_param "${RC_HTTP_RATE_LIMIT_PER_MINUTE:-}" "http_rate_limit_per_minute"
  add_param "${RC_ROBOT_DESCRIPTION_FILE:-}" "robot_description_file"
  add_param "${RC_LIDAR_TOPIC:-}" "lidar_topic"
  add_param "${RC_IMU_TOPIC:-}" "imu_topic"

  # JSON 문자열 공백 제거 후 파라미터 추가
  val_cidrs="${RC_HTTP_ALLOWED_CIDRS_JSON:-}"
  add_param "${val_cidrs//[[:space:]]/}" "http_allowed_cidrs_json"
  val_allowed="${RC_HTTP_ALLOWED_SKILLS_JSON:-}"
  add_param "${val_allowed//[[:space:]]/}" "http_allowed_skills_json"
  val_blocked="${RC_HTTP_BLOCKED_SKILLS_JSON:-}"
  add_param "${val_blocked//[[:space:]]/}" "http_blocked_skills_json"
  val_mcp_servers="${RC_MCP_SERVERS_JSON:-}"
  add_param "${RC_ENABLE_MCP:-}" "mcp_enabled"
  add_param "${val_mcp_servers//[[:space:]]/}" "mcp_servers_json"

  add_param "${ENABLE_DISCORD:-}" "enable_discord"
  add_param "${ENABLE_TELEGRAM:-}" "enable_telegram"
  add_param "${ENABLE_SLACK:-}" "enable_slack"
  add_param "${ENABLE_GRPC:-}" "enable_grpc"
  add_param "${USE_GRPC:-}" "use_grpc"
  add_param "${ENABLE_GRPC_CLIENT:-}" "use_client"
  add_param "${GRPC_TARGET_HOST:-}" "target_host"
  add_param "${GRPC_TARGET_PORT:-}" "target_port"
  grpc_target_peers_json="${GRPC_TARGET_PEERS_JSON:-}"
  add_param "${grpc_target_peers_json//[[:space:]]/}" "target_peers_json"

  # gRPC 피어 인증/전송 설정 — Go CLI(BuildCommonLaunchArgs)와 동일한 매핑.
  # 누락되면 컨테이너 안에서 토큰이 적용되지 않아 피어 인증이 꺼진 채로 뜬다.
  add_param "${GRPC_PEER_TOKEN:-}" "grpc_peer_token"
  grpc_peer_tokens_json="${GRPC_PEER_TOKENS_JSON:-}"
  add_param "${grpc_peer_tokens_json//[[:space:]]/}" "grpc_peer_tokens_json"
  add_param "${GRPC_MAX_FILE_BYTES:-}" "grpc_max_file_bytes"
  add_param "${GRPC_DEDUP_PATH:-}" "grpc_dedup_path"

  add_param "${TELEGRAM_BOT_TOKEN:-}" "telegram_token"
  add_param "${SLACK_APP_TOKEN:-}" "slack_app_token"
  add_param "${SLACK_BOT_TOKEN:-}" "slack_bot_token"
  add_param "${DISCORD_BOT_TOKEN:-}" "discord_token"

  if [[ -n "$SOUL_FILE" ]]; then
    BASE_CMD+=("robot_soul_file:=/ros2_ws/config/ROBOT.md")
  fi
  if [[ -n "$SKILLS_GUIDE" ]]; then
    BASE_CMD+=("skills_guide_file:=/ros2_ws/config/SKILLS.md")
  fi
  if [[ -n "$ROBOT_LIMITS" ]]; then
    BASE_CMD+=("robot_limits_file:=/ros2_ws/config/ROBOT_LIMITS.json")
  fi
  if [[ -n "$TROUBLESHOOTING" ]]; then
    BASE_CMD+=("troubleshooting_guide_file:=/ros2_ws/config/TROUBLESHOOTING.md")
  fi
  if [[ -n "${RC_SYSTEM_PROMPT_FILE:-}" && -f "${RC_SYSTEM_PROMPT_FILE}" ]]; then
    BASE_CMD+=("system_prompt_file:=/ros2_ws/config/system_prompt.md")
  fi
  if [[ -n "$BUTLER_SCRIPTS_DIR" ]]; then
    BASE_CMD+=("butler_script_dir:=$BUTLER_SCRIPTS_DIR")
  fi
  if [[ -n "$AGENT_WORKSPACE_DIR" ]]; then
    BASE_CMD+=("agent_workspace_dir:=/ros2_ws/agent_workspace")
  fi
fi

# 소싱 래핑
CONTAINER_CMD=()
if [[ -n "$BUTLER_SOURCE_DIR" ]]; then
  # Butler 워크스페이스의 setup.bash를 소싱하기 전 기본 ROS 및 ros2_ws 환경을 소싱하여 라이브러리 경로 보존
  CONTAINER_CMD=(
    "bash" "-c"
    "if [ -f /opt/ros/humble/setup.bash ]; then source /opt/ros/humble/setup.bash; fi && if [ -f /ros2_ws/install/setup.bash ]; then source /ros2_ws/install/setup.bash; fi && if [ -f $BUTLER_SOURCE_DIR/install/setup.bash ]; then source $BUTLER_SOURCE_DIR/install/setup.bash; fi && exec \"\$0\" \"\$@\""
    "${BASE_CMD[@]}"
  )
else
  CONTAINER_CMD=("${BASE_CMD[@]}")
fi

# 도커 이미지 빌드
if [[ -z "$(docker images -q "$IMAGE_NAME:$TAG" 2>/dev/null)" ]]; then
  echo "Building docker image $IMAGE_NAME:$TAG..."
  docker build \
    --build-arg BUILD_DATE="$(date +'%Y-%m-%d %H:%M:%S %Z')" \
    --build-arg GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" \
    --build-arg IMAGE_TAG="$TAG" \
    -t "$IMAGE_NAME:$TAG" .
fi

# X11 포워딩 설정
if command -v xhost >/dev/null 2>&1; then
  xhost +local:docker >/dev/null 2>&1 || true
fi

# 환경 변수 파일 확인
ENV_ARGS=()
if [[ -n "${ENV_PATH}" && -f "${ENV_PATH}" ]]; then
  ENV_ARGS=(--env-file "${ENV_PATH}")
fi

# 도커 실행
echo "Starting $IMAGE_NAME:$TAG..."
docker run -it --rm \
  --name robo_claw_container \
  --network host \
  --privileged \
  -e DISPLAY="${DISPLAY:-}" \
  -e TZ="${TZ:-${RC_TZ:-Asia/Seoul}}" \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v /dev:/dev \
  -v /sys/class/power_supply:/sys/class/power_supply:ro \
  -v /etc/localtime:/etc/localtime:ro \
  "${DOCKER_VOLUMES[@]}" \
  "${ENV_ARGS[@]}" \
  "$IMAGE_NAME:$TAG" \
  "${CONTAINER_CMD[@]}"

# X11 권한 회수
if command -v xhost >/dev/null 2>&1; then
  xhost -local:docker >/dev/null 2>&1 || true
fi
