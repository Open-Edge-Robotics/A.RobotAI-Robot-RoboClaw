from datetime import datetime
from typing import Any


class EpisodicMixin:
    """에피소드 메모리 (단기/장기 이벤트 로깅 및 대화 기록) 전담"""

    _short_term: list[dict[str, Any]]
    _max_short_term: int

    # StorageMixin에서 제공될 메서드
    def _persist_event(self, entry: dict[str, Any]) -> None: ...
    def _get_persisted_events(self, limit: int | None = None) -> list[dict[str, Any]]: ...

    def add_event(self, event_type: str, data: dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": event_type,
            "data": data,
        }
        self._short_term.append(entry)
        if len(self._short_term) > self._max_short_term:
            oldest = self._short_term.pop(0)
            self._persist_event(oldest)

    def get_recent(self, n: int = 10) -> list[dict[str, Any]]:
        if n <= 0:
            return []
        combined = self._get_persisted_events(limit=n) + self._short_term
        return combined[-n:]

    def get_conversation_history(self, n: int = 10) -> list[dict[str, str]]:
        combined = self._get_persisted_events() + self._short_term
        combined = combined[-n * 2 :]
        messages = []
        for e in combined:
            if e["type"] == "task_received":
                messages.append(
                    {"role": "user", "content": e["data"].get("instruction", "")}
                )
            elif e["type"] == "task_completed":
                messages.append(
                    {"role": "assistant", "content": e["data"].get("result", "")}
                )
        return messages
