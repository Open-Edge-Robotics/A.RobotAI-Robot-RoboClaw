"""런치 파라미터 truthiness 헬퍼 ``_optional_overrides`` 계약 테스트.

``robo_claw.launch.py`` 는 노드 기본값을 빈 문자열로 덮어쓰지 않기 위해
"raw 값이 truthy 일 때만 파라미터를 설정"하는 패턴을 40곳에서 반복하고 있었다.
이를 데이터 기반 헬퍼로 통합했으므로, 포함/제외 기준을 여기서 고정한다.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_LAUNCH_FILE = Path(__file__).resolve().parents[1] / "launch" / "robo_claw.launch.py"


def _load_launch_module():
    spec = importlib.util.spec_from_file_location("probe_robo_claw_launch_overrides", _LAUNCH_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_raw_falsy_is_excluded():
    module = _load_launch_module()

    assert module._optional_overrides({"a": ("", "x")}) == {}
    assert module._optional_overrides({"a": (None, "x")}) == {}


def test_raw_truthy_includes_value():
    module = _load_launch_module()

    assert module._optional_overrides({"a": ("v", "v")}) == {"a": "v"}


def test_raw_truthy_keeps_falsy_value():
    """raw 가 truthy 이면 결과값이 False/0 이어도 포함되어 기본값을 명시적으로 끈다."""
    module = _load_launch_module()

    out = module._optional_overrides(
        {
            "enable_rag": ("false", False),
            "rag_top_k": ("0", 0),
        }
    )

    assert out == {"enable_rag": False, "rag_top_k": 0}


def test_key_order_is_preserved():
    module = _load_launch_module()

    out = module._optional_overrides({"a": ("1", 1), "b": ("2", 2), "c": ("", 3)})

    assert list(out) == ["a", "b"]
