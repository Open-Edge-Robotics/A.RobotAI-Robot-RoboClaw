"""자율협동 전역 제어 플래그."""

import threading

# 자율협동 루프가 실행 중인지 나타내는 이벤트. stop_autonomous_cooperate 가 clear() 한다.
_COOPERATE_ACTIVE = threading.Event()
# SleepBetweenCycles 대기 중 즉시 깨우기 위한 이벤트.
_COOPERATE_WAKE = threading.Event()
_COOPERATE_LOCK = threading.Lock()
_COOPERATE_THREAD: threading.Thread | None = None
