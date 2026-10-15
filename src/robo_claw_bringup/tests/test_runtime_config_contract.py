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


def test_cloid_expression_setting_mapping() -> None:
    module = _load_generated_runtime_config()
    field = module.RUNTIME_CONFIG_FIELDS["agent.cloid_expression_enabled"]
    assert field["env"] == "RC_CLOID_EXPRESSION_ENABLED"
    assert field["launch_argument"] == "cloid_expression_enabled"
    assert field["parameter"] == ""
    assert field["scope"] == "robot_profile"
    assert field["secret"] is False


def test_cloid_navigation_pose_setting_mapping() -> None:
    """주행 전 boxing 자세 prelude 스위치는 계약/launch 인자 이름이 일치해야 한다."""
    module = _load_generated_runtime_config()
    field = module.RUNTIME_CONFIG_FIELDS["agent.cloid_navigation_pose_enabled"]
    assert field["env"] == "RC_CLOID_NAVIGATION_POSE_ENABLED"
    assert field["launch_argument"] == "cloid_navigation_pose_enabled"
    assert field["parameter"] == ""
    assert field["scope"] == "robot_profile"
    assert field["secret"] is False
    assert module.ENV_TO_LAUNCH_ARGUMENT["RC_CLOID_NAVIGATION_POSE_ENABLED"] == (
        "cloid_navigation_pose_enabled"
    )
