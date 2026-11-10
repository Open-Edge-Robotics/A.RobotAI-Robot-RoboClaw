"""Robot-local map topic declaration and launch wiring contracts."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from test_cloid_robot_config import (
    _build_context,
    _evaluated_params,
    _find_node,
    _load_launch_module,
)

pytestmark = pytest.mark.unit

_SRC = Path(__file__).resolve().parents[2]
_BRINGUP = _SRC / "robo_claw_bringup"


class FakeParameterNode:
    def __init__(self, overrides):
        self.values = {}
        self.overrides = overrides

    def declare_parameter(self, name, default):
        self.values[name] = self.overrides.get(name, default)

    def get_parameter(self, name):
        value = self.values[name]
        fields = SimpleNamespace(
            string_value=value if isinstance(value, str) else "",
            bool_value=value if isinstance(value, bool) else False,
            integer_value=value if isinstance(value, int) else 0,
            double_value=value if isinstance(value, float) else 0.0,
            string_array_value=value if isinstance(value, list) else [],
        )
        return SimpleNamespace(get_parameter_value=lambda: fields)


def _load_agent_params(monkeypatch):
    path = _SRC / "robo_claw_agent/robo_claw_agent/agent_node/params.py"
    spec = importlib.util.spec_from_file_location("probe_agent_map_params", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "overrides, expected", [({}, "/map"), ({"map_topic": "/lio/grid_map"}, "/lio/grid_map")]
)
def test_agent_declares_and_reads_robot_map_topic(monkeypatch, overrides, expected):
    module = _load_agent_params(monkeypatch)
    node = FakeParameterNode(overrides)

    module.declare_agent_parameters(node)

    assert node.values["map_topic"] == expected
    assert module.read_agent_parameters(node).map_topic == expected


@pytest.mark.parametrize(
    "overrides, expected",
    [({}, True), ({"map_is_static": False}, False), ({"map_is_static": True}, True)],
)
def test_agent_declares_and_reads_map_static_policy(monkeypatch, overrides, expected):
    module = _load_agent_params(monkeypatch)
    node = FakeParameterNode(overrides)

    module.declare_agent_parameters(node)

    assert module.AgentParams().map_is_static is True
    assert node.values["map_is_static"] is expected
    assert module.read_agent_parameters(node).map_is_static is expected


def test_cloid_profile_marks_lio_map_dynamic():
    config = yaml.safe_load((_BRINGUP / "config/cloid_config.yaml").read_text())
    assert config["robo_claw_agent_node"]["ros__parameters"]["map_is_static"] is False


def test_cloid_profile_selects_lio_occupancy_grid():
    config = yaml.safe_load((_BRINGUP / "config/cloid_config.yaml").read_text())
    assert config["robo_claw_agent_node"]["ros__parameters"]["map_topic"] == "/lio/grid_map"


@pytest.mark.parametrize(
    "robot_config, expected",
    [("cloid", "/lio/grid_map"), ("sim", "/map"), ("missing-profile", "/map")],
)
def test_launch_passes_robot_local_map_topic_to_grpc(monkeypatch, robot_config, expected):
    module = _load_launch_module()
    monkeypatch.setattr(module, "get_package_share_directory", lambda _name: str(_BRINGUP))
    context = _build_context(module.generate_launch_description(), {"robot_config": robot_config})

    entities = module._launch_setup(context)

    params = _evaluated_params(context, _find_node(entities, "robo_claw_grpc"))
    assert params["map_topic"] == expected


@pytest.mark.parametrize("topic", [None, "", "   ", [], 42])
def test_launch_invalid_robot_map_topic_falls_back_to_map(monkeypatch, topic):
    module = _load_launch_module()
    config = {"robo_claw_agent_node": {"ros__parameters": {"map_topic": topic}}}
    monkeypatch.setattr(module, "_load_robot_config", lambda _robot: config)
    context = _build_context(module.generate_launch_description(), {"robot_config": "sim"})

    entities = module._launch_setup(context)

    params = _evaluated_params(context, _find_node(entities, "robo_claw_grpc"))
    assert params["map_topic"] == "/map"
