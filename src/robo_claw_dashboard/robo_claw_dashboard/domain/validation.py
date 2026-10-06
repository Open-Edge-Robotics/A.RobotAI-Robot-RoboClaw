"""대시보드 입력 검증 — 에이전트에 전달할 채팅 메시지 규칙."""

DEFAULT_MAX_CHAT_CHARS = 2000


def validate_chat_message(
    message: str | None, *, max_chars: int = DEFAULT_MAX_CHAT_CHARS
) -> tuple[bool, str]:
    """채팅 메시지가 전송 가능한지 검증한다. (ok, error_message) 반환."""
    if not isinstance(message, str) or not message.strip():
        return False, "메시지가 비어 있습니다."
    if len(message) > max_chars:
        return False, f"메시지가 허용 길이({max_chars}자)를 초과했습니다."
    return True, ""
