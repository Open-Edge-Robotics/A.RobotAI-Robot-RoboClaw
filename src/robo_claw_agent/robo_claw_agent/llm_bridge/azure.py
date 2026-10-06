import logging
import os

from ..tracing import traceable, wrap_openai_client
from .base import DEFAULT_TIMEOUT_SEC, BaseLLMBridge, openai_message_to_text

logger = logging.getLogger(__name__)


class AzureOpenAIBridge(BaseLLMBridge):
    """Azure OpenAI 브릿지"""

    def __init__(
        self,
        deployment: str,
        embedding_deployment: str | None = None,
        api_version: str = "2024-02-15-preview",
        endpoint: str | None = None,
        api_key: str | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    ) -> None:
        self._deployment = deployment
        self._embedding_deployment = embedding_deployment or deployment
        self._api_version = api_version
        self._endpoint = endpoint or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        self._api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")
        self._timeout_sec = float(timeout_sec)
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AzureOpenAI

            # LangSmith 트레이싱 활성 시 클라이언트를 감싸 토큰/지연시간을 캡처한다.
            self._client = wrap_openai_client(
                AzureOpenAI(
                    azure_endpoint=self._endpoint,
                    api_key=self._api_key,
                    api_version=self._api_version,
                    timeout=self._timeout_sec,
                )
            )
        return self._client

    def validate_config(self) -> str | None:
        if not self._endpoint:
            return "Azure OpenAI: endpoint가 설정되지 않았습니다."
        if not self._api_key:
            return "Azure OpenAI: api_key가 설정되지 않았습니다."
        if not self._deployment:
            return "Azure OpenAI: deployment(model)가 설정되지 않았습니다."
        return None

    def healthcheck(self, timeout_sec: float = 10.0) -> str | None:
        try:
            from openai import AzureOpenAI

            client = AzureOpenAI(
                azure_endpoint=self._endpoint,
                api_key=self._api_key,
                api_version=self._api_version,
                timeout=timeout_sec,
            )
            client.models.list()
            return None
        except Exception as e:
            return f"Azure OpenAI 서버 연결 실패: {e}"

    @traceable(name="AzureOpenAIBridge.chat")
    def chat(self, messages, system_prompt=None, **kwargs):
        payload = (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        )
        payload.extend(messages)
        if "max_tokens" in kwargs:
            kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")

        logger.debug("Azure OpenAI call: messages=%s", payload)
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self._deployment,
            messages=payload,
            **kwargs,
        )
        content = openai_message_to_text(resp.choices[0].message)
        logger.debug("Azure OpenAI response: %s", content)
        return content

    @traceable(name="AzureOpenAIBridge.embed")
    def embed(self, text: str) -> list[float]:
        client = self._get_client()
        return (
            client.embeddings.create(input=[text], model=self._embedding_deployment)
            .data[0]
            .embedding
        )

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        resp = client.embeddings.create(
            input=texts, model=self._embedding_deployment
        )
        ordered = sorted(resp.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]

    @traceable(name="AzureOpenAIBridge.analyze_image")
    def analyze_image(self, prompt: str, image_base64: str) -> str:
        logger.debug("Azure OpenAI image analysis call: prompt='%s'", prompt)
        client = self._get_client()
        resp = client.chat.completions.create(
            model=self._deployment,
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
        logger.debug("Azure OpenAI image analysis response: %s", content)
        return content
