"""System 1 내장 서버 launch 헬퍼(_system1_launch.py) 테스트."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

pytest.importorskip("launch")

_HELPER = Path(__file__).resolve().parents[1] / "launch" / "_system1_launch.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("probe_system1_launch", _HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helper = _load_helper()


def test_no_action_by_default():
    assert helper.system1_local_server_actions({}) == []
    assert helper.system1_local_server_actions({"SYSTEM1_LOCAL_SERVER": "false"}) == []


def test_local_server_adds_supervisor_process():
    from launch.actions import ExecuteProcess  # pyright: ignore[reportMissingImports]

    actions = helper.system1_local_server_actions({"SYSTEM1_LOCAL_SERVER": "true"})

    assert len(actions) == 1
    assert isinstance(actions[0], ExecuteProcess)
    cmd = " ".join(str(part) for part in actions[0].cmd)
    assert "robo_claw_agent.system1_local_server" in cmd


def test_launch_file_wires_helper():
    launch_file = _HELPER.parent / "robo_claw.launch.py"
    text = launch_file.read_text(encoding="utf-8")
    assert "from _system1_launch import system1_local_server_actions" in text
    assert "launch_nodes.extend(system1_local_server_actions(os.environ))" in text
