import ast
import json
import logging
import os
import re
import sys
from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from ..tracing import traceable
from .base import BaseLLMBridge, ToolCall, ToolTurn, parse_tool_arguments

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 디버깅용 호출 dump (정식 기능 아님)
#
# API 로 실제로 전송된 payload 와 받은 응답을 JSON 파일로 남겨서 "무엇이
# 나갔고 무엇이 왔는지" 확인할 수 있게 한다. 저장 위치는 하드코딩이며 별도
# 설정/옵션으로 끄지 않는다 — 필요 없으면 아래 두 상수만 수정하면 된다.
# ---------------------------------------------------------------------------
# 저장 위치: 저장소 루트의 chat_history/ 디렉터리
#   <repo>/src/robo_claw_agent/robo_claw_agent/llm_bridge/ollama.py
#    └ parents[4] == 저장소 루트
_DEBUG_DUMP_DIR = Path(__file__).resolve().parents[4] / "chat_history"
# False 로 두면 아무것도 기록하지 않는다 (debug 용이라 기본은 True)
_DEBUG_DUMP_ENABLED = True


def _jsonable(value: Any) -> Any:
    """응답 객체를 JSON 으로 직렬화 가능한 형태로 바꾼다.

    ollama 클라이언트는 버전에 따라 dict 를 돌려주거나 pydantic 모델을
    돌려준다. 둘 다 처리하고, 그 외에는 str 로 강제 변환한다.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _jsonable(model_dump())
        except Exception:  # noqa: BLE001 — 직렬화 실패 시 str 로 강제 변환
            pass
    return str(value)


# think=true 시 일부 모델이 content에 <think>...</think>를 섞어 반환한다.
# 닫는 태그가 없는 경우(토큰 한도로 잘림)도 있으므로 두 패턴을 모두 처리한다.
_THINK_BLOCK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)
_UNCLOSED_THINK_PATTERN = re.compile(r"<think>.*", re.DOTALL)


def _strip_think(content: str) -> str:
    """응답 텍스트에서 think 블록을 제거한다.

    닫는 태그가 없는 잘린 think 블록은 이후 내용 전체가 사고 과정이므로
    함께 제거한다.
    """
    cleaned = _THINK_BLOCK_PATTERN.sub("", content)
    return _UNCLOSED_THINK_PATTERN.sub("", cleaned).strip()


class OllamaBridge(BaseLLMBridge):
    """Ollama 로컬 LLM 브릿지"""

    def __init__(
        self,
        model: str = "llama3.2",
        embedding_model: str = "nomic-embed-text",
        base_url: str = "http://localhost:11434",
        options_json: str = "{}",
        timeout_sec: float = 120.0,
        **kwargs: Any,
    ) -> None:
        self._model = model
        self._embedding_model = embedding_model
        self._base_url = base_url
        # ollama.Client(httpx 기반)의 HTTP 타임아웃. 원격 서버가 응답을 늦추거나
        # 멈춰도 무한 대기하지 않도록 상한을 둔다. (기본 ollama 클라이언트는
        # timeout=None 이라 블로킹 호출이 영원히 대기할 수 있음)
        self._timeout_sec = float(timeout_sec)

        try:
            cleaned_options = options_json.strip() if options_json else ""
            if cleaned_options in ('""', "''") or not cleaned_options:
                self._options = {}
            else:
                if (
                    len(cleaned_options) >= 2
                    and cleaned_options[0] == cleaned_options[-1]
                    and cleaned_options[0] in ('"', "'")
                ):
                    inner = cleaned_options[1:-1].strip()
                    if inner and inner[0] in "{[":
                        cleaned_options = inner
                try:
                    self._options = json.loads(cleaned_options)
                except json.JSONDecodeError:
                    self._options = ast.literal_eval(cleaned_options)
        except Exception as e:
            logger.error("OllamaBridge: failed to parse JSON options: %s", e)
            self._options = {}

        self._think = self._options.pop("think", None)
        # 비대칭 임베딩용 쿼리 지시문(선택). options_json 에 "embed_query_instruction"
        # 을 넣어 도메인에 맞게 덮어쓸 수 있다. None 이면 기본 지시문 사용.
        self._embed_query_instruction = self._options.pop("embed_query_instruction", None)
        self._client = None
        self._think_supported: bool | None = None
        logger.info(
            "OllamaBridge: model=%s, embed=%s, base_url=%s, options=%s, think=%s",
            model,
            embedding_model,
            base_url,
            self._options,
            self._think,
        )

    def validate_config(self) -> str | None:
        if not self._model:
            return "Ollama: model이 설정되지 않았습니다."
        if not self._base_url:
            return "Ollama: base_url이 설정되지 않았습니다."
        return None

    def healthcheck(self, timeout_sec: float = 10.0) -> str | None:
        try:
            import httpx

            resp = httpx.get(f"{self._base_url}/api/tags", timeout=timeout_sec)
            if resp.status_code != 200:
                return f"Ollama 헬스체크 실패 (HTTP {resp.status_code})"
            return None
        except Exception as e:
            return f"Ollama 서버 연결 실패 ({self._base_url}): {e}"

    def _get_client(self):
        if self._client is None:
            import ollama

            self._client = ollama.Client(host=self._base_url, timeout=self._timeout_sec)
        return self._client

    def _dump_call(
        self,
        kind: str,
        request: dict[str, Any],
        response: Any = None,
        error: str | None = None,
    ) -> None:
        """디버깅용: API 호출의 request/response 를 chat_history/ 아래 JSON 으로 남긴다.

        정식 기능이 아니라 확인용이므로 실패해도 본체 호출에는 영향이 없어야
        한다 (예외는 삼킨다).
        """
        if not _DEBUG_DUMP_ENABLED:
            return
        # 단위 테스트에서는 가짜 클라이언트(예: test_llm_bridge.py 의 FakeClient)
        # 로 호출하므로 실제 사용 데이터가 아니다. 디버깅 디렉터리를 오염시키지
        # 않도록 pytest 환경에서는 기록하지 않는다. (디버깅용이라 pytest 실행
        # 중에 payload 를 보고 싶으면 아래 조건을 제거하면 된다.)
        if "pytest" in sys.modules:
            return
        try:
            _DEBUG_DUMP_DIR.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            model = str(request.get("model", "unknown")).replace(":", "-").replace("/", "-")
            filename = (
                f"ollama_bridge_call_{kind}_{model}"
                f"_{now.strftime('%Y%m%d-%H%M%S')}-{now.microsecond:06d}"
                f"_{os.getpid()}.json"
            )
            record = {
                "kind": kind,
                "timestamp": now.isoformat(),
                "pid": os.getpid(),
                "base_url": self._base_url,
                "request": _jsonable(request),
            }
            if error is not None:
                record["error"] = error
            else:
                record["response"] = _jsonable(response)
            with (_DEBUG_DUMP_DIR / filename).open("w", encoding="utf-8") as fp:
                json.dump(record, fp, ensure_ascii=False, indent=2, default=str)
        except Exception as e:  # noqa: BLE001 — 디버깅 기능이 본체를 막지 않도록
            logger.warning("OllamaBridge: failed to dump call payload (%s)", e)

    def _invoke(self, kind: str, request: dict[str, Any], call):
        """API 호출 후 보낸 request 와 받은 response 를 함께 기록한다.

        호출이 예외로 끝나면 request 와 에러만 기록하고 예외는 그대로
        전파한다 (기존 동작 유지).
        """
        try:
            response = call()
        except Exception as e:
            self._dump_call(kind, request, error=f"{type(e).__name__}: {e}")
            raise
        self._dump_call(kind, request, response=response)
        return response

    def _supports_think(self) -> bool:
        if self._think_supported is None:
            try:
                import inspect

                import ollama

                self._think_supported = "think" in inspect.signature(ollama.Client.chat).parameters
            except Exception as e:
                logger.debug(
                    "OllamaBridge: failed to detect think support (%s) → treating as unsupported", e
                )
                self._think_supported = False
        return self._think_supported

    def _apply_think(self, call_kwargs: dict) -> None:
        if self._think is None:
            return
        if self._supports_think():
            call_kwargs["think"] = self._think
        else:
            logger.warning(
                "OllamaBridge: think=%s was requested but the ollama client does not "
                "support think. Run `pip install -U ollama` and try again.",
                self._think,
            )

    @traceable(run_type="llm", name="OllamaBridge.chat")
    def chat(self, messages, system_prompt=None, **kwargs):
        client = self._get_client()
        payload = [{"role": "system", "content": system_prompt}] if system_prompt else []
        payload.extend(messages)
        logger.debug("Ollama call: messages=%s", payload)

        chat_kwargs = {"model": self._model, "messages": payload}
        if self._options:
            chat_kwargs["options"] = self._options
        self._apply_think(chat_kwargs)

        response_format = kwargs.pop("response_format", None)
        if response_format:
            if response_format == "json" or (
                isinstance(response_format, dict)
                and response_format.get("type") in ["json", "json_object"]
            ):
                # think=true 시 format="json" 을 강제하면 모델이 thinking 후
                # 자연어로 응답하려는 것을 JSON 으로 억눌러 {"error": ...} 같은
                # 포기 응답을 유발할 수 있다. think 모드에서는 format 제약을
                # 생략하고 파서가 응답에서 JSON 을 추출하도록 한다.
                if self._think is True and self._supports_think():
                    logger.info(
                        "OllamaBridge: skipping format=json because think=%s "
                        "(letting model produce natural response with embedded JSON)",
                        self._think,
                    )
                else:
                    chat_kwargs["format"] = "json"

        resp = self._invoke("chat", chat_kwargs, lambda: client.chat(**chat_kwargs))
        msg = resp.get("message", {})
        content = msg.get("content", "")

        # think=true 시 Ollama가 thinking 필드를 별도로 반환하는 경우 로깅.
        thinking = msg.get("thinking")
        if thinking:
            logger.info("Ollama thinking (first 300 chars): %s", str(thinking)[:300])

        # think=true 시 일부 모델/버전에서 content 안에 <think>...</think> 태그가
        # 포함될 수 있다. 태그 안의 텍스트에 JSON 조각이 있으면 파서가 잘못
        # 추출하므로 think 블록을 제거한 후 반환한다.
        cleaned = _strip_think(content)
        if cleaned != content:
            logger.debug(
                "Stripped <think> block from Ollama response (%d → %d chars)",
                len(content),
                len(cleaned),
            )
            content = cleaned

        logger.debug("Ollama response: %s", content)
        return content

    def supports_tools(self) -> bool:
        """Ollama 는 네이티브 tool calling 을 지원한다."""
        return True

    @traceable(run_type="llm", name="OllamaBridge.chat_with_tools")
    def chat_with_tools(
        self,
        messages,
        system_prompt=None,
        tools=None,
        **kwargs,
    ) -> ToolTurn:
        """도구 목록을 전달해 호출 요청을 받고, 호출 목록을 구조화해 반환한다.

        스키마 제약(``default``/``minimum``/``additionalProperties`` 등)은
        ollama 파이썬 클라이언트의 ``Tool`` 모델을 거치면서 일부가 제거되지만,
        서버가 렌더링하는 툴 프롬프트는 동일하다(실측: prompt_eval_count 동일).
        따라서 원본 ``input_schema`` 를 그대로 넘겨도 호출 계약은 보존된다.
        """
        client = self._get_client()
        payload = [{"role": "system", "content": system_prompt}] if system_prompt else []
        payload.extend(messages)
        logger.debug("Ollama tool call: messages=%s, tools=%d", payload, len(tools or []))

        # Ollama expects tool_calls.arguments to be a dict, not a JSON string.
        # The planner stores arguments as JSON strings (OpenAI format), so convert.
        sanitized_payload = []
        for msg in payload:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                sanitized_tool_calls = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except (json.JSONDecodeError, ValueError):
                            args = {}
                    sanitized_tool_calls.append(
                        {
                            **tc,
                            "function": {**fn, "arguments": args},
                        }
                    )
                sanitized_payload.append({**msg, "tool_calls": sanitized_tool_calls})
            else:
                sanitized_payload.append(msg)

        chat_kwargs: dict = {"model": self._model, "messages": sanitized_payload}
        if self._options:
            chat_kwargs["options"] = self._options
        self._apply_think(chat_kwargs)
        if tools:
            chat_kwargs["tools"] = list(tools)

        resp = self._invoke("chat_with_tools", chat_kwargs, lambda: client.chat(**chat_kwargs))
        msg = resp.get("message", {}) or {}
        content = _strip_think(msg.get("content", "") or "")

        tool_calls: list[ToolCall] = []
        for raw in msg.get("tool_calls") or []:
            fn = (raw or {}).get("function") or {}
            name = str(fn.get("name") or "").strip()
            if not name:
                continue
            tool_calls.append(
                ToolCall(
                    name=name,
                    arguments=parse_tool_arguments(fn.get("arguments")),
                    id=str(raw.get("id") or "").strip() or None,
                )
            )

        logger.debug(
            "Ollama tool response: %d call(s), content=%d chars",
            len(tool_calls),
            len(content),
        )
        return ToolTurn(
            content=content,
            tool_calls=tool_calls,
            assistant_message=deepcopy(_jsonable(msg)),
        )

    @traceable(run_type="llm", name="OllamaBridge.embed")
    def embed(self, text: str, *, _request_kind: str = "embed") -> list[float]:
        client = self._get_client()
        request = {"model": self._embedding_model, "prompt": text}
        raw = self._invoke(
            _request_kind,
            request,
            lambda: client.embeddings(model=self._embedding_model, prompt=text),
        )
        return raw["embedding"]

    # Qwen3-Embedding 등 instruction-tuned 임베더는 쿼리에 지시문을 붙여야
    # 문서(원문)와의 비대칭 임베딩으로 검색 분리도가 크게 좋아진다.
    _DEFAULT_QUERY_INSTRUCTION = (
        "Given a user's request, retrieve the most relevant stored robot "
        "knowledge such as place names, coordinates, and facts."
    )

    @traceable(run_type="llm", name="OllamaBridge.embed_query")
    def embed_query(self, text: str) -> list[float]:
        model = (self._embedding_model or "").lower()
        # 비대칭(instruction-tuned) 모델에만 쿼리 지시문을 부착한다.
        # 문서 저장은 embed()로 원문 그대로 → 기존 벡터/컬렉션 재인덱싱 불필요.
        if "qwen3-embedding" in model or "qwen3_embedding" in model:
            instruction = self._embed_query_instruction or self._DEFAULT_QUERY_INSTRUCTION
            text = f"Instruct: {instruction}\nQuery: {text}"
        return self.embed(text, _request_kind="embed_query")

    @traceable(run_type="llm", name="OllamaBridge.analyze_image")
    def analyze_image(self, prompt: str, image_base64: str) -> str:
        client = self._get_client()
        logger.debug("Ollama image analysis call: prompt='%s'", prompt)

        generate_kwargs = {"model": self._model, "prompt": prompt, "images": [image_base64]}
        if self._options:
            generate_kwargs["options"] = self._options
        self._apply_think(generate_kwargs)

        # base64 이미지는 통째로 기록하면 파일이 수 MB 가 되므로 길이와 앞부분만 남긴다.
        request = {
            "model": generate_kwargs.get("model"),
            "prompt": prompt,
            "images": [
                {
                    "base64_length": len(image_base64),
                    "base64_preview": image_base64[:200],
                }
            ],
            "options": generate_kwargs.get("options"),
            "think": generate_kwargs.get("think"),
        }

        resp = self._invoke(
            "analyze_image",
            request,
            lambda: client.generate(**generate_kwargs),
        )
        content = resp["response"]
        logger.debug("Ollama image analysis response: %s", content)
        return content
