"""
에이전트 내부 운영 메트릭 추적 유틸리티.
"""

from __future__ import annotations

from threading import Lock
from typing import Any


class OperationTracker:
    """작은 단위 작업의 성공/실패/지연 시간을 스레드 안전하게 기록한다."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._stats: dict[str, dict[str, Any]] = {}

    def record(
        self,
        name: str,
        *,
        success: bool,
        duration_sec: float,
        error: str = "",
    ) -> None:
        with self._lock:
            stat = self._stats.setdefault(
                name,
                {
                    "count": 0,
                    "success": 0,
                    "failure": 0,
                    "total_duration_sec": 0.0,
                    "last_duration_sec": 0.0,
                    "last_error": "",
                },
            )
            stat["count"] += 1
            stat["success"] += 1 if success else 0
            stat["failure"] += 0 if success else 1
            stat["total_duration_sec"] += max(0.0, float(duration_sec))
            stat["last_duration_sec"] = max(0.0, float(duration_sec))
            if error:
                stat["last_error"] = error
            elif success:
                stat["last_error"] = ""

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                name: {
                    **stat,
                    "avg_duration_sec": (
                        stat["total_duration_sec"] / stat["count"]
                        if stat["count"]
                        else 0.0
                    ),
                }
                for name, stat in self._stats.items()
            }
