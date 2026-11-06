"""ROS 그래프 질의를 안전하게 수행하는 순수 로직.

rmw 계층은 도메인에 섞여 있는 다른 RMW 구현(Fast DDS 등)의 participant를
발견하면 빈 노드 이름을 돌려주고, rcl 이 이를 ``RCLError`` 로 승격한다
(``Failed to get node names: empty node name returned by the RMW layer``).
이 오류는 일시적일 수도 영구적일 수도 있으므로, 호출부가 한 번의 실패로
스캔 전체를 버리지 않도록 재시도 결과를 명시적으로 돌려준다.

ROS2/rclpy 에 의존하지 않으므로 단위 테스트에서 그대로 검증한다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

#: 기본 시도 횟수(최초 호출 + 재시도 1회)
DEFAULT_QUERY_ATTEMPTS = 2
#: 재시도 사이 대기 시간. 그래프 캐시가 갱신될 여유만 준다.
DEFAULT_QUERY_RETRY_DELAY_SEC = 0.2


def call_with_retry(
    func: Callable[[], T],
    *,
    attempts: int = DEFAULT_QUERY_ATTEMPTS,
    retry_delay_sec: float = DEFAULT_QUERY_RETRY_DELAY_SEC,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T | None, Exception | None]:
    """``func`` 를 최대 ``attempts`` 회 호출하고 ``(결과, 마지막 예외)`` 를 돌려준다.

    - 성공하면 ``(결과, None)`` 을 돌려준다.
    - 모든 시도가 실패하면 ``(None, 마지막 예외)`` 를 돌려준다.
    - 빈 결과(``[]``)는 정상 성공으로 취급한다.
    """
    last_error: Exception | None = None
    total = max(1, attempts)
    for attempt in range(total):
        try:
            return func(), None
        except Exception as exc:  # noqa: BLE001 - rmw 계층 오류를 호출부에서 처리한다
            last_error = exc
            if attempt + 1 < total and retry_delay_sec > 0:
                sleep(retry_delay_sec)
    return None, last_error
