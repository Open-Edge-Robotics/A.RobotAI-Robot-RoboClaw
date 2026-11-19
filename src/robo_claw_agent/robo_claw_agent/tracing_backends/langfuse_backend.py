"""Langfuse 트레이싱 백엔드.

Langfuse v2 서버(2.x)는 OTEL 수집 엔드포인트가 없으므로 SDK도 2.x 계열
(``langfuse>=2.60,<3``)을 사용한다. v3 이상 서버로 이전할 때는 이 파일만 교체하면
된다(`from langfuse import observe`).

설계 원칙
---------
* **전역 monkeypatch 금지**: ``langfuse.openai`` 는 ``openai`` 모듈을 전역
  monkeypatch 하므로 사용하지 않는다. 브리지 메서드에 이미 적용된 ``traceable``
  계측을 ``observe(as_type="generation")`` 로 매핑하고, 토큰 사용량은 응답 객체에서
  추출해 기록한다.
* **비활성 시 데코레이터 미적용**: langfuse 2.x ``observe`` 는 클라이언트가 비활성
  상태면 호출마다 ERROR 로그를 남긴다. facade가 활성 백엔드만 데코레이션하므로
  이 경로를 타지 않는다.
* **redaction 중앙화**: ``langfuse_context.configure(mask=...)`` 에 facade의
  ``redact_trace_data`` 를 전달해 모든 이벤트 input/output을 전송 직전에 마스킹한다.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
from collections.abc import Callable
from typing import Any

from .base import NoopBackend, env_truthy, is_generation_span

logger = logging.getLogger(__name__)

# Langfuse v2 응답/usage 객체에서 토큰 수를 찾을 때 쓰는 키
_USAGE_INPUT_KEYS = ("prompt_tokens", "input_tokens", "prompt_eval_count")
_USAGE_OUTPUT_KEYS = ("completion_tokens", "output_tokens", "eval_count")

# 브리지 인스턴스가 모델명을 보관하는 속성 이름
_MODEL_ATTRIBUTES = ("model", "_model", "_deployment", "model_name")
_EMBEDDING_MODEL_ATTRIBUTES = ("_embedding_model", "_embedding_deployment")


class LangfuseBackend(NoopBackend):
    """Langfuse SDK(`langfuse` 2.x) 기반 백엔드."""

    name = "langfuse"

    def __init__(self) -> None:
        self._importable = False
        self._configured = False

    def is_enabled(self) -> bool:
        return env_truthy("LANGFUSE_TRACING")

    def available(self) -> bool:
        if self._importable:
            return True
        try:
            import langfuse  # noqa: F401
        except Exception:
            return False
        self._importable = True
        return True

    def ready(self) -> bool:
        """키가 없으면 langfuse 2.x 클라이언트가 비활성 상태로 매 호출 ERROR 로그를 남긴다."""
        return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))

    def activate(self) -> None:
        """마스킹 훅을 등록한다(최초 1회)."""
        if self._configured:
            return
        from langfuse.decorators import langfuse_context

        langfuse_context.configure(mask=_mask_payload)
        self._configured = True

    def decorate(
        self,
        func: Callable[..., Any],
        *,
        name: str,
        run_type: str | None = None,
        tags: list[str] | None = None,
        process_inputs: Callable[..., Any] | None = None,
        process_outputs: Callable[..., Any] | None = None,
    ) -> Callable[..., Any]:
        from langfuse.decorators import observe

        generation = is_generation_span(run_type, name)
        target = _with_generation_details(func, name) if generation else func
        if generation:
            return observe(name=name, as_type="generation")(target)
        return observe(name=name)(target)

    def update_metadata(self, metadata: dict[str, Any], tags: list[str] | None = None) -> None:
        from langfuse.decorators import langfuse_context

        if langfuse_context.get_current_trace_id() is None:
            return
        kwargs: dict[str, Any] = {}
        if metadata:
            kwargs["metadata"] = metadata
        if tags:
            kwargs["tags"] = list(tags)
        if kwargs:
            langfuse_context.update_current_trace(**kwargs)

    def trace_id(self) -> str:
        from langfuse.decorators import langfuse_context

        return str(langfuse_context.get_current_trace_id() or "")

    def record_usage(self, response: Any) -> None:
        usage = _extract_usage(response)
        if not usage:
            return
        from langfuse.decorators import langfuse_context

        if langfuse_context.get_current_observation_id() is None:
            return
        # Langfuse 2.x 서버(2.95)는 배치 ingestion에서 usageDetails를 저장하지 않고
        # 토큰 수를 추정치로 채운다. legacy usage 필드도 함께 보내 실제 값을 보존한다.
        langfuse_context.update_current_observation(
            usage={"unit": "TOKENS", **usage},
            usage_details=usage,
        )

    def describe(self) -> str:
        if not self.is_enabled():
            return "Langfuse tracing disabled (set LANGFUSE_TRACING=true in .env to enable)"
        if not self.available():
            return (
                "LANGFUSE_TRACING is enabled but the 'langfuse' package is not "
                "installed. Run `uv sync` (or `pip install 'langfuse>=2.60,<3'`) to enable tracing."
            )
        public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
        secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
        if not public_key or not secret_key:
            return (
                "LANGFUSE_TRACING is enabled but LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY "
                "are not set; traces will NOT be uploaded."
            )
        host = os.environ.get("LANGFUSE_HOST") or "https://cloud.langfuse.com"
        return f"Langfuse tracing ENABLED (host={host})"

    def flush(self) -> None:
        from langfuse.decorators import langfuse_context

        langfuse_context.flush()

    def reset(self) -> None:
        """캐시된 import/설정 상태를 초기화한다."""
        self._importable = False
        self._configured = False


def _mask_payload(data: Any = None, **_kwargs: Any) -> Any:
    """Langfuse 전송 직전에 credential과 대용량 payload를 제거한다."""
    from ..tracing import redact_trace_data

    return redact_trace_data(data)


def _with_generation_details(func: Callable[..., Any], name: str) -> Callable[..., Any]:
    """generation observation이 열려 있는 동안 모델명과 토큰 사용량을 기록한다."""
    if asyncio.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            result = await func(*args, **kwargs)
            _record_generation_details(args, kwargs, name)
            return result

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        result = func(*args, **kwargs)
        _record_generation_details(args, kwargs, name)
        return result

    return sync_wrapper


def _record_generation_details(args: tuple[Any, ...], kwargs: dict[str, Any], name: str) -> None:
    try:
        from langfuse.decorators import langfuse_context

        if langfuse_context.get_current_observation_id() is None:
            return
        model = _model_name(args, kwargs, name)
        if model:
            langfuse_context.update_current_observation(model=model)
    except Exception as exc:  # 계측 실패가 LLM 호출 결과를 바꾸지 않는다.
        logger.debug("Langfuse generation metadata update failed: %s", exc)


def _model_name(args: tuple[Any, ...], kwargs: dict[str, Any], name: str) -> str:
    explicit = kwargs.get("model")
    if isinstance(explicit, str) and explicit:
        return explicit
    owner = args[0] if args else None
    if owner is None:
        return ""
    attributes = (".embed" in name and _EMBEDDING_MODEL_ATTRIBUTES) or _MODEL_ATTRIBUTES
    for attribute in attributes:
        value = getattr(owner, attribute, None)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_usage(response: Any) -> dict[str, int]:
    """SDK 응답에서 토큰 사용량을 ``usage_details`` 형식으로 변환한다."""
    if response is None:
        return {}
    usage = response if isinstance(response, dict) else getattr(response, "usage", None) or response
    if usage is None:
        return {}
    numbers: dict[str, int] = {}
    for target, keys in (("input", _USAGE_INPUT_KEYS), ("output", _USAGE_OUTPUT_KEYS)):
        for key in keys:
            value = _usage_value(usage, key)
            if value is not None:
                numbers[target] = value
                break
    if not numbers:
        return {}
    total = numbers.get("input", 0) + numbers.get("output", 0)
    if total:
        numbers["total"] = total
    return numbers


def _usage_value(usage: Any, key: str) -> int | None:
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
