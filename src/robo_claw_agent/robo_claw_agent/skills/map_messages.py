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

    return skill.wait_for_message(
        OccupancyGrid,
        map_topic,
        timeout_sec=timeout_sec,
        use_transient_local=True,
        max_age_sec=max_age_sec,
        fresh_volatile=True,
        fresh_transient_local=not map_is_static(skill.node),
    )


def map_is_static(node: Any) -> bool:
    """Legacy profiles use static maps; dynamic profiles opt into strict freshness."""
    has_parameter = getattr(node, "has_parameter", None)
    if not callable(has_parameter) or not has_parameter("map_is_static"):
        return True
    value = node.get_parameter("map_is_static").get_parameter_value().bool_value
    return value is True


def map_sample_is_fresh(
    node: Any,
    message: Any,
    max_age_sec: float | None,
    received_age_sec: float = 0.0,
) -> bool:
    """Bound cached data by receipt age and stamped data by the ROS clock."""
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
