import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

# LLM API 호출 기본 timeout (초)
DEFAULT_TIMEOUT_SEC = 60.0

Message = dict[str, str]


def openai_message_to_text(message: Any) -> str:
    """OpenAI 호환(OpenAI/Azure OpenAI) ChatCompletionMessage를 문자열로 변환한다.

    모델이 시스템 프롬프트의 JSON 지시를 무시하고 네이티브 tool_call로 응답하면
    ``message.content``가 ``None``이 된다. 그대로 ``or ""``로 반환하면 파서가
    "빈 응답"으로 보고 ``ValueError("Empty or invalid LLM response")``를 던지며,
    모델이 실제로 하려던 도구 호출 정보는 완전히 유실된다.

    ``tool_calls``가 있으면 이를 OpenAI 스타일 ``{"tool_calls": [...]}`` JSON 문자열로
    직렬화해 반환한다 — ``agent_node.utils.parse_llm_plan``의 기존 tool_calls 폴백
    경로(``_coerce_tool_call``)가 새 파싱 코드 없이 그대로 소화할 수 있는 형태다.
    """
    content = getattr(message, "content", None)
    if content:
        return content

    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return ""

    calls: list[dict[str, Any]] = []
    for tc in tool_calls:
        fn = getattr(tc, "function", None)
        calls.append(
            {
                "function": {
                    "name": getattr(fn, "name", None) if fn else None,
                    "arguments": getattr(fn, "arguments", "") if fn else "",
                }
            }
        )
    return json.dumps({"tool_calls": calls}, ensure_ascii=False)


class BaseLLMBridge(ABC):
    """LLM 백엔드 공통 추상 인터페이스"""

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> str:
        """메시지 목록으로 응답 생성"""

    def chat_stream(
        self,
        messages: list[Message],
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """스트리밍 응답 Generator"""
        yield self.chat(messages, system_prompt=system_prompt, **kwargs)

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """텍스트 임베딩 생성"""

    @abstractmethod
    def analyze_image(self, prompt: str, image_base64: str) -> str:
        """이미지 기반 시각 분석 수행"""

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """텍스트 목록을 임베딩한다.

        기본 구현은 ``embed`` 순차 호출이며, 배치 API를 지원하는
        프로바이더는 이 메서드를 override 해 단일 호출로 처리한다.
        """
        return [self.embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        """검색 쿼리용 임베딩을 생성한다.

        기본 구현은 문서와 동일한 대칭 임베딩(``embed``)이다. Qwen3-Embedding 처럼
        instruction-tuned 비대칭 임베더는 이 메서드를 override 해 쿼리 지시문을 붙인다
        (문서는 지시문 없이 원문으로 임베딩). 저장 벡터·재인덱싱에는 영향이 없다.
        """
        return self.embed(text)

    def validate_config(self) -> str | None:
        """프로바이더별 필수 설정값 검증.

        Returns:
            None if OK, 오류 메시지 if 필수값 누락.
        """
        return None

    def healthcheck(self, timeout_sec: float = 10.0) -> str | None:
        """LLM 서버 연결 가능 여부를 가볍게 확인한다.

        Returns:
            None if OK, 오류 메시지 if 연결 실패.
        """
        return None
