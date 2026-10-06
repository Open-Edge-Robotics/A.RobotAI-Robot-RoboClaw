#!/usr/bin/env bash
# YOLOv8 ONNX 모델 다운로드 스크립트
# 사용법: ./download_model.sh [모델크기]
#   모델크기: n (기본값) | s | m | l | x
# 예시:
#   ./download_model.sh       # yolov8n (가장 가벼움)
#   ./download_model.sh s     # yolov8s
#   ./download_model.sh m     # yolov8m

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 기본값은 이 스크립트가 있는 저장소 루트의 models/ (docker 실행 스크립트가 마운트하는
# 경로와 동일). RC_MODELS_DIR로 오버라이드 가능 — run_robo_claw_docker.sh/
# run_former_docker.sh와 동일한 변수를 사용해 다운로드 위치와 마운트 위치를 일치시킨다.
MODEL_DIR="${RC_MODELS_DIR:-${SCRIPT_DIR}/models}"
MODEL_SIZE="${1:-n}"
MODEL_NAME="yolov8${MODEL_SIZE}"
PT_FILE="${MODEL_DIR}/${MODEL_NAME}.pt"
ONNX_FILE="${MODEL_DIR}/${MODEL_NAME}.onnx"

# 모델 크기 검증
VALID_SIZES=("n" "s" "m" "l" "x")
VALID=false
for s in "${VALID_SIZES[@]}"; do
  [[ "${MODEL_SIZE}" == "${s}" ]] && VALID=true && break
done
if [[ "${VALID}" == false ]]; then
  echo "[오류] 지원하지 않는 모델 크기: ${MODEL_SIZE}"
  echo "  지원: n | s | m | l | x"
  exit 1
fi

# 성능 정보 출력
declare -A MODEL_INFO=(
  ["n"]="~6MB  | 최고 속도 | 낮은 정확도  (임베디드/실시간 추천)"
  ["s"]="~22MB | 빠름      | 보통 정확도"
  ["m"]="~52MB | 보통      | 높은 정확도"
  ["l"]="~87MB | 느림      | 더 높은 정확도"
  ["x"]="~136MB| 가장 느림 | 최고 정확도"
)
echo "======================================"
echo "  YOLOv8 ONNX 모델 다운로더"
echo "======================================"
echo "  모델  : ${MODEL_NAME}"
echo "  크기  : ${MODEL_INFO[${MODEL_SIZE}]}"
echo "  출력  : ${ONNX_FILE}"
echo "======================================"
echo ""

# 이미 존재하면 스킵
if [[ -f "${ONNX_FILE}" ]]; then
  echo "[완료] ONNX 파일이 이미 존재합니다: ${ONNX_FILE}"
  echo "  강제 재생성하려면 파일을 삭제 후 다시 실행하세요."
  exit 0
fi

# models 디렉토리 생성
mkdir -p "${MODEL_DIR}"

# uv 확인
if ! command -v uv &>/dev/null; then
  echo "[오류] uv 가 없습니다. https://docs.astral.sh/uv/ 를 참고해 설치하세요."
  echo "  빠른 설치: curl -LsSf https://astral.sh/uv/install.sh | sh"
  exit 1
fi

# ultralytics 설치 확인
if ! uv run python3 -c "import ultralytics" &>/dev/null; then
  echo "[정보] ultralytics가 없습니다. dev 의존성으로 추가합니다..."
  uv add --dev ultralytics
fi

# .pt 다운로드 (없는 경우)
if [[ ! -f "${PT_FILE}" ]]; then
  echo "[다운로드] ${MODEL_NAME}.pt ..."
  PT_URL="https://github.com/ultralytics/assets/releases/download/v8.2.0/${MODEL_NAME}.pt"
  if command -v wget &>/dev/null; then
    wget -q --show-progress -O "${PT_FILE}" "${PT_URL}"
  elif command -v curl &>/dev/null; then
    curl -L --progress-bar -o "${PT_FILE}" "${PT_URL}"
  else
    echo "[오류] wget 또는 curl 이 필요합니다."
    exit 1
  fi
  echo "[완료] ${PT_FILE} 다운로드 완료"
fi

# ONNX export
echo "[변환] ${MODEL_NAME}.pt → ONNX (opset=12) ..."
uv run python3 - <<PYEOF
from ultralytics import YOLO
import shutil, os

model = YOLO("${PT_FILE}")
model.export(format="onnx", opset=12, simplify=True)

# onnx 파일 이동
exported = "${PT_FILE}".replace(".pt", ".onnx")
dest = "${ONNX_FILE}"
if exported != dest:
    shutil.move(exported, dest)

print(f"ONNX 파일 저장: {dest}")
print(f"파일 크기: {os.path.getsize(dest) / 1024 / 1024:.1f} MB")
PYEOF

# .pt 파일 정리 (선택)
echo ""
read -r -p "[선택] .pt 파일을 삭제하시겠습니까? (용량 절약) [y/N]: " CLEANUP
if [[ "${CLEANUP}" =~ ^[Yy]$ ]]; then
  rm -f "${PT_FILE}"
  echo "[정리] ${PT_FILE} 삭제 완료"
fi

# 완료
echo ""
echo "======================================"
echo "  완료!"
echo "  모델 경로: ${ONNX_FILE}"
echo ""
echo "  실행 방법:"
echo "  ros2 run robo_claw_vision object_detector_node \\"
echo "    --ros-args -p model_path:=${ONNX_FILE}"
echo ""
echo "  또는 launch:"
echo "  ros2 launch robo_claw_bringup robo_claw.launch.py \\"
echo "    use_vision:=true vision_model_path:=${ONNX_FILE}"
echo "======================================"
