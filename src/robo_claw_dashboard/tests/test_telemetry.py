"""텔레메트리 정규화 단위 테스트."""

import pytest
from robo_claw_dashboard.domain.telemetry import (
    build_battery_telemetry,
    normalize_battery_percentage,
)

pytestmark = pytest.mark.unit


class TestNormalizeBatteryPercentage:
    def test_zero_to_one_scaled_to_percent(self):
        assert normalize_battery_percentage(0.87) == 87.0

    def test_more_than_one_kept_as_percent(self):
        assert normalize_battery_percentage(87.0) == 87.0

    def test_none_returns_none(self):
        assert normalize_battery_percentage(None) is None

    def test_rounds_to_one_decimal(self):
        assert normalize_battery_percentage(0.12345) == 12.3

    def test_zero(self):
        assert normalize_battery_percentage(0.0) == 0.0


class TestBuildBatteryTelemetry:
    def test_percent_from_zero_to_one(self):
        t = build_battery_telemetry(0.87, voltage=12.3, is_charging=True)
        assert t["percentage"] == "87.0%"
        assert t["voltage"] == "12.30V"
        assert t["is_charging"] is True

    def test_unknown_when_percentage_missing(self):
        t = build_battery_telemetry(None)
        assert t["percentage"] == "unknown"
        assert t["voltage"] == "unknown"
        assert t["is_charging"] is None
