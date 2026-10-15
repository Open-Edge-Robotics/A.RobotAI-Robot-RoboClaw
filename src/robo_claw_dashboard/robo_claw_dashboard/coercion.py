"""정수/실수 coercion 공용 헬퍼.

``dashboard_node.py`` 의 ``_as_int``/``_as_float`` 와
``adapters/ros_adapter.py`` 의 ``_coerce_int``/``_coerce_float`` 가 동일 구현을
복제하고 있었다. 노드와 어댑터가 함께 쓰는 리프 모듈로 모은다.
"""

from __future__ import annotations

from typing import Any


def coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
