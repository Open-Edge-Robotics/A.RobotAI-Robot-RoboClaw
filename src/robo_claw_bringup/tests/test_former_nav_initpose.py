"""former nav2 기동 스크립트의 AMCL 초기 위치 선택을 블랙박스로 검증한다.

`former/<robot>/run_former_env.sh nav2` 를 가짜 `docker` 로 실행해, 컨테이너에 마운트될
params 파일의 `amcl.initial_pose` 가 어느 소스(last_pose.yaml / initpose.md / 원본)에서
왔는지 확인한다. 실제 docker·ROS 는 필요 없다.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
ROBOTS = ("former0045", "former0047")
MAP_NAME = "test_map.yaml"

PARAMS = """amcl:
  ros__parameters:
    set_initial_pose: true
    initial_pose:
      x: -0.731
      y: 4.521
      z: 0.0
      yaw: -0.598
"""

INITPOSE_MD = (
    "Position(0.407429, 0.204412, 0), Orientation(0, 0, -0.797877, 0.60282) = Angle: -1.84753\n"
)

# 가짜 docker: `docker run` 인자 중 /ws/params 마운트를 찾아 params 파일을 출력한다
# (비-daemon 모드에서는 스크립트 종료 시 임시 디렉터리가 지워지므로 실행 중에 읽는다).
FAKE_DOCKER = """#!/bin/bash
[ "${1:-}" = "run" ] || exit 0
prev=""
for arg in "$@"; do
    if [ "$prev" = "-v" ]; then
        case "$arg" in
            *:/ws/params:ro) cat "${arg%%:/ws/params:ro}/nav2_params.yaml" ;;
        esac
    fi
    prev="$arg"
