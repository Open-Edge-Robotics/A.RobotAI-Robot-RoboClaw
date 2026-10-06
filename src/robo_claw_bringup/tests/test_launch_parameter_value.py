"""런치 파일이 문자열 파라미터를 YAML로 재해석하지 않는지 검증한다.

``ros2 launch`` 는 ``LaunchConfiguration`` 을 노드 파라미터 값으로 그대로 넘기면
``launch_ros`` 가 그 값을 yaml.safe_load 로 파싱한다. 토큰/키처럼 임의 문자가
들어오는 문자열 파라미터가 ``*``, ``{``, ``[``, ``%`` 등 YAML 예약 문자로 시작하거나
숫자/불리언처럼 보이면, 파싱 실패로 런치 전체가 죽거나 타입이 바뀐다.
따라서 이런 파라미터는 ``ParameterValue(..., value_type=str)`` 로 감싸야 한다.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest
from launch import LaunchContext  # pyright: ignore[reportMissingImports]
from launch.actions import DeclareLaunchArgument  # pyright: ignore[reportMissingImports]
from launch.utilities import perform_substitutions  # pyright: ignore[reportMissingImports]
from launch_ros.actions import Node  # pyright: ignore[reportMissingImports]
from launch_ros.utilities import evaluate_parameters  # pyright: ignore[reportMissingImports]

pytestmark = pytest.mark.unit

_LAUNCH_FILE = Path(__file__).resolve().parents[1] / "launch" / "robo_claw.launch.py"

# YAML로 다시 파싱되면 깨지는 대표 값들.
# - "*hostile": alias 참조로 파싱 실패(YAMLError)
# - "{open": 흐름 매핑 미완성으로 파싱 실패
# - "1234": int로 강제 변환되어 선언된 string 파라미터와 타입 불일치
_HOSTILE_TOKENS = ["*hostile", "{open", "1234"]


def _load_launch_module():
    spec = importlib.util.spec_from_file_location("probe_robo_claw_launch", _LAUNCH_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _build_context(launch_description, overrides: dict[str, str]) -> LaunchContext:
    context = LaunchContext()
    for entity in launch_description.entities:
        if isinstance(entity, DeclareLaunchArgument):
            default = entity.default_value
            value = perform_substitutions(context, default) if default is not None else ""
            context.launch_configurations[entity.name] = overrides.get(entity.name, value)
    for key, value in overrides.items():
        context.launch_configurations[key] = value
    return context


def _find_node(entities, executable: str) -> Node:
    for entity in entities:
        if isinstance(entity, Node) and entity.node_executable == executable:
            return entity
    raise AssertionError(f"노드를 찾을 수 없습니다: {executable}")


def _evaluated_params(context: LaunchContext, node: Node) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for param in evaluate_parameters(context, node._Node__parameters):  # noqa: SLF001
        if isinstance(param, dict):
            merged.update(param)
    return merged


def _run_launch(overrides: dict[str, str]) -> dict[str, dict[str, Any]]:
    module = _load_launch_module()
    launch_description = module.generate_launch_description()
    context = _build_context(launch_description, overrides)
    entities = module._launch_setup(context)  # noqa: SLF001
    return {
        "channel": _evaluated_params(context, _find_node(entities, "channel_node")),
        "client": _evaluated_params(context, _find_node(entities, "client_node")),
        "agent": _evaluated_params(context, _find_node(entities, "agent_node")),
    }


@pytest.mark.parametrize("token", _HOSTILE_TOKENS)
def test_grpc_peer_token_is_preserved_as_string(token: str) -> None:
    params = _run_launch(
        {
            "use_channel": "true",
            "enable_grpc": "true",
            "robot_config": "former",
            "grpc_peer_token": token,
        }
    )
    value = params["channel"]["grpc_peer_token"]
    assert value == token
    assert isinstance(value, str)
    client_value = params["client"]["grpc_peer_token"]
    assert client_value == token
    assert isinstance(client_value, str)


def test_http_tokens_and_api_keys_survive_yaml_special_chars() -> None:
    hostile = "*hostile"
    params = _run_launch(
        {
            "use_channel": "true",
            "enable_grpc": "true",
            "robot_config": "former",
            "http_readonly_token": hostile,
            "http_control_token": hostile,
            "openai_api_key": hostile,
        }
    )
    assert params["channel"]["http_readonly_token"] == hostile
    assert params["channel"]["http_control_token"] == hostile
    assert params["agent"]["openai_api_key"] == hostile


@pytest.mark.parametrize(
    "payload",
    ['{"Former":"secret456"}', '[{"host":"1.2.3.4","port":50051}]'],
)
def test_json_params_reach_nodes_as_parseable_raw_strings(payload: str) -> None:
    """CLI가 따옴표 없이 넘긴 raw JSON이 노드에서 그대로 json.loads 되어야 한다.

    args.go가 값을 작은따옴표로 감싸면 노드는 ``'{"a":"b"}'`` 를 받아
    json.loads에 실패하고 peer 토큰/MCP 설정을 조용히 버린다.
    """
    params = _run_launch(
        {
            "use_channel": "true",
            "enable_grpc": "true",
            "robot_config": "former",
            "grpc_peer_tokens_json": payload,
            "target_peers_json": payload,
            "mcp_servers_json": payload,
        }
    )
    for node, key in (
        ("channel", "grpc_peer_tokens_json"),
        ("client", "target_peers_json"),
        ("agent", "mcp_servers_json"),
    ):
        value = params[node][key]
        assert isinstance(value, str), (node, key, type(value))
        assert json.loads(value) == json.loads(payload)
