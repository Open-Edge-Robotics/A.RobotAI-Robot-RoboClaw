#!/usr/bin/env bash
# 시나리오를 kinematic/mujoco 백엔드로 각각 실행해 판정·계측을 비교한다.
#
# Phase 1(백엔드 분리) 검증 도구: 같은 시나리오가 두 백엔드에서 같은 결론을 내는지
# 확인한다. mujoco 패키지가 없으면 안내 후 종료한다.
#
# 사용법:
#   ./scripts/compare_sim_backends.sh                       # scenarios/*.yaml 전체
#   ./scripts/compare_sim_backends.sh path/to/scenario.yaml # 지정 시나리오만
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${SCRIPT_DIR}"

if [[ $# -gt 0 ]]; then
  SCENARIOS=("$@")
else
  SCENARIOS=(src/robo_claw_sim/scenarios/*.yaml)
fi

if ! ./scripts/run_in_ros_env.sh --ws python3 -c "import mujoco" >/dev/null 2>&1; then
  echo "[sim-compare] mujoco 패키지가 없습니다. 'uv sync --extra sim-mujoco' 후 다시 실행하세요." >&2
  exit 1
fi

./scripts/run_in_ros_env.sh --ws python3 - "${SCENARIOS[@]}" <<'PY'
"""두 백엔드로 각 시나리오를 실행하고 판정/계측을 비교한다."""
import json
import os
import subprocess
import sys
import tempfile

BACKENDS = ("kinematic", "mujoco")
scenarios = sys.argv[1:]

mismatch = False
print(f"{'scenario':<30} {'backend':<9} {'passed':<7} {'goals':<6} {'travel_m':<9} {'sec':<8}")
print("-" * 78)

with tempfile.TemporaryDirectory() as tmp:
    for scenario in scenarios:
        name = os.path.basename(scenario).removesuffix(".yaml")
        for backend in BACKENDS:
            output = os.path.join(tmp, f"{name}-{backend}.json")
            completed = subprocess.run(
                [
                    "ros2", "run", "robo_claw_sim", "sim_runner",
                    "--backend", backend, "--output", output, scenario,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if not os.path.exists(output):
                detail = (completed.stderr or completed.stdout).strip().splitlines()
                print(f"{name:<30} {backend:<9} ERROR   -      -         -")
                if detail:
                    print(f"    {detail[-1]}")
                mismatch = True
                continue

            with open(output, encoding="utf-8") as handle:
                payload = json.load(handle)
            metrics = payload.get("metrics", {})
            passed = bool(payload.get("passed"))
            print(
                f"{name:<30} {backend:<9} {str(passed):<7} "
                f"{metrics.get('goals_reached', '-'):<6} "
                f"{metrics.get('traveled_m', '-'):<9} {metrics.get('elapsed_sec', '-'):<8}"
            )
            if not passed:
                mismatch = True
                for reason in payload.get("reasons", []):
                    print(f"    - {reason}")

print()
if mismatch:
    print("[sim-compare] 실패: 한쪽 백엔드에서 통과하지 못한 시나리오가 있습니다.", file=sys.stderr)
    sys.exit(1)
print("[sim-compare] OK — 모든 시나리오가 두 백엔드에서 통과했습니다.")
PY