done
"""


def _last_pose(map_name: str = MAP_NAME, *, x: str = "1.250", frame: str = "map") -> str:
    return (
        "# RoboClaw last known robot pose\n"
        "version: 1\n"
        f'frame: "{frame}"\n'
        f'map: "{map_name}"\n'
        f"x: {x}\n"
        "y: -2.500\n"
        "yaw: 0.7854\n"
        'near_place: "거실"\n'
        'updated_at: "2026-11-20T10:00:00+09:00"\n'
    )


@pytest.fixture
def nav_env(tmp_path):
    """스크립트를 임시 트리로 복사하고 (실행 함수, 경로들)을 돌려준다."""
    if shutil.which("bash") is None:
        pytest.skip("bash 필요")

    def _make(robot: str):
        root = tmp_path / robot
        robot_dir = root / "former" / robot
        robot_dir.mkdir(parents=True)
        shutil.copy(REPO_ROOT / "former" / robot / "run_former_env.sh", robot_dir)
        (root / "former" / "params").mkdir()
        (root / "former" / "params" / "nav2_params.yaml").write_text(PARAMS, encoding="utf-8")
        home = root / "home"
        (home / "workspace" / "nav2" / "maps").mkdir(parents=True)
        (home / "workspace" / "nav2" / "maps" / MAP_NAME).write_text("image: x.pgm\n")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "docker").write_text(FAKE_DOCKER, encoding="utf-8")
        (bin_dir / "docker").chmod(0o755)
        memory_dir = home / "workspace" / "A.RobotAI-Robot-RoboClaw" / "agent_workspace" / "memory"
        memory_dir.mkdir(parents=True)

        def run(**env_overrides: str) -> subprocess.CompletedProcess[str]:
            env = {
                "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
                "HOME": str(home),
                "TMPDIR": str(root),
            }
            env.update(env_overrides)
            return subprocess.run(
                [
                    "bash",
                    str(robot_dir / "run_former_env.sh"),
                    "nav2",
                    "-m",
                    MAP_NAME,
                    "--home-dir",
                    str(home),
                ],
                env=env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

        return run, {"root": root, "memory": memory_dir}

    return _make


def _initial_pose(stdout: str) -> tuple[str, str, str]:
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "initial_pose:")
    values = {}
    for line in lines[start + 1 : start + 5]:
        key, _, value = line.strip().partition(":")
        values[key] = value.strip()
    return values["x"], values["y"], values["yaw"]


@pytest.mark.parametrize("robot", ROBOTS)
def test_last_pose_from_default_memory_dir_wins(nav_env, robot):
    run, paths = nav_env(robot)
    (paths["memory"] / "last_pose.yaml").write_text(_last_pose(), encoding="utf-8")
    (paths["root"] / "former" / "initpose.md").write_text(INITPOSE_MD, encoding="utf-8")

    result = run()

    assert result.returncode == 0, result.stderr
    assert _initial_pose(result.stdout) == ("1.250", "-2.500", "0.7854")
    assert "last_pose.yaml" in result.stdout


@pytest.mark.parametrize("robot", ROBOTS)
def test_map_mismatch_falls_back_to_initpose(nav_env, robot):
    run, paths = nav_env(robot)
    (paths["memory"] / "last_pose.yaml").write_text(_last_pose("other_map.yaml"), encoding="utf-8")
    (paths["root"] / "former" / "initpose.md").write_text(INITPOSE_MD, encoding="utf-8")

    result = run()

    assert result.returncode == 0, result.stderr
    assert _initial_pose(result.stdout) == ("0.407429", "0.204412", "-1.84753")
    assert "other_map.yaml" in result.stderr


def test_explicit_last_pose_file_and_invalid_values(nav_env, tmp_path):
    run, paths = nav_env("former0045")
    custom = tmp_path / "custom_last_pose.yaml"
    custom.write_text(_last_pose(x="3.000"), encoding="utf-8")

    result = run(RC_LAST_POSE_FILE=str(custom))
    assert result.returncode == 0, result.stderr
    assert _initial_pose(result.stdout)[0] == "3.000"

    # 값이 깨졌거나 frame 이 map 이 아니면 쓰지 않는다(initpose.md 도 없으면 원본 유지).
    custom.write_text(_last_pose(x="nan"), encoding="utf-8")
    result = run(RC_LAST_POSE_FILE=str(custom))
    assert result.returncode == 0, result.stderr
    assert _initial_pose(result.stdout) == ("-0.731", "4.521", "-0.598")

    custom.write_text(_last_pose(frame="odom"), encoding="utf-8")
    result = run(RC_LAST_POSE_FILE=str(custom))
    assert _initial_pose(result.stdout) == ("-0.731", "4.521", "-0.598")


def test_initpose_source_override(nav_env):
    run, paths = nav_env("former0045")
    (paths["memory"] / "last_pose.yaml").write_text(_last_pose(), encoding="utf-8")
    (paths["root"] / "former" / "initpose.md").write_text(INITPOSE_MD, encoding="utf-8")

    forced = run(RC_INITPOSE_SOURCE="initpose")
    assert _initial_pose(forced.stdout)[0] == "0.407429"

    # none 이면 params 파일을 그대로 마운트한다(가짜 docker 는 원본 params 를 출력).
    untouched = run(RC_INITPOSE_SOURCE="none")
    assert untouched.returncode == 0, untouched.stderr
    assert _initial_pose(untouched.stdout) == ("-0.731", "4.521", "-0.598")


def test_max_age_rejects_old_pose(nav_env):
    run, paths = nav_env("former0045")
    (paths["memory"] / "last_pose.yaml").write_text(
        _last_pose().replace("2026-11-20T10:00:00+09:00", "2000-01-01T00:00:00+00:00"),
        encoding="utf-8",
    )

    result = run(RC_LAST_POSE_MAX_AGE_H="24")

    assert result.returncode == 0, result.stderr
    assert _initial_pose(result.stdout) == ("-0.731", "4.521", "-0.598")
    assert "older than 24h" in result.stderr
