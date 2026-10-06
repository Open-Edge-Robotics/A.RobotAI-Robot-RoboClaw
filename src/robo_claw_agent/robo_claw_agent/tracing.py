"""LangSmith 트레이싱/모니터링 통합 (선택적 의존성).

robo_claw 의 LLM 호출을 `LangSmith <https://smith.langchain.com>`_ 로
추적·모니터링하기 위한 얇은 통합 계층이다.

설계 원칙
---------
* **완전 선택적**: ``langsmith`` 패키지가 없거나 트레이싱 환경변수가 꺼져 있으면
  이 모듈의 모든 함수는 원본 객체를 그대로 돌려주거나 no-op 으로 동작한다.
  따라서 기존 실행 경로/성능/의존성에 아무 영향이 없다.
* **단일 진입점**: 모든 LLM 호출은 ``llm_bridge`` 계층을 통과하므로, 벤더 SDK
  클라이언트를 여기서 한 번 감싸면(``wrap_openai`` / ``wrap_anthropic``) 토큰
  사용량·모델·지연시간이 자동으로 캡처된다. 상위 메서드는 ``traceable`` 로
  묶어 하나의 실행 트리(run tree)로 그룹핑한다.

활성화 조건 (둘 다 만족해야 함)
-------------------------------
1. ``langsmith`` 패키지 설치
2. 트레이싱 on: ``LANGSMITH_TRACING=true`` (또는 legacy ``LANGCHAIN_TRACING_V2=true``)

관련 환경변수 (LangSmith SDK 표준)
----------------------------------
* ``LANGSMITH_API_KEY``  : LangSmith API 키 (없으면 트레이스 업로드 안 됨)
* ``LANGSMITH_PROJECT``  : 프로젝트 이름 (기본: ``default``)
* ``LANGSMITH_ENDPOINT`` : 커스텀 엔드포인트 (기본: 공식 SaaS)

에이전트 노드 프로세스의 환경변수로만 읽는다(launch 인자/ROS 파라미터 배선 없음).
실행 방식별 전달 경로는 다음과 같다(``docs/LANGSMITH_INTEGRATION.md`` 참고).

* ``./rclaw launch <robot> <env>`` (로컬/``--docker``): AI Config Server 프로필의
  LangSmith 설정이 캐시 ``.env`` 로 내려와 전달된다.
* ``scripts/run_robo_claw_docker.sh``: 저장소 루트 ``.env`` 가 ``--env-file`` 로 전달된다.
* ``./rclaw run`` / ``./rclaw sim``: 저장소 ``.env`` 는 launch 인자 구성에만 쓰이고
  환경변수로 전달되지 않는다. 실행 전에 셸에서 ``export`` 해야 한다.
"""

from __future__ import annotations

import functools
import logging
import os
from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

# 환경변수를 "참"으로 해석할 값들
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def is_tracing_env_enabled() -> bool:
    """트레이싱 활성화 환경변수가 켜져 있는지 확인한다.

    신규(``LANGSMITH_TRACING``)와 legacy(``LANGCHAIN_TRACING_V2``) 변수를 모두
    지원한다.
    """
    value = os.environ.get("LANGSMITH_TRACING") or os.environ.get("LANGCHAIN_TRACING_V2")
    return bool(value) and value.strip().lower() in _TRUE_VALUES


def _langsmith_available() -> bool:
    """``langsmith`` 패키지 import 가능 여부."""
    try:
        import langsmith  # noqa: F401

        return True
    except Exception:
        return False


def is_active() -> bool:
    """트레이싱이 켜져 있고 langsmith 패키지도 사용 가능한지 여부."""
    return is_tracing_env_enabled() and _langsmith_available()


def wrap_openai_client(client: Any) -> Any:
    """OpenAI/AzureOpenAI 클라이언트를 LangSmith 로 감싼다.

    트레이싱 비활성/미설치 시 원본 클라이언트를 그대로 반환한다. 감싸진
    클라이언트는 ``chat.completions.create`` / ``embeddings.create`` 호출을
    토큰 사용량과 함께 자동으로 트레이싱한다.
    """
    if not is_active():
        return client
    try:
        from langsmith.wrappers import wrap_openai

        return wrap_openai(client)
    except Exception as e:  # 래핑 실패가 LLM 호출을 막아서는 안 된다.
        logger.warning("LangSmith wrap_openai failed, using raw client: %s", e)
        return client


def wrap_anthropic_client(client: Any) -> Any:
    """Anthropic 클라이언트를 LangSmith 로 감싼다 (실패/비활성 시 원본 반환)."""
    if not is_active():
        return client
    try:
        from langsmith.wrappers import wrap_anthropic

        return wrap_anthropic(client)
    except Exception as e:
        logger.warning("LangSmith wrap_anthropic failed, using raw client: %s", e)
        return client


