#!/usr/bin/env bash
# 시나리오를 kinematic/mujoco 백엔드로 각각 실행해 판정·계측을 비교한다.
#
# Phase 1(백엔드 분리) 검증 도구: 같은 시나리오가 두 백엔드에서 같은 결론을 내는지
# 확인한다. mujoco 패키지가 없으면 안내 후 종료한다.
#
# 사용법:
#   ./scripts/compare_sim_backends.sh                       # scenarios/*.yaml 전체
#   ./scripts/compare_sim_backends.sh path/to/scenario.yaml # 지정 시나리오만
#   RC_SIM_RUN_TIMEOUT=240 ./scripts/compare_sim_backends.sh  # 실행당 제한 시간(초)
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
RUN_TIMEOUT_SEC = float(os.environ.get("RC_SIM_RUN_TIMEOUT", "240"))
scenarios = sys.argv[1:]


def run(scenario: str, backend: str, output: str) -> tuple[bool, dict, str]:
    """시나리오 1회 실행. (ok, metrics, error) 반환."""
    try:
        completed = subprocess.run(
            [
                "ros2", "run", "robo_claw_sim", "sim_runner",
                "--backend", backend, "--output", output, scenario,
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=RUN_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        return False, {}, f"{RUN_TIMEOUT_SEC:.0f}s timeout"
    if not os.path.exists(output):
        detail = (completed.stderr or completed.stdout).strip().splitlines()
        return False, {}, detail[-1] if detail else "no result"
    with open(output, encoding="utf-8") as handle:
        payload = json.load(handle)
    return bool(payload.get("passed")), payload.get("metrics", {}), ""


def main() -> int:
    failures = 0
    verdict_mismatch = 0
    deltas: list[float] = []

    print(
        f"{'scenario':<28} {'kinematic':<22} {'mujoco':<22} {'goals':<6} {'Δtravel'}"
    )
    print("-" * 88)

    with tempfile.TemporaryDirectory() as tmp:
        for scenario in scenarios:
            name = os.path.basename(scenario).removesuffix(".yaml")
            results: dict[str, tuple[bool, dict, str]] = {}
            for backend in BACKENDS:
                output = os.path.join(tmp, f"{name}-{backend}.json")
                results[backend] = run(scenario, backend, output)

            kinematic = results["kinematic"]
            mujoco = results["mujoco"]
            if kinematic[2] or mujoco[2]:
                failures += 1
                print(f"{name:<28} ERROR: {kinematic[2] or mujoco[2]}", file=sys.stderr)

            passed_kin, metrics_kin, _ = kinematic
            passed_muj, metrics_muj, _ = mujoco
            if passed_kin != passed_muj:
                verdict_mismatch += 1
                failures += 1

            goals_kin = metrics_kin.get("goals_reached", "-")
            goals_muj = metrics_muj.get("goals_reached", "-")
            travel_kin = float(metrics_kin.get("traveled_m", 0.0) or 0.0)
            travel_muj = float(metrics_muj.get("traveled_m", 0.0) or 0.0)
            delta = 0.0
            if travel_kin > 1e-6:
                delta = abs(travel_muj - travel_kin) / travel_kin * 100.0
            deltas.append(delta)

            print(
                f"{name:<28} "
                f"{('pass' if passed_kin else 'FAIL') + ' ' + str(goals_kin) + 'g ' + f'{travel_kin:.2f}m':<22} "
                f"{('pass' if passed_muj else 'FAIL') + ' ' + str(goals_muj) + 'g ' + f'{travel_muj:.2f}m':<22} "
                f"{'ok' if goals_kin == goals_muj else 'DIFF':<6} {delta:.1f}%",
                flush=True,
            )
            if not passed_kin:
                for reason in metrics_kin.get("reasons", []):
                    print(f"    kinematic: {reason}", flush=True)
            if not passed_muj:
                for reason in metrics_muj.get("reasons", []):
                    print(f"    mujoco: {reason}", flush=True)

    print("-" * 88)
    if deltas:
        print(
            f"요약: 시나리오 {len(scenarios)}개 / 판정 불일치 {verdict_mismatch}건 / "
            f"최대 주행거리 차이 {max(deltas):.1f}% / 평균 {sum(deltas) / len(deltas):.1f}%"
        )
    if failures:
        print(f"[sim-compare] 실패: {failures}건(실행 오류 또는 판정 불일치)", file=sys.stderr)
        return 1
    print("[sim-compare] OK — 모든 시나리오가 두 백엔드에서 같은 판정을 냈습니다.")
    return 0


sys.exit(main())
PY
