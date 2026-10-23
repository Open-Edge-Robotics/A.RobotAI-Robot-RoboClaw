import logging
import os

from ..tracing import traceable, wrap_openai_client
from .base import (
    DEFAULT_TIMEOUT_SEC,
    BaseLLMBridge,
    ToolCall,
    ToolTurn,
    openai_message_to_text,
    parse_tool_arguments,
)

logger = logging.getLogger(__name__)


class OpenAIBridge(BaseLLMBridge):
    """OpenAI API 브릿지"""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        embedding_model: str = "text-embedding-3-small",
        api_key: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._model = model
        self._embedding_model = embedding_model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._timeout_sec = float(timeout_sec)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            # LangSmith 트레이싱 활성 시 클라이언트를 감싸 토큰/지연시간을 캡처한다.
            self._client = wrap_openai_client(
                OpenAI(api_key=self._api_key, timeout=self._timeout_sec)
            )
        return self._client

    def validate_config(self) -> str | None:
        if not self._api_key:
            return "OpenAI: api_key가 설정되지 않았습니다."
        if not self._model:
            return "OpenAI: model이 설정되지 않았습니다."
        return None

    def healthcheck(self, timeout_sec: float = 10.0) -> str | None:
        try:
            from openai import OpenAI

            client = OpenAI(api_key=self._api_key, timeout=timeout_sec)
            client.models.list()
            return None
        except Exception as e:
            return f"OpenAI 서버 연결 실패: {e}"

    @traceable(name="OpenAIBridge.chat")
    def chat(self, messages, system_prompt=None, **kwargs):
        payload = (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        )
        payload.extend(messages)
        if "max_tokens" in kwargs:
            kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")

        logger.debug("OpenAI call: messages=%s", payload)
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self._model,
            messages=payload,
            **kwargs,
        )
        content = openai_message_to_text(resp.choices[0].message)
        logger.debug("OpenAI response: %s", content)
        return content

    def supports_tools(self) -> bool:
        """OpenAI Chat Completions 는 네이티브 tool calling 을 지원한다."""
        return True

    @traceable(name="OpenAIBridge.chat_with_tools")
    def chat_with_tools(self, messages, system_prompt=None, tools=None, **kwargs) -> ToolTurn:
        """도구 목록을 전달해 호출 요청을 받고, 호출 목록을 구조화해 반환한다."""
        payload = (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        )
        payload.extend(messages)
        if "max_tokens" in kwargs:
            kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")

        logger.debug("OpenAI tool call: messages=%s, tools=%d", payload, len(tools or []))
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self._model,
            messages=payload,
            tools=list(tools or []),
            **kwargs,
        )
        message = resp.choices[0].message
        content = message.content or ""
        tool_calls = [
            ToolCall(
                name=str(call.function.name),
                arguments=parse_tool_arguments(call.function.arguments),
                id=str(getattr(call, "id", "") or "").strip() or None,
            )
            for call in (message.tool_calls or [])
            if getattr(call.function, "name", None)
        ]
        logger.debug("OpenAI tool response: %d call(s)", len(tool_calls))
        return ToolTurn(content=content, tool_calls=tool_calls)

    @traceable(name="OpenAIBridge.embed")
    def embed(self, text: str) -> list[float]:
        client = self._get_client()
        resp = client.embeddings.create(input=[text], model=self._embedding_model)
        return resp.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        resp = client.embeddings.create(input=texts, model=self._embedding_model)
        ordered = sorted(resp.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    @traceable(name="OpenAIBridge.analyze_image")
    def analyze_image(self, prompt: str, image_base64: str) -> str:
        logger.debug("OpenAI image analysis call: prompt='%s'", prompt)
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self._model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            },
                        },
                    ],
                }
            ],
            max_completion_tokens=1500,
        )
        content = resp.choices[0].message.content or ""
        logger.debug("OpenAI image analysis response: %s", content)
        return content
