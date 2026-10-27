import logging
import os
from typing import Any

from ..tracing import traceable, wrap_anthropic_client
from .base import DEFAULT_TIMEOUT_SEC, BaseLLMBridge, ToolCall, ToolTurn, parse_tool_arguments

logger = logging.getLogger(__name__)

# Anthropic Messages API가 실제로 받는 생성 옵션 중, 이 브릿지가 그대로 전달할 것들.
# response_format 등 OpenAI 전용 kwargs는 여기 없으면 조용히(그러나 로깅과 함께) 무시된다.
_SUPPORTED_CHAT_KWARGS = ("temperature", "top_p", "top_k", "stop_sequences")


def to_anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """OpenAI/Ollama 형식 tool 스펙을 Anthropic ``input_schema`` 형식으로 변환한다.

    Anthropic 은 ``{"type": "function", "function": {...}}`` 래퍼를 쓰지 않고
    ``{"name", "description", "input_schema"}`` 를 직접 받는다.
    """
    converted: list[dict[str, Any]] = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        parameters = fn.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {"type": "object", "properties": {}}
        converted.append(
            {
                "name": name,
                "description": str(fn.get("description") or ""),
                "input_schema": parameters,
            }
        )
    return converted


def _extract_anthropic_text(response: Any) -> str:
    """Anthropic 응답의 content 블록 중 첫 번째 text 블록 텍스트를 반환한다.

    extended thinking이 켜져 있으면 content[0]이 ``thinking`` 블록일 수 있고, tool
    사용을 강제하면 ``tool_use`` 블록일 수 있다. 이때 무조건 ``content[0].text``에
    접근하면 AttributeError/IndexError가 원인 불명 상태로 호출부까지 올라간다.
    """
    blocks = getattr(response, "content", None) or []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            return block.text
    block_types = [getattr(b, "type", "?") for b in blocks] or "empty"
    raise ValueError(f"Anthropic 응답에 text 블록이 없습니다 (블록 타입: {block_types})")


class AnthropicBridge(BaseLLMBridge):
    """Anthropic Claude 브릿지"""

    def __init__(
        self,
        model: str = "claude-3-5-sonnet-20241022",
        api_key: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self._timeout_sec = float(timeout_sec)

    def validate_config(self) -> str | None:
        if not self._api_key:
            return "Anthropic: api_key가 설정되지 않았습니다."
        if not self._model:
            return "Anthropic: model이 설정되지 않았습니다."
        return None

    def healthcheck(self, timeout_sec: float = 10.0) -> str | None:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self._api_key, timeout=timeout_sec)
            client.messages.create(
                model=self._model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
            return None
        except Exception as e:
            return f"Anthropic 서버 연결 실패: {e}"

    @traceable(name="AnthropicBridge.chat")
    def chat(self, messages, system_prompt=None, **kwargs):
        import anthropic

        logger.debug("Anthropic call: messages=%s, system='%s'", messages, system_prompt)
        # LangSmith 트레이싱 활성 시 클라이언트를 감싸 토큰/지연시간을 캡처한다.
        client = wrap_anthropic_client(
            anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout_sec)
        )

        if "response_format" in kwargs:
            # Anthropic Messages API에는 OpenAI 스타일 JSON 모드가 없다. 시스템
            # 프롬프트 끝의 _RESPONSE_FORMAT_REMINDER(prompts.py)에만 의존한다 —
            # 예전처럼 조용히 버리지 않고 최소한 로그를 남겨, 다른 프로바이더로
            # 전환했을 때의 동작 차이를 디버깅할 수 있게 한다.
            logger.debug(
                "AnthropicBridge.chat: response_format=%r은 Anthropic Messages API가 "
                "네이티브로 지원하지 않아 무시됩니다(프롬프트 기반 JSON 지시에 의존).",
                kwargs["response_format"],
            )

        create_kwargs: dict[str, Any] = {"max_tokens": kwargs.get("max_tokens", 1024)}
        for key in _SUPPORTED_CHAT_KWARGS:
            if key in kwargs:
                create_kwargs[key] = kwargs[key]

        response = client.messages.create(
            model=self._model,
            system=system_prompt or "",
            messages=messages,
            **create_kwargs,
        )
        content = _extract_anthropic_text(response)
        logger.debug("Anthropic response: %s", content)
        return content

    def supports_tools(self) -> bool:
        """Anthropic Messages API 는 네이티브 tool_use 를 지원한다."""
        return True

    @traceable(name="AnthropicBridge.chat_with_tools")
    def chat_with_tools(self, messages, system_prompt=None, tools=None, **kwargs) -> ToolTurn:
        """도구 목록을 전달해 tool_use 요청을 받고, 호출 목록을 구조화해 반환한다."""
        import anthropic

        logger.debug("Anthropic tool call: messages=%s, tools=%d", messages, len(tools or []))
        client = wrap_anthropic_client(
            anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout_sec)
        )

        create_kwargs: dict[str, Any] = {"max_tokens": kwargs.get("max_tokens", 1024)}
        for key in _SUPPORTED_CHAT_KWARGS:
            if key in kwargs:
                create_kwargs[key] = kwargs[key]

        anthropic_tools = to_anthropic_tools(tools)
        if anthropic_tools:
            create_kwargs["tools"] = anthropic_tools

        response = client.messages.create(
            model=self._model,
            system=system_prompt or "",
            messages=messages,
            **create_kwargs,
        )

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in getattr(response, "content", None) or []:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                content_parts.append(getattr(block, "text", "") or "")
            elif block_type == "tool_use":
                name = str(getattr(block, "name", "") or "").strip()
                if not name:
                    continue
                tool_calls.append(
                    ToolCall(name=name, arguments=parse_tool_arguments(getattr(block, "input", None)))
                )

        content = "\n".join(part for part in content_parts if part).strip()
        logger.debug("Anthropic tool response: %d call(s)", len(tool_calls))
        return ToolTurn(content=content, tool_calls=tool_calls)

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError(
            "AnthropicBridge는 임베딩을 지원하지 않습니다. "
            "enable_rag 사용 시 ollama/openai 등 임베딩 가능한 provider를 사용하세요."
        )

    @traceable(name="AnthropicBridge.analyze_image")
    def analyze_image(self, prompt: str, image_base64: str) -> str:
        import anthropic

        logger.debug("Anthropic image analysis call: prompt='%s'", prompt)
        client = wrap_anthropic_client(
            anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout_sec)
        )
        resp = client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_base64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        content = resp.content[0].text
        logger.debug("Anthropic image analysis response: %s", content)
        return content
