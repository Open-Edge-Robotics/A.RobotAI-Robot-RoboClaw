"""에이전트 상태 하트비트 워치독.

executor 가 콜백을 더 이상 dispatch 하지 않으면 rclpy 타이머도 함께 멈추므로,
타이머 기반 감시는 자기 자신이 멈춘 것을 감지할 수 없다. 이 워치독은 **별도 스레드**에서
마지막 ``_publish_status`` 시각만 확인한다.

동작 순서:

1. ``timeout_sec`` 동안 하트비트가 없으면 먼저 가벼운 복구(노드를 executor 에 재등록)를
   한 번 시도하고 경고를 남긴다.
2. 다음 점검에서도 하트비트가 없으면 ERROR 를 남기고 프로세스(컨테이너)를 종료해
   systemd 가 bringup 을 재기동하도록 한다.

ROS2/rclpy 에 의존하지 않으므로 대역(fake) 시계와 콜백으로 단위 테스트한다.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: 하트비트 유실 판정 기본 시간(초). 0 이하면 감시를 끈다.
DEFAULT_TIMEOUT_SEC = 30.0
#: 하트비트 확인 주기(초).
DEFAULT_CHECK_INTERVAL_SEC = 5.0


def is_heartbeat_stale(last_beat: float | None, now: float, timeout_sec: float) -> bool:
    """하트비트가 제한 시간을 넘겼는지 판단한다.

    ``timeout_sec`` 이 0 이하이거나 아직 하트비트가 없으면 감시하지 않는다
    (기동 직후 오탐 방지).
    """
    if timeout_sec <= 0.0 or last_beat is None:
        return False
    return (now - last_beat) >= timeout_sec


def _in_container() -> bool:
    """docker/podman 컨테이너 안에서 실행 중인지 확인한다."""
    return os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")


def terminate_for_restart() -> None:
    """컨테이너를 비정상 종료시켜 systemd(on-failure) 재기동을 유도한다.

    ``ros2 launch`` 는 자식 프로세스가 죽어도 exit code 0 으로 끝나므로
    (``on_exit=Shutdown()`` 포함) 에이전트만 종료하면 컨테이너가 정상 종료로
    처리되어 재기동되지 않는다. 컨테이너 안에서는 PID 1(launch)을 SIGKILL 해
    비정상 종료 코드를 만든다. 컨테이너가 아니면 프로세스만 종료한다.
    """
    if _in_container() and os.getpid() != 1:
        try:
            os.kill(1, signal.SIGKILL)
        except OSError:
            pass
    os._exit(1)


class HeartbeatWatchdog:
    """별도 스레드에서 에이전트 하트비트를 감시한다."""

    def __init__(
        self,
        *,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        check_interval_sec: float = DEFAULT_CHECK_INTERVAL_SEC,
        clock: Callable[[], float] = time.monotonic,
        recover: Callable[[], None] | None = None,
        on_timeout: Callable[[], None] | None = None,
        name: str = "robo-claw-heartbeat-watchdog",
    ) -> None:
        self._timeout_sec = float(timeout_sec)
        self._check_interval_sec = max(0.05, float(check_interval_sec))
        self._clock = clock
        self._recover = recover
        self._on_timeout = on_timeout or terminate_for_restart
        self._name = name
        self._lock = threading.Lock()
        self._last_beat: float | None = None
        self._recovery_attempted = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self._timeout_sec > 0.0

    @property
    def recovery_attempted(self) -> bool:
        return self._recovery_attempted

    def beat(self) -> None:
        """하트비트를 기록한다. 상태 퍼블리시 경로에서 호출한다."""
        with self._lock:
            self._last_beat = self._clock()

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self.beat()
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=1.0)
        self._thread = None

    def _last_beat_time(self) -> float | None:
        with self._lock:
            return self._last_beat

    def _run(self) -> None:
        while not self._stop_event.wait(self._check_interval_sec):
            if not is_heartbeat_stale(self._last_beat_time(), self._clock(), self._timeout_sec):
                if self._recovery_attempted:
                    logger.info("Agent heartbeat recovered after executor re-registration")
                    self._recovery_attempted = False
                continue

            if not self._recovery_attempted and self._recover is not None:
                self._recovery_attempted = True
                logger.warning(
                    "Agent heartbeat missing for %.1fs; attempting executor recovery",
                    self._timeout_sec,
                )
                try:
                    self._recover()
                except Exception:  # noqa: BLE001 - 복구 실패는 종료 경로로 이어진다
                    logger.exception("Executor recovery failed")
                continue

            logger.error(
                "Agent heartbeat missing for %.1fs after recovery attempt; "
                "terminating so the service can restart",
                self._timeout_sec,
            )
            self._on_timeout()
            return
