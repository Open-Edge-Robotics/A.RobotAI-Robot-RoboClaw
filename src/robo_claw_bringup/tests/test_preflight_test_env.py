"""테스트 preflight가 proto 생성물 누락을 모두 감지하는지 검증합니다."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "preflight_test_env.sh"
BASH = shutil.which("bash") or "/bin/bash"
GENERATED_FILES = (
    "src/robo_claw_grpc/robo_claw_grpc/robo_pb2.py",
    "src/robo_claw_grpc/robo_claw_grpc/robo_pb2_grpc.py",
    "src/robo_claw_grpc/robo_claw_grpc/robo_pb2.pyi",
    "src/robo_claw_grpc/robo_claw_grpc/fleet_control_pb2.py",
    "src/robo_claw_grpc/robo_claw_grpc/fleet_control_pb2_grpc.py",
    "src/robo_claw_grpc/robo_claw_grpc/fleet_control_pb2.pyi",
    "src/robo_claw_channel/robo_claw_channel/messenger_pb2.py",
    "src/robo_claw_channel/robo_claw_channel/messenger_pb2_grpc.py",
    "src/robo_claw_channel/robo_claw_channel/messenger_pb2.pyi",
)


@pytest.fixture
def test_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    script = scripts_dir / "preflight_test_env.sh"
    shutil.copy2(SCRIPT_PATH, script)
    (tmp_path / "install").mkdir()
    (tmp_path / "install" / "setup.bash").touch()
    for relative_path in GENERATED_FILES:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    colcon = bin_dir / "colcon"
    colcon.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    colcon.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "ROS_DISTRO": "jazzy"}
    return script, env


def test_preflight_accepts_complete_workspace(test_repo: tuple[Path, dict[str, str]]) -> None:
    script, env = test_repo

    result = subprocess.run([BASH, str(script)], env=env, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "[preflight] 테스트 환경 OK" in result.stdout


@pytest.mark.parametrize("relative_path", GENERATED_FILES)
def test_preflight_rejects_each_missing_proto_artifact(
    test_repo: tuple[Path, dict[str, str]], relative_path: str
) -> None:
    script, env = test_repo
    (script.parent.parent / relative_path).unlink()

    result = subprocess.run([BASH, str(script)], env=env, capture_output=True, text=True)

    assert result.returncode == 1
    assert relative_path in result.stderr
