"""자율협동 전역 제어 상태.

``autonomous_skill.globals`` 와 동일한 claim/release 패턴을 쓴다. 시작/중지 판정과
스레드 등록·반납은 모듈 밖에서 Event/Lock 을 직접 조작하지 않고 아래 함수로만
수행한다(점유 확인과 설정 사이의 경합을 막는다).

하위 호환을 위해 ``_COOPERATE_*`` 이름은 계속 노출하지만, 새 코드는 함수 API 를
사용한다.
"""

from __future__ import annotations

import threading

# 자율협동 루프가 실행 중인지 나타내는 이벤트. release_cooperation 이 clear 한다.
_COOPERATE_ACTIVE = threading.Event()
# SleepBetweenCycles 대기 중 즉시 깨우기 위한 이벤트.
_COOPERATE_WAKE = threading.Event()
_COOPERATE_LOCK = threading.Lock()
_COOPERATE_THREAD: threading.Thread | None = None


def is_cooperation_active() -> bool:
    """자율협동 루프가 실행 중인지."""
    return _COOPERATE_ACTIVE.is_set()


def claim_cooperation() -> bool:
    """자율협동 슬롯을 원자적으로 점유한다. 이미 실행 중이면 False."""
    with _COOPERATE_LOCK:
        if _COOPERATE_ACTIVE.is_set():
            return False
        if _COOPERATE_THREAD is not None and _COOPERATE_THREAD.is_alive():
            return False
        _COOPERATE_ACTIVE.set()
        return True


def set_cooperation_thread(thread: threading.Thread | None) -> None:
    """점유한 슬롯의 실행 스레드를 등록/해제한다."""
    with _COOPERATE_LOCK:
        global _COOPERATE_THREAD
        _COOPERATE_THREAD = thread


def get_cooperation_thread() -> threading.Thread | None:
    with _COOPERATE_LOCK:
        return _COOPERATE_THREAD


def clear_cooperation_thread_if_current() -> None:
    """현재 스레드가 슬롯 소유자일 때만 스레드 핸들을 비운다."""
    with _COOPERATE_LOCK:
        global _COOPERATE_THREAD
        if _COOPERATE_THREAD is threading.current_thread():
            _COOPERATE_THREAD = None


def release_cooperation(*, wake: bool = True) -> None:
    """자율협동 슬롯을 반납한다. ``wake=True`` 면 대기 중 루프를 즉시 깨운다."""
    with _COOPERATE_LOCK:
        _COOPERATE_ACTIVE.clear()
        if wake:
            _COOPERATE_WAKE.set()
