"""트레이싱 백엔드 공통 인터페이스와 환경변수 헬퍼.

`robo_claw_agent.tracing` facade가 여러 백엔드(LangSmith, Langfuse)를 같은 방식으로
호출하기 위한 최소 계약만 정의한다. 각 백엔드는 SDK import를 메서드 안에서 지연
수행해야 하며(선택적 의존성), 실패는 facade가 흡수한다.

ROS 2 의존이 없는 순수 모듈이다(AGENTS.md §3).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Protocol, runtime_checkable

# 환경변수를 "참"으로 해석할 값들
TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

# LangSmith run_type 중 Langfuse generation으로 매핑할 유형
GENERATION_RUN_TYPES = frozenset({"llm", "embedding"})

# run_type을 지정하지 않는 브리지 메서드 이름(끝부분) → generation으로 취급한다.
GENERATION_NAME_SUFFIXES = (
    ".chat",
    ".chat_with_tools",
    ".embed",
    ".embed_query",
    ".analyze_image",
)


def env_truthy(name: str) -> bool:
    """환경변수 값이 참으로 해석되는지 확인한다."""
    value = os.environ.get(name)
    return bool(value) and value.strip().lower() in TRUE_VALUES


def is_generation_span(run_type: str | None, name: str) -> bool:
    """Langfuse observation을 generation으로 만들지 판단한다.

    브리지 메서드는 ``run_type`` 을 생략하는 경우가 있으므로(LangSmith SDK wrapper가
    실제 LLM run을 만들기 때문), 이름 규칙을 함께 사용한다.
    """
    if run_type and run_type in GENERATION_RUN_TYPES:
        return True
    return name.endswith(GENERATION_NAME_SUFFIXES)


@runtime_checkable
class TracingBackend(Protocol):
    """트레이싱 백엔드가 제공해야 하는 최소 인터페이스."""

    name: str

    def is_enabled(self) -> bool:
        """환경변수 게이트가 켜져 있는지."""

    def available(self) -> bool:
        """SDK 패키지를 import할 수 있는지(결과는 캐시 권장)."""

    def ready(self) -> bool:
        """계측을 시작할 최소 조건(키 등)이 갖춰졌는지."""

    def activate(self) -> None:
        """최초 사용 전에 필요한 전역 설정을 수행한다(중복 호출 허용)."""

    def decorate(
        self,
        func: Callable[..., Any],
        *,
        name: str,
        run_type: str | None,
        tags: list[str] | None,
        process_inputs: Callable[..., Any] | None,
        process_outputs: Callable[..., Any] | None,
    ) -> Callable[..., Any]:
        """함수를 이 백엔드의 계측 데코레이터로 감싼다."""

    def wrap_openai_client(self, client: Any) -> Any:
        """OpenAI 호환 클라이언트를 감싼다(미지원 시 원본 반환)."""

    def wrap_anthropic_client(self, client: Any) -> Any:
        """Anthropic 클라이언트를 감싼다(미지원 시 원본 반환)."""

    def update_metadata(self, metadata: dict[str, Any], tags: list[str] | None) -> None:
        """현재 실행(run/trace)에 검색용 metadata와 tag를 추가한다."""

    def trace_id(self) -> str:
        """현재 trace ID를 반환한다(없으면 빈 문자열)."""

    def suppression_context(self) -> AbstractContextManager[Any]:
        """SDK 하위 계측을 끄는 컨텍스트(기본: no-op)."""

    def describe(self) -> str | None:
        """시작 시 로깅할 상태 문자열(없으면 생략)."""

    def flush(self) -> None:
        """버퍼에 남은 이벤트를 전송한다."""

    def record_usage(self, response: Any) -> None:
        """LLM 응답 객체에서 토큰 사용량을 기록한다(미지원 시 no-op)."""


class NoopBackend:
    """공통 기본 구현. 각 백엔드는 필요한 메서드만 재정의한다."""

    name = "noop"

    def is_enabled(self) -> bool:
        return False

    def available(self) -> bool:
        return False

    def ready(self) -> bool:
        return True

    def activate(self) -> None:
        return None

    def wrap_openai_client(self, client: Any) -> Any:
        return client

    def wrap_anthropic_client(self, client: Any) -> Any:
        return client

    def update_metadata(self, metadata: dict[str, Any], tags: list[str] | None) -> None:
        return None

    def trace_id(self) -> str:
        return ""

    def suppression_context(self) -> AbstractContextManager[Any]:
        return nullcontext()

    def describe(self) -> str | None:
        return None

    def flush(self) -> None:
        return None

    def record_usage(self, response: Any) -> None:
        return None

    def reset(self) -> None:
        """캐시된 import/설정 상태를 초기화한다(테스트 및 재구성용)."""
        return None
