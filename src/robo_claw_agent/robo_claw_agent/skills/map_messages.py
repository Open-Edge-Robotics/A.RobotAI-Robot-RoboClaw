"""Map reception and freshness checks without changing other sensor policies."""

import math
from typing import Any


def wait_for_map_message(
    skill: Any,
    map_topic: str,
    timeout_sec: float = 5.0,
    max_age_sec: float | None = 10.0,
) -> Any | None:
    from nav_msgs.msg import OccupancyGrid

    # latched(TRANSIENT_LOCAL) 맵은 stamp와 무관하게 "마지막으로 발행된 맵"으로
    # 수용한다. CLOiD /lio/grid_map처럼 1회 발행 뒤 stamp가 갱신되지 않는 맵도
    # stale로 차단하지 않는다. freshness는 VOLATILE(실시간 스트림)에만 적용한다.
    return skill.wait_for_message(
        OccupancyGrid,
        map_topic,
        timeout_sec=timeout_sec,
        use_transient_local=True,
        max_age_sec=max_age_sec,
        fresh_volatile=True,
    )


def map_stamp_age_sec(node: Any, message: Any) -> float | None:
    """Return ``now - header.stamp`` seconds for a map message, or None if unknown.

    latched 맵은 수신을 항상 허용하되, 호출부가 안전 정책을 스스로 세울 수 있도록
    맵 나이를 메타데이터로 노출한다.
    """
    stamp = getattr(getattr(message, "header", None), "stamp", None)
    if stamp is None:
        return None
    try:
        stamp_sec = float(stamp.sec) + float(stamp.nanosec) * 1e-9
        if not math.isfinite(stamp_sec) or stamp_sec <= 0.0:
            return None
        clock = getattr(node, "get_clock", lambda: None)()
        if clock is None:
            return None
        now_sec = float(clock.now().nanoseconds) / 1e9
        if not math.isfinite(now_sec) or now_sec <= 0.0:
            return None
        return now_sec - stamp_sec
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def map_sample_is_fresh(
    node: Any,
    message: Any,
    max_age_sec: float | None,
    received_age_sec: float = 0.0,
) -> bool:
    """Bound cached data by receipt age and stamped data by the ROS clock.

    VOLATILE(실시간) 맵에만 적용한다. latched 맵은 ``wait_for_map_message``에서
    이 함수를 거치지 않고 항상 수용된다.
    """
    if max_age_sec is None:
        return True
    if not math.isfinite(received_age_sec) or not 0.0 <= received_age_sec <= max_age_sec:
        return False
    stamp = getattr(getattr(message, "header", None), "stamp", None)
    if stamp is None:
        return True
    try:
        stamp_sec = float(stamp.sec) + float(stamp.nanosec) * 1e-9
        if not math.isfinite(stamp_sec) or stamp_sec < 0.0:
            return False
        if stamp_sec == 0.0:
            return True
        clock = getattr(node, "get_clock", lambda: None)()
        if clock is None:
            return True
        now_sec = float(clock.now().nanoseconds) / 1e9
        if not math.isfinite(now_sec):
            return False
        if now_sec <= 0.0:
            return True
        return -2.0 <= now_sec - stamp_sec <= max_age_sec
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False
