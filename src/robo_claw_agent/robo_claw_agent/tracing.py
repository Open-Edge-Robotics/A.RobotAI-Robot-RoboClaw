"""LLM 트레이싱/모니터링 facade (선택적 의존성).

robo_claw 의 LLM 호출을 외부 트레이싱 백엔드로 추적·모니터링하기 위한 얇은 통합
계층이다. 백엔드는 `tracing_backends` 패키지에 격리되어 있고, 이 모듈은 공개 API와
공통 redaction만 담당한다.

지원 백엔드
-----------
* **LangSmith** (``langsmith``): ``LANGSMITH_TRACING=true`` +
  ``LANGSMITH_API_KEY``. 벤더 SDK 클라이언트 래핑으로 토큰 사용량·모델명을 자동
  캡처한다.
* **Langfuse** (``langfuse`` 2.x): ``LANGFUSE_TRACING=true`` +
  ``LANGFUSE_PUBLIC_KEY``/``LANGFUSE_SECRET_KEY``(필수) + ``LANGFUSE_HOST``(선택).
  자체 호스팅 서버를 쓰려면 ``LANGFUSE_HOST`` 를 지정한다.

두 백엔드는 **동시에 켤 수 있다**. 각 백엔드는 독립된 컨텍스트 스택을 사용하므로
한 번의 호출이 양쪽에 각각 run tree/trace 로 기록된다.

설계 원칙
---------
* **완전 선택적**: SDK 패키지가 없거나 환경변수가 꺼져 있으면 모든 함수가 원본
  객체를 그대로 돌려주거나 no-op 으로 동작한다. 기존 실행 경로/성능/의존성에 영향이
  없다.
* **단일 진입점**: 모든 LLM 호출은 ``llm_bridge`` 계층을 통과하므로, 이 계층의
  메서드를 감싸면 토큰 사용량·모델·지연시간이 캡처된다.
* **백엔드 실패 격리**: 계측 실패(import/설정/래핑)는 경고로 남기고 원본 동작을
  유지한다. 트레이싱 문제로 로봇 제어 경로가 중단되지 않는다.
* **redaction 중앙화**: 전송 직전에 ``redact_trace_data`` 로 credential/대용량
  payload를 제거한다(LangSmith process_inputs, Langfuse mask 훅).

환경변수를 읽는 위치는 실행 방식에 따라 다르다(`docs/LLM_TRACING.md` 참고).

* ``./rclaw launch <robot> <env>`` (로컬/``--docker``): AI Config Server 프로필 값이
  캐시 ``.env`` 로 내려와 전달된다.
* ``scripts/run_robo_claw_docker.sh``: 저장소 루트 ``.env`` 가 ``--env-file`` 로 전달된다.
* ``./rclaw run`` / ``./rclaw sim``: 셸에서 ``export`` 해야 한다.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from typing import Any, TypeVar

from .tracing_backends import BACKENDS, TracingBackend

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

_CALL_LOGGING_SUPPRESSED = ContextVar("call_logging_suppressed", default=False)


def call_logging_suppressed() -> bool:
    """현재 실행 문맥의 API 호출 로그 제외 여부를 반환합니다."""
    return _CALL_LOGGING_SUPPRESSED.get()


@contextmanager
def suppress_call_logging():
    """내부 점검 동안 호출 로그와 SDK 하위 트레이싱을 제외합니다."""
    token = _CALL_LOGGING_SUPPRESSED.set(True)
    try:
        with ExitStack() as stack:
            for backend in BACKENDS:
                if not backend.available():
                    continue
                try:
                    stack.enter_context(backend.suppression_context())
                except Exception as exc:  # 억제 실패는 무시하고 계속 진행한다.
                    logger.debug("%s suppression unavailable: %s", backend.name, exc)
            yield
    finally:
        _CALL_LOGGING_SUPPRESSED.reset(token)


def is_tracing_env_enabled() -> bool:
    """백엔드 중 하나라도 트레이싱 환경변수가 켜져 있는지 확인한다.

    신규(``LANGSMITH_TRACING``)와 legacy(``LANGCHAIN_TRACING_V2``) 변수,
    ``LANGFUSE_TRACING`` 을 모두 지원한다.
    """
    return any(backend.is_enabled() for backend in BACKENDS)


def _usable(backend: TracingBackend) -> bool:
    """환경변수 on + SDK import 가능 + 최소 조건(키 등) 충족."""
    return backend.is_enabled() and backend.available() and backend.ready()


def is_active() -> bool:
    """트레이싱이 켜져 있고 SDK도 사용 가능한 백엔드가 하나 이상 있는지 여부."""
    return any(_usable(backend) for backend in BACKENDS)


def _active_backends() -> tuple[TracingBackend, ...]:
    """계측에 사용할 백엔드 목록(환경변수 on + SDK import 가능)."""
    if not is_active():
        return ()
    return tuple(backend for backend in BACKENDS if backend.is_enabled() and backend.available())


def wrap_openai_client(client: Any) -> Any:
    """OpenAI/AzureOpenAI 클라이언트를 활성 백엔드 래퍼로 감싼다.

    트레이싱 비활성/미설치 시 원본 클라이언트를 그대로 반환한다. 감싸진
    클라이언트는 ``chat.completions.create`` / ``embeddings.create`` 호출을
    토큰 사용량과 함께 자동으로 트레이싱한다.
    """
    for backend in _active_backends():
        try:
            client = backend.wrap_openai_client(client)
        except Exception as e:  # 래핑 실패가 LLM 호출을 막아서는 안 된다.
            logger.warning("%s client wrapper failed, using current client: %s", backend.name, e)
    return client


def wrap_anthropic_client(client: Any) -> Any:
    """Anthropic 클라이언트를 활성 백엔드 래퍼로 감싼다 (실패/비활성 시 원본 반환)."""
    for backend in _active_backends():
        try:
            client = backend.wrap_anthropic_client(client)
        except Exception as e:
            logger.warning("%s client wrapper failed, using current client: %s", backend.name, e)
    return client


def _build_traced(
    func: _F, d_args: tuple[Any, ...], d_kwargs: dict[str, Any]
) -> Callable[..., Any]:
    """활성 백엔드로 함수를 감싼다(활성 백엔드가 없으면 원본)."""
    name = (
        d_kwargs.get("name") or (d_args[0] if d_args else "") or getattr(func, "__name__", "trace")
    )
    run_type = d_kwargs.get("run_type")
    tags = d_kwargs.get("tags")
    process_inputs = d_kwargs.get("process_inputs")
    process_outputs = d_kwargs.get("process_outputs")

    wrapped: Callable[..., Any] = func
    for backend in _active_backends():
        try:
            backend.activate()
            wrapped = backend.decorate(
                wrapped,
                name=str(name),
                run_type=run_type,
                tags=tags,
                process_inputs=process_inputs,
                process_outputs=process_outputs,
            )
        except Exception as e:
            logger.warning(
                "%s traceable setup failed, tracing disabled for %s: %s",
                backend.name,
                getattr(func, "__qualname__", func),
                e,
            )
    return wrapped


def traceable(*d_args: Any, **d_kwargs: Any) -> Callable[[_F], _F]:
    """활성 트레이싱 백엔드용 안전한 데코레이터.

    LangSmith ``traceable`` 과 호환되는 키워드(``name``, ``run_type``, ``tags``,
    ``process_inputs``, ``process_outputs``)를 그대로 받는다. 트레이싱 비활성/미설치
    시에는 원본 함수를 그대로 호출하는 no-op 으로 동작한다. 동기/비동기 함수를 모두
    지원한다.
    """

    def decorator(func: _F) -> _F:
        # 실제 트레이싱 함수는 최초 호출 시 한 번만 생성해 캐시한다.
        cache: dict[str, Callable[..., Any]] = {}

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if call_logging_suppressed():
                return func(*args, **kwargs)
            traced = cache.get("fn")
            if traced is None:
                traced = _build_traced(func, d_args, d_kwargs)
                if traced is func:
                    # 활성 백엔드가 없다. 이후 켜질 수 있으므로 캐시하지 않는다.
                    return func(*args, **kwargs)
                cache["fn"] = traced
            return traced(*args, **kwargs)

        wrapper.__roboclaw_traced__ = True
        return wrapper  # type: ignore[return-value]

    return decorator


def record_generation_usage(response: Any) -> None:
    """LLM SDK 응답의 토큰 사용량을 활성 백엔드에 기록한다.

    LangSmith 는 SDK wrapper 로 자동 캡처하므로 no-op 이고, Langfuse 는 현재
    generation observation 에 ``usage_details`` 를 남긴다. 브리지 메서드가 응답
    객체를 반환하지 않는 경우(텍스트만 반환) 이 함수로 직접 전달한다.
    """
    if call_logging_suppressed() or response is None:
        return
    for backend in _active_backends():
        try:
            backend.record_usage(response)
        except Exception as e:  # 계측 실패가 호출 결과를 바꾸지 않는다.
            logger.debug("%s usage recording failed: %s", backend.name, e)


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
    """트레이스 입력/출력에서 credential과 대용량 payload를 제거한다."""
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
    """현재 run/trace에 검색용 metadata/tag를 추가하며 비활성 시 아무 작업도 하지 않는다."""
    if not metadata and not tags:
        return
    safe = redact_trace_data(metadata)
    for backend in _active_backends():
        try:
            backend.update_metadata(safe, tags)
        except Exception as exc:  # trace metadata 실패는 실행에 영향을 주지 않는다.
            logger.debug("%s trace metadata update failed: %s", backend.name, exc)


def current_trace_id() -> str:
    """현재 trace ID를 반환하며 비활성 시 빈 문자열을 반환한다.

    여러 백엔드가 켜져 있으면 우선순위(``BACKENDS`` 순서)상 첫 번째 ID를 반환한다.
    """
    for trace_id in current_trace_ids().values():
        if trace_id:
            return trace_id
    return ""


def current_trace_ids() -> dict[str, str]:
    """활성 백엔드별 현재 trace ID를 반환한다(없으면 빈 문자열)."""
    ids: dict[str, str] = {}
    for backend in _active_backends():
        try:
            ids[backend.name] = backend.trace_id()
        except Exception:
            ids[backend.name] = ""
    return ids


def init_tracing(emit: Callable[[str], None] | None = None) -> None:
    """시작 시 백엔드별 트레이싱 상태를 진단해 로깅한다.

    Args:
        emit: 로그 출력 콜백(예: ``node.get_logger().info``). None 이면 모듈
            로거를 사용한다.
    """

    def _log(msg: str) -> None:
        (emit or logger.info)(msg)

    for backend in BACKENDS:
        try:
            message = backend.describe()
        except Exception as exc:  # 진단 실패는 기동을 막지 않는다.
            _log(f"{backend.name} tracing status unavailable: {exc}")
            continue
        if message:
            _log(message)
        if _usable(backend):
            try:
                backend.activate()
            except Exception as exc:
                _log(f"{backend.name} tracing activation failed: {exc}")


def flush_tracing() -> None:
    """정상 종료 시 버퍼에 남은 trace를 전송한다(실패는 경고만 남긴다).

    긴급 정지 경로에서는 호출하지 않는다. SDK 타임아웃만큼 지연될 수 있다.
    """
    for backend in _active_backends():
        try:
            backend.flush()
        except Exception as exc:
            logger.warning("%s tracing flush failed: %s", backend.name, exc)


def trace_operation(
    op_name: str, func: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """이미 계측된 호출은 재포장하지 않고, 나머지 작업은 실제 이름으로 기록합니다."""
    if getattr(func, "__roboclaw_traced__", False):
        return func(*args, **kwargs)

    @traceable(
        name=op_name,
        run_type="chain",
        process_inputs=trace_process_inputs,
        process_outputs=trace_process_outputs,
    )
    def operation(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        return func(*args, **kwargs)

    return operation(args, kwargs)


def trace_summary_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """상위 orchestration에는 대화 원문 대신 메시지 개수를 기록합니다."""
    result = trace_process_inputs(
        {key: value for key, value in inputs.items() if key != "messages"}
    )
    if "messages" in inputs:
        result["message_count"] = len(inputs["messages"])
    return result


def trace_context_outputs(outputs: Any) -> dict[str, Any] | None:
    """컨텍스트의 대화 본문은 LLM span에만 저장합니다."""
    if outputs is None:
        return None
    messages, robot_summary = outputs
    return {"message_count": len(messages), "robot_summary": redact_trace_data(robot_summary)}


__all__ = [
    "call_logging_suppressed",
    "current_trace_id",
    "current_trace_ids",
    "flush_tracing",
    "init_tracing",
    "is_active",
    "is_tracing_env_enabled",
    "record_generation_usage",
    "redact_trace_data",
    "set_current_trace_metadata",
    "suppress_call_logging",
    "trace_context_outputs",
    "trace_operation",
    "trace_process_inputs",
    "trace_process_outputs",
    "trace_summary_inputs",
    "traceable",
    "wrap_anthropic_client",
    "wrap_openai_client",
]