def traceable(*d_args: Any, **d_kwargs: Any) -> Callable[[_F], _F]:
    """``langsmith.traceable`` 의 안전한 래퍼 데코레이터.

    트레이싱 비활성/미설치 시에는 원본 함수를 그대로 호출하는 no-op 으로
    동작한다. 동기/비동기 함수 모두를 지원한다(내부적으로 langsmith 가
    함수 종류를 감지). 항상 인자와 함께 호출한다: ``@traceable(name=...)``.
    """

    def decorator(func: _F) -> _F:
        # 실제 트레이싱 함수는 최초 호출 시 한 번만 생성해 캐시한다.
        cache: dict[str, Callable[..., Any]] = {}

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if not is_active():
                return func(*args, **kwargs)
            traced = cache.get("fn")
            if traced is None:
                try:
                    from langsmith import traceable as _traceable

                    traced = _traceable(*d_args, **d_kwargs)(func)
                except Exception as e:
                    logger.warning(
                        "LangSmith traceable setup failed, tracing disabled for %s: %s",
                        getattr(func, "__qualname__", func),
                        e,
                    )
                    traced = func
                cache["fn"] = traced
            return traced(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


_SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "base64",
    "credential",
    "file_content",
    "image",
    "password",
    "secret",
    "token",
)
_MAX_TRACE_STRING = 8_000
_MAX_TRACE_ITEMS = 100


def redact_trace_data(value: Any, *, depth: int = 0) -> Any:
    """LangSmith 입력/출력에서 credential과 대용량 payload를 제거한다."""
    if depth > 8:
        return "<MAX_DEPTH>"
    if isinstance(value, dict):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_TRACE_ITEMS:
                result["<truncated>"] = f"{len(value) - _MAX_TRACE_ITEMS} more items"
                break
            name = str(key)
            lowered = name.lower()
            result[name] = (
                "<REDACTED>"
                if any(part in lowered for part in _SENSITIVE_KEY_PARTS)
                else redact_trace_data(item, depth=depth + 1)
            )
        return result
    if isinstance(value, (list, tuple)):
        items = list(value)
        result = [redact_trace_data(item, depth=depth + 1) for item in items[:_MAX_TRACE_ITEMS]]
        if len(items) > _MAX_TRACE_ITEMS:
            result.append(f"<{len(items) - _MAX_TRACE_ITEMS} more items>")
        return result
    if isinstance(value, bytes):
        return f"<BYTES:{len(value)}>"
    if is_dataclass(value) and not isinstance(value, type):
        return redact_trace_data(asdict(value), depth=depth + 1)
    if all(hasattr(value, name) for name in ("skill_name", "success", "message")):
        return redact_trace_data(
            {
                "skill_name": getattr(value, "skill_name", ""),
                "success": getattr(value, "success", False),
                "status": getattr(value, "status", ""),
                "message": getattr(value, "message", ""),
                "duration_sec": getattr(value, "duration_sec", 0.0),
                "result_data": getattr(value, "result_data", {}),
            },
            depth=depth + 1,
        )
    if isinstance(value, str) and len(value) > _MAX_TRACE_STRING:
        return value[:_MAX_TRACE_STRING] + f"<TRUNCATED:{len(value)}>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return f"<{type(value).__name__}>"


def trace_process_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """메서드 self와 callback을 제외한 안전한 trace 입력을 반환한다."""
    safe = {
        key: value
        for key, value in inputs.items()
        if key not in {"self", "goal_handle", "send_fb", "func", "skill"}
    }
    skill = inputs.get("skill")
    if skill is not None:
        safe["skill_name"] = getattr(skill, "name", type(skill).__name__)
    return redact_trace_data(safe)


def trace_process_outputs(outputs: Any) -> Any:
    """Skill/Task 결과의 민감하거나 큰 값을 제거한다."""
    return redact_trace_data(outputs)


def set_current_trace_metadata(metadata: dict[str, Any], tags: list[str] | None = None) -> None:
    """현재 run에 검색용 metadata/tag를 추가하며 비활성 시 아무 작업도 하지 않는다."""
    if not is_active():
        return
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        if run is None:
            return
        run.extra.setdefault("metadata", {}).update(redact_trace_data(metadata))
        if tags:
            current = list(getattr(run, "tags", None) or [])
            run.tags = list(dict.fromkeys([*current, *tags]))
    except Exception:
        return


def current_trace_id() -> str:
    """현재 LangSmith trace ID를 반환하며 비활성 시 빈 문자열을 반환한다."""
    if not is_active():
        return ""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        return str(getattr(run, "trace_id", "") or getattr(run, "id", "")) if run else ""
    except Exception:
        return ""


def init_tracing(emit: Callable[[str], None] | None = None) -> None:
    """시작 시 LangSmith 트레이싱 상태를 진단해 로깅한다.

    Args:
        emit: 로그 출력 콜백(예: ``node.get_logger().info``). None 이면 모듈
            로거를 사용한다.
    """

    def _log(msg: str) -> None:
        (emit or logger.info)(msg)

    if not is_tracing_env_enabled():
        _log("LangSmith tracing disabled (set LANGSMITH_TRACING=true in .env to enable)")
        return

    if not _langsmith_available():
        _log(
            "LANGSMITH_TRACING is enabled but the 'langsmith' package is not "
            "installed. Run `uv sync` (or `pip install langsmith`) to enable tracing."
        )
        return

    project = os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT", "default")
    endpoint = os.environ.get("LANGSMITH_ENDPOINT") or os.environ.get(
        "LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com"
    )
    has_key = bool(os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY"))
    if not has_key:
        _log(
            "LANGSMITH_TRACING is enabled but LANGSMITH_API_KEY is not set; "
            "traces will NOT be uploaded."
        )
    _log(f"LangSmith tracing ENABLED (project={project}, endpoint={endpoint})")
