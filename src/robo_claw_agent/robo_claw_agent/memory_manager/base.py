from typing import Literal, Protocol

BackendType = Literal["json", "sqlite"]


class Embedder(Protocol):
    """임베딩 생성을 위한 최소 인터페이스 프로토콜"""

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
