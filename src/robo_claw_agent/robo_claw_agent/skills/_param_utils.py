"""스킬 파라미터 coercion 순수 헬퍼.

여러 스킬 모듈(Stretch 모드 전환, CLOiD 모션 스킬 등)이 bool/timeout 변환을 각자
복사해 갖고 있었고, 그 결과 허용 문자열이나 경계 판정이 조금씩 달라졌다. ROS 의존
없이 동작하는 순수 함수만 이 모듈에 모아 단일 구현으로 사용한다.
"""

from __future__ import annotations

import math
from typing import Any


def bool_param(value: Any, default: bool) -> bool:
    """문자열/숫자/bool 표현을 bool 로 관대하게 변환한다."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def bounded_timeout(
    value: Any,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float | None:
    """timeout 값을 [minimum, maximum] 범위로 검증해 반환한다.

    None 이면 ``default``, 범위 밖/비수치/NaN 이면 ``None``(호출부가 거절 처리).
    bool 은 숫자로 오인하지 않는다(``True`` -> 1.0 방지).
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return None
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(timeout) or timeout < minimum or timeout > maximum:
        return None
    return timeout
