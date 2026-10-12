"""공유 runtime contract의 생성된 bringup 매핑 회귀 테스트."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_GENERATED_CONFIG = (
    Path(__file__).resolve().parents[1] / "launch" / "_generated_runtime_config.py"
)


def _load_generated_runtime_config():
    spec = importlib.util.spec_from_file_location(
        "generated_runtime_config", _GENERATED_CONFIG
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_langsmith_workspace_id_mapping() -> None:
    module = _load_generated_runtime_config()
    field = module.RUNTIME_CONFIG_FIELDS["langsmith.workspace_id"]
    assert field["env"] == "LANGSMITH_WORKSPACE_ID"
    assert field["scope"] == "runtime"
    assert field["secret"] is False


def test_cloid_cleanup_demo_settings_mapping() -> None:
    module = _load_generated_runtime_config()
    enabled = module.RUNTIME_CONFIG_FIELDS["agent.cloid_cleanup_demo_enabled"]
    assert enabled["env"] == "RC_CLOID_CLEANUP_DEMO_ENABLED"
    assert enabled["launch_argument"] == "cloid_cleanup_demo_enabled"
    assert enabled["parameter"] == ""
    assert enabled["scope"] == "robot_profile"

    motion_ids = module.RUNTIME_CONFIG_FIELDS[
        "agent.cloid_cleanup_demo_motion_ids_json"
    ]
    assert motion_ids["env"] == "RC_CLOID_CLEANUP_DEMO_MOTION_IDS_JSON"
    assert motion_ids["launch_argument"] == "cloid_cleanup_demo_motion_ids_json"
    assert motion_ids["parameter"] == "cloid_cleanup_demo_motion_ids_json"
    assert motion_ids["scope"] == "robot_profile"
