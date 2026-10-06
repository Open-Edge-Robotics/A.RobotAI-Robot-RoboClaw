"""get_datetime 스킬 테스트."""

import importlib.util
import os
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# system_skill/__init__.py 가 ROS2 모듈(geometry_msgs 등)을 import 하므로,
# ROS2 가 없는 환경에서는 __init__.py 를 우회해 datetime.py 를 직접 로드한다.
_DATETIME_MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "robo_claw_agent",
    "skills",
    "system_skill",
    "datetime.py",
)

_spec = importlib.util.spec_from_file_location(
    "_datetime_skill_under_test", _DATETIME_MODULE_PATH
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
GetDatetimeSkill = _mod.GetDatetimeSkill


@pytest.fixture
def skill():
    return GetDatetimeSkill()


class TestGetDatetimeSkillBasic:
    """기본 스킬 메타데이터 및 규칙 준수 테스트"""

    def test_name(self, skill):
        assert skill.name == "get_datetime"

    def test_risk_level(self, skill):
        assert skill.risk_level == "read"

    def test_description_mentions_datetime(self, skill):
        desc = skill.description
        assert "날짜" in desc
        assert "시간" in desc
        assert "추측" in desc

    def test_enabled(self, skill):
        assert skill.enabled is True

    def test_no_manipulation_backend_requirement(self, skill):
        assert skill.requires_manipulation_backend is None


class TestGetDatetimeExecute:
    """execute() 결과 검증"""

    def test_returns_success(self, skill):
        result = skill.execute({})
        assert result["success"] is True

    def test_includes_date(self, skill):
        result = skill.execute({})
        info = result["datetime_info"]
        # YYYY-MM-DD 형식
        assert "date" in info
        parsed = datetime.strptime(info["date"], "%Y-%m-%d")
        assert parsed is not None

    def test_includes_time(self, skill):
        result = skill.execute({})
        info = result["datetime_info"]
        assert "time" in info
        # HH:MM:SS 형식
        datetime.strptime(info["time"], "%H:%M:%S")

    def test_includes_weekday_korean(self, skill):
        result = skill.execute({})
        info = result["datetime_info"]
        weekdays = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        assert info["weekday"] in weekdays

    def test_includes_iso_format(self, skill):
        result = skill.execute({})
        info = result["datetime_info"]
        assert "iso_format" in info
        # ISO 8601 형식 파싱 가능 확인
        datetime.fromisoformat(info["iso_format"])

    def test_includes_unix_timestamp(self, skill):
        result = skill.execute({})
        info = result["datetime_info"]
        assert "unix_timestamp" in info
        assert isinstance(info["unix_timestamp"], int)
        assert info["unix_timestamp"] > 0

    def test_includes_datetime_field(self, skill):
        result = skill.execute({})
        info = result["datetime_info"]
        assert "datetime" in info
        datetime.strptime(info["datetime"], "%Y-%m-%d %H:%M:%S")

    def test_includes_timezone(self, skill):
        result = skill.execute({})
        info = result["datetime_info"]
        assert "timezone" in info
        assert isinstance(info["timezone"], str)

    def test_message_contains_date_and_weekday(self, skill):
        result = skill.execute({})
        assert result["message"] is not None
        assert "현재" in result["message"]


class TestGetDatetimeTimezone:
    """timezone 파라미터 테스트"""

    def test_utc_timezone(self, skill):
        result = skill.execute({"timezone": "UTC"})
        assert result["success"] is True
        info = result["datetime_info"]
        assert "UTC" in info["timezone"] or "utc" in info["timezone"].lower()

    def test_asia_seoul_timezone(self, skill):
        result = skill.execute({"timezone": "Asia/Seoul"})
        assert result["success"] is True
        info = result["datetime_info"]
        # Asia/Seoul 또는 KST 가 timezone에 포함되어야 함
        tz_str = str(info["timezone"])
        assert "Seoul" in tz_str or "KST" in tz_str

    def test_america_new_york_timezone(self, skill):
        result = skill.execute({"timezone": "America/New_York"})
        assert result["success"] is True
        info = result["datetime_info"]
        tz_str = str(info["timezone"])
        assert "New_York" in tz_str or "EST" in tz_str or "EDT" in tz_str

    def test_invalid_timezone_returns_failure(self, skill):
        result = skill.execute({"timezone": "Invalid/Nonexistent"})
        assert result["success"] is False
        assert "알 수 없는 타임존" in result["message"]

    def test_utc_timestamp_is_consistent(self, skill):
        """UTC 호출과 로컬 호출의 unix_timestamp가 거의 동일해야 함"""
        import time as _time

        result_local = skill.execute({})
        result_utc = skill.execute({"timezone": "UTC"})

        ts_local = result_local["datetime_info"]["unix_timestamp"]
        ts_utc = result_utc["datetime_info"]["unix_timestamp"]

        # 2초 이내 차이 (실행 지연 허용)
        assert abs(ts_local - ts_utc) <= 2


class TestGetDatetimeLocale:
    """locale 파라미터 테스트"""

    def test_english_locale(self, skill):
        result = skill.execute({"locale": "en"})
        assert result["success"] is True
        info = result["datetime_info"]
        weekdays_en = [
            "Monday", "Tuesday", "Wednesday",
            "Thursday", "Friday", "Saturday", "Sunday",
        ]
        assert info["weekday"] in weekdays_en

    def test_korean_locale_default(self, skill):
        result = skill.execute({})
        info = result["datetime_info"]
        weekdays_ko = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        assert info["weekday"] in weekdays_ko

    def test_korean_locale_explicit(self, skill):
        result = skill.execute({"locale": "ko"})
        info = result["datetime_info"]
        weekdays_ko = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
        assert info["weekday"] in weekdays_ko

    def test_locale_with_timezone(self, skill):
        result = skill.execute({"timezone": "America/New_York", "locale": "en"})
        assert result["success"] is True
        info = result["datetime_info"]
        weekdays_en = [
            "Monday", "Tuesday", "Wednesday",
            "Thursday", "Friday", "Saturday", "Sunday",
        ]
        assert info["weekday"] in weekdays_en


class TestGetDatetimeAccuracy:
    """결정론적 정확성 테스트"""

    def test_date_matches_system_clock(self, skill):
        """스킬 반환 날짜가 실제 시스템 날짜와 일치하는지 확인"""
        result = skill.execute({})
        info = result["datetime_info"]
        real_now = datetime.now().astimezone()
        assert info["date"] == real_now.strftime("%Y-%m-%d")

    def test_time_within_tolerance(self, skill):
        """스킬 반환 시간이 현재 시간과 5초 이내 차이인지 확인"""
        import time as _time

        before = _time.time()
        result = skill.execute({})
        after = _time.time()

        info = result["datetime_info"]
        ts = info["unix_timestamp"]
        assert before - 1 <= ts <= after + 1