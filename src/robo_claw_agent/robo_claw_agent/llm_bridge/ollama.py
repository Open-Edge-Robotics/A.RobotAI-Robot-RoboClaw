import ast
import json
import logging
import re
from typing import Any

from ..tracing import traceable
from .base import BaseLLMBridge

logger = logging.getLogger(__name__)

# think=true 시 일부 모델이 content에 <think>...</think>를 섞어 반환한다.
# 닫는 태그가 없는 경우(토큰 한도로 잘림)도 있으므로 두 패턴을 모두 처리한다.
_THINK_BLOCK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)
_UNCLOSED_THINK_PATTERN = re.compile(r"<think>.*", re.DOTALL)


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

    def _supports_think(self) -> bool:
        if self._think_supported is None:
            try:
                import inspect
                import ollama

                self._think_supported = "think" in inspect.signature(
                    ollama.Client.chat
                ).parameters
            except Exception as e:
                logger.debug("OllamaBridge: failed to detect think support (%s) → treating as unsupported", e)
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
        payload = (
            [{"role": "system", "content": system_prompt}] if system_prompt else []
        )
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

        resp = client.chat(**chat_kwargs)
        msg = resp.get("message", {})
        content = msg.get("content", "")

        # think=true 시 Ollama가 thinking 필드를 별도로 반환하는 경우 로깅.
        thinking = msg.get("thinking")
        if thinking:
            logger.info("Ollama thinking (first 300 chars): %s", str(thinking)[:300])

        # think=true 시 일부 모델/버전에서 content 안에 <think>...</think> 태그가
        # 포함될 수 있다. 태그 안의 텍스트에 JSON 조각이 있으면 파서가 잘못
        # 추출하므로 think 블록을 제거한 후 반환한다.
        cleaned = _THINK_BLOCK_PATTERN.sub("", content)
        # 닫는 태그 없이 잘린 think 블록은 이후 내용 전체가 사고 과정이므로 버린다.
        cleaned = _UNCLOSED_THINK_PATTERN.sub("", cleaned).strip()
        if cleaned != content:
            logger.debug("Stripped <think> block from Ollama response (%d → %d chars)",
                         len(content), len(cleaned))
            content = cleaned

        logger.debug("Ollama response: %s", content)
        return content

    @traceable(run_type="llm", name="OllamaBridge.embed")
    def embed(self, text: str) -> list[float]:
        client = self._get_client()
        return client.embeddings(model=self._embedding_model, prompt=text)["embedding"]

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
        return self.embed(text)

    @traceable(run_type="llm", name="OllamaBridge.analyze_image")
    def analyze_image(self, prompt: str, image_base64: str) -> str:
        client = self._get_client()
        logger.debug("Ollama image analysis call: prompt='%s'", prompt)

        generate_kwargs = {"model": self._model, "prompt": prompt, "images": [image_base64]}
        if self._options:
            generate_kwargs["options"] = self._options
        self._apply_think(generate_kwargs)

        resp = client.generate(**generate_kwargs)
        content = resp["response"]
        logger.debug("Ollama image analysis response: %s", content)
        return content
