import threading

# 전역 제어 플래그 — explore_skill._EXPLORE_ACTIVE 패턴과 동일
_AUTONOMOUS_ACTIVE = threading.Event()
_AUTONOMOUS_LOCK = threading.Lock()

# SleepBetweenCycles 전용 wake-up 이벤트 (stop_autonomous 시 즉시 깨어남)
_SLEEP_WAKE = threading.Event()


def claim_autonomous(active_event: threading.Event | None = None) -> bool:
    """Atomically claim the shared autonomous/reactive execution slot."""
    event = active_event or _AUTONOMOUS_ACTIVE
    with _AUTONOMOUS_LOCK:
        if event.is_set():
            return False
        event.set()
        return True


def release_autonomous() -> None:
    """Release the shared execution slot and wake any interrupted sleep."""
    with _AUTONOMOUS_LOCK:
        _AUTONOMOUS_ACTIVE.clear()
        _SLEEP_WAKE.set()
