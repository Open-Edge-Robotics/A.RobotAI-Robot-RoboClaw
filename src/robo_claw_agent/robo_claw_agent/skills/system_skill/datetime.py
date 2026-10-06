"""현재 날짜/시간 조회 스킬.

로컬 SLM 모델이 날짜/시간을 혼동(hallucination)하는 문제를 보완하기 위해
시스템 클록에서 결정론적으로 정확한 값을 반환한다.
"""

import logging
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)

# ZoneInfo는 키를 찾지 못하면 ZoneInfoNotFoundError를, 절대 경로나 '..'가 섞인
# 잘못된 키에는 ValueError를 던진다. timezone은 LLM이 생성하는 파라미터이므로
# 둘 다 잡아야 스킬이 예외로 죽지 않는다.
_TZ_LOOKUP_ERRORS = (ZoneInfoNotFoundError, ValueError)

_KOREAN_WEEKDAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
_ENGLISH_WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


class GetDatetimeSkill(BaseSkill):
    """현재 정확한 날짜, 시간, 요일, 타임존을 반환하는 스킬"""

    name = "get_datetime"
    answer_mode = "informational"
    input_schema = {
        "type": "object",
        "properties": {
            "timezone": {"type": "string", "description": "IANA timezone, e.g. Asia/Seoul"},
            "locale": {"type": "string", "enum": ["ko", "en"], "default": "ko"},
        },
        "additionalProperties": False,
    }
    risk_level = "read"
    description = (
        "현재 정확한 날짜, 시간, 요일, 타임존을 시스템 클록에서 조회하여 반환합니다. "
        "사용자가 '오늘', '지금', '몇 시', '몇 월', '무슨 요일' 등 날짜/시간을 물을 때, "
        "또는 스킬 실행 결과에 현재 날짜/시간 타임스탬프가 필요할 때 반드시 이 스킬을 사용하세요. "
        "절대 날짜나 시간을 추측하지 마세요. "
        "선택 파라미터: timezone(예: 'Asia/Seoul', 'UTC', 'America/New_York', 기본=시스템 로컬), "
        "locale(요일 표시 언어, 'ko' 또는 'en', 기본='ko')."
    )

    def _resolve_local_tz(self) -> tuple[ZoneInfo | None, str]:
        """시스템 타임존을 결정한다.

        우선순위: TZ 환경변수 → /etc/timezone 파일 → None(naive UTC).
        Docker 컨테이너에서 /etc/localtime 마운트가 없거나 tzdata가 없는
        환경에서도 최소한 TZ 환경변수로 정확한 시간을 반환하기 위함.
        """
        # 1. TZ 환경변수 (Docker -e TZ=... 에서 주입)
        env_tz = os.environ.get("TZ", "").strip()
        if env_tz:
            try:
                return ZoneInfo(env_tz), env_tz
            except _TZ_LOOKUP_ERRORS:
                logger.debug("TZ env var '%s' is not a valid zoneinfo key", env_tz)

        # 2. /etc/timezone 파일 (Debian/Ubuntu 계열)
        try:
            with open("/etc/timezone", encoding="utf-8") as f:
                file_tz = f.read().strip()
            if file_tz:
                try:
                    return ZoneInfo(file_tz), file_tz
                except _TZ_LOOKUP_ERRORS:
                    pass
        except OSError:
            pass

        # 3. astimezone() 에 맡김 (시스템 /etc/localtime 기반)
        return None, "system_local"

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        tz_name = str(params.get("timezone", "")).strip()
        locale = str(params.get("locale", "ko")).strip().lower()

        # 타임존 결정
        tz: ZoneInfo | None = None
        tz_display = "system_local"
        if tz_name:
            try:
                tz = ZoneInfo(tz_name)
                tz_display = tz_name
            except _TZ_LOOKUP_ERRORS as e:
                logger.warning("Rejecting unknown timezone '%s': %s", tz_name, e)
                return self.fail_result(
                    f"알 수 없는 타임존입니다: {tz_name}. "
                    "예: 'Asia/Seoul', 'UTC', 'America/New_York', 'Europe/London'"
                )
        else:
            # 파라미터로 타임존이 지정되지 않은 경우 시스템 타임존 자동 감지
            tz, tz_display = self._resolve_local_tz()

        # 현재 시간
        if tz is not None:
            now = datetime.now(tz)
        else:
            now = datetime.now().astimezone()

        # 요일
        if locale == "en":
            weekday = _ENGLISH_WEEKDAYS[now.weekday()]
        else:
            weekday = _KOREAN_WEEKDAYS[now.weekday()]

        # 타임존 표시명
        tzinfo = now.tzinfo
        tz_name_resolved = str(tzinfo) if tzinfo else tz_display

        result = {
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S"),
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
            "weekday": weekday,
            "timezone": tz_name_resolved,
            "iso_format": now.isoformat(),
            "unix_timestamp": int(now.timestamp()),
        }

        display = f"현재 {tz_display} 기준 {result['date']} {weekday} {result['time']}"

        return self.success_result(display, datetime_info=result)
