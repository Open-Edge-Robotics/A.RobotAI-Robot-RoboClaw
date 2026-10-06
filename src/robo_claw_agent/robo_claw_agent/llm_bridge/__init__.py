from typing import Any

from .base import BaseLLMBridge, Message, DEFAULT_TIMEOUT_SEC
from .ollama import OllamaBridge
from .openai import OpenAIBridge
from .azure import AzureOpenAIBridge
from .anthropic import AnthropicBridge

def create_llm_bridge(provider: str, **kwargs: Any) -> BaseLLMBridge:
    _registry = {
        "ollama": OllamaBridge,
        "openai": OpenAIBridge,
        "azure": AzureOpenAIBridge,
        "anthropic": AnthropicBridge,
    }
    cls = _registry.get(provider.lower())
    if cls is None:
        supported = ", ".join(sorted(_registry.keys()))
        raise ValueError(f"지원하지 않는 LLM provider입니다: {provider} (지원: {supported})")

    # options_json은 ollama 전용이므로 먼저 추출하여 다른 LLM으로 전달되는 것을 방지합니다.
    options_json = kwargs.pop("options_json", None)

    if provider.lower() == "azure":
        if "model" in kwargs:
            kwargs["deployment"] = kwargs.pop("model")
        if "embedding_model" in kwargs:
            kwargs["embedding_deployment"] = kwargs.pop("embedding_model")
    elif provider.lower() == "ollama":
        kwargs.pop("api_key", None)
        endpoint = kwargs.pop("endpoint", None)
        if endpoint:
            kwargs["base_url"] = endpoint
        if options_json:
            kwargs["options_json"] = options_json
    return cls(**kwargs)
__all__ = [
    "BaseLLMBridge",
    "Message",
    "DEFAULT_TIMEOUT_SEC",
    "OllamaBridge",
    "OpenAIBridge",
    "AzureOpenAIBridge",
    "AnthropicBridge",
    "create_llm_bridge",
]
