"""공유 runtime contract의 생성된 bringup 매핑 회귀 테스트."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_GENERATED_CONFIG = (
    Path(__file__).resolve().parents[1] / "launch" / "_generated_runtime_config.py"
)


def test_langsmith_workspace_id_mapping() -> None:
    spec = importlib.util.spec_from_file_location(
        "generated_runtime_config", _GENERATED_CONFIG
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    field = module.RUNTIME_CONFIG_FIELDS["langsmith.workspace_id"]
    assert field["env"] == "LANGSMITH_WORKSPACE_ID"
    assert field["scope"] == "runtime"
    assert field["secret"] is False
