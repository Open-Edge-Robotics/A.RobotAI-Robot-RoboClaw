"""LangSmith 트레이싱 백엔드.

`robo_claw_agent.tracing` facade에서 분리한 LangSmith 전용 구현이다. 동작과 로그
문구는 기존 `tracing.py` 구현과 동일하게 유지한다(문서/운영 절차 호환).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from .base import TRUE_VALUES, NoopBackend

logger = logging.getLogger(__name__)


class LangsmithBackend(NoopBackend):
    """LangSmith SDK(`langsmith`) 기반 백엔드."""

    name = "langsmith"

    def __init__(self) -> None:
        self._importable = False

    def is_enabled(self) -> bool:
        value = os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2")
        return bool(value) and value.strip().lower() in TRUE_VALUES

    def available(self) -> bool:
        if self._importable:
            return True
        try:
            import langsmith  # noqa: F401
        except Exception:
            return False
        self._importable = True
        return True

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
        from langsmith import traceable as _traceable

        options: dict[str, Any] = {"name": name}
        if run_type:
            options["run_type"] = run_type
        if tags:
            options["tags"] = tags
        if process_inputs is not None:
            options["process_inputs"] = process_inputs
        if process_outputs is not None:
            options["process_outputs"] = process_outputs
        return _traceable(**options)(func)

    def wrap_openai_client(self, client: Any) -> Any:
        from langsmith.wrappers import wrap_openai

        return wrap_openai(client)

    def wrap_anthropic_client(self, client: Any) -> Any:
        from langsmith.wrappers import wrap_anthropic

        return wrap_anthropic(client)

    def update_metadata(self, metadata: dict[str, Any], tags: list[str] | None = None) -> None:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        if run is None:
            return
        run.extra.setdefault("metadata", {}).update(metadata)
        if tags:
            current = list(getattr(run, "tags", None) or [])
            run.tags = list(dict.fromkeys([*current, *tags]))

    def trace_id(self) -> str:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        if run is None:
            return ""
        return str(getattr(run, "trace_id", "") or getattr(run, "id", "") or "")

    def suppression_context(self) -> AbstractContextManager[Any]:
        """내부 점검 동안 LangSmith SDK 하위 트레이싱을 끈다."""
        try:
            from langsmith import tracing_context
        except ImportError:
            return nullcontext()
        return tracing_context(enabled=False)

    def describe(self) -> str:
        if not self.is_enabled():
            return "LangSmith tracing disabled (set LANGSMITH_TRACING=true in .env to enable)"
        if not self.available():
            return (
                "LANGSMITH_TRACING is enabled but the 'langsmith' package is not "
                "installed. Run `uv sync` (or `pip install langsmith`) to enable tracing."
            )
        project = os.environ.get("LANGSMITH_PROJECT") or os.environ.get(
            "LANGCHAIN_PROJECT", "default"
        )
        endpoint = os.environ.get("LANGSMITH_ENDPOINT") or os.environ.get(
            "LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"
        )
        has_key = bool(os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"))
        if not has_key:
            return (
                "LANGSMITH_TRACING is enabled but LANGSMITH_API_KEY is not set; "
                "traces will NOT be uploaded."
            )
        return f"LangSmith tracing ENABLED (project={project}, endpoint={endpoint})"

    def flush(self) -> None:
        from langsmith import Client

        client = Client()
        try:
            client.flush()
        finally:
            client.close()

    def reset(self) -> None:
        """캐시된 import/설정 상태를 초기화한다."""
        self._importable = False
