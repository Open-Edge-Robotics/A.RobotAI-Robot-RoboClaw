"""트레이싱 백엔드 레지스트리.

facade(`robo_claw_agent.tracing`)는 이 목록을 순서대로 사용한다. 순서가 trace ID
우선순위(로그/연결용)를 결정하므로 LangSmith를 먼저 둔다.
"""

from __future__ import annotations

from .base import TracingBackend
from .langfuse_backend import LangfuseBackend
from .langsmith_backend import LangsmithBackend

__all__ = ["BACKENDS", "LangfuseBackend", "LangsmithBackend", "TracingBackend"]

BACKENDS: tuple[TracingBackend, ...] = (LangsmithBackend(), LangfuseBackend())
