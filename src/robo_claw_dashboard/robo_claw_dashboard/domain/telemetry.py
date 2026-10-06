"""텔레메트리 정규화 순수 로직 — ROS import 금지."""


def normalize_battery_percentage(pct: float | None) -> float | None:
    """BatteryState.percentage 를 0~100% 백분율로 정규화한다.

    0.0<=pct<=1.0 이면 (ROS 기본값 0~1) 100을 곱하고, 이미 백분율이면 그대로 둔다.
    """
    if pct is None:
        return None
    try:
        value = float(pct)
    except (TypeError, ValueError):
        # 오염된/stale 센서 데이터는 None 으로 처리해 안전하게 빠르게 실패시킨다.
        return None
    if 0.0 <= value <= 1.0:
        value *= 100.0
    return round(value, 1)


def build_battery_telemetry(
    percentage: float | None,
    voltage: float | None = None,
    is_charging: bool | None = None,
) -> dict:
    """배터리 원시값을 대시보드 표시용 딕셔너리로 정규화한다."""
    normalized = normalize_battery_percentage(percentage)
    return {
        "percentage": f"{normalized:.1f}%" if normalized is not None else "unknown",
        "voltage": f"{voltage:.2f}V" if voltage is not None else "unknown",
        "is_charging": bool(is_charging) if is_charging is not None else None,
    }
