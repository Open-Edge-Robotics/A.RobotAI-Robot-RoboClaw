"""주기 스캔 요약 로그의 출력 간격을 판단하는 순수 로직.

ROS2/rclpy에 의존하지 않으므로 단위 테스트에서 그대로 검증한다.
"""

from __future__ import annotations

DEFAULT_LOG_INTERVAL_SEC = 600.0


def should_log_summary(
    last_log_time: float | None,
    now: float,
    interval_sec: float,
    *,
    changed: bool = False,
) -> bool:
    """요약 로그를 지금 남길지 판단한다.

    - ``interval_sec`` 이 0 이하이면 매 스캔마다 남긴다(제한 없음).
    - 아직 한 번도 남기지 않았으면 첫 스캔은 항상 남긴다.
    - 그 외에는 마지막 로그 시각으로부터 ``interval_sec`` 이상 지났을 때만 남긴다.
    """
    if changed or interval_sec <= 0:
        return True
    if last_log_time is None:
        return True
    return (now - last_log_time) >= interval_sec
