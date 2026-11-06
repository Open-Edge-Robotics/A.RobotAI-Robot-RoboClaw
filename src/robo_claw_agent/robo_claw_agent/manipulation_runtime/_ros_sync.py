"""ROS2 future 동기 대기 헬퍼 (중첩 spin 없음).

``agent_node`` 는 ``MultiThreadedExecutor`` 로 노드를 spin 한다. 그 상태에서
스킬 실행 콜백이 ``rclpy.spin_once(node)`` 로 다시 노드를 spin 하면 rclpy 내부의
노드-실행기 결합이 깨져, 상위 executor 의 wait set 이 비어버리고 **모든 콜백
(상태 타이머, joint_states, 액션/서비스)이 영구히 멈춘다**.

그래서 여기서는 spin 을 전혀 하지 않고 future 완료 콜백을 ``threading.Event`` 로
기다린다. 실행은 이미 executor 스레드들이 담당하고 있으므로 future 는 그대로
진행된다. (같은 이유로 ``ros_future.wait_for_future`` 도 이벤트 기반이다.)
"""

from __future__ import annotations

import threading
import time
from typing import Any

#: future 완료 콜백 등록 시 사용하는 이벤트 대기 폴링 간격(초). Event 가 완료를
#: 알려주므로 실제로는 wake-up 지연만 영향이 있다. 하위 호환을 위해 남긴다.
_POLL_INTERVAL_SEC = 0.05


def wait_for_future_sync(
    node: Any,
    future: Any,
    timeout_sec: float,
    timeout_msg: str,
    *,
    error_cls: type = Exception,
) -> None:
    """``future.done()`` 까지 이벤트로 동기 대기한다.

    타임아웃 시 ``future`` 를 취소하고 ``error_cls(timeout_msg)`` 를 발생시킨다.

    ``node`` 인자는 호출부 호환을 위해 유지한다. spin 은 executor 가 담당하므로
    이 함수는 노드를 spin 하지 않는다.
    """
    del node  # 중첩 spin 금지: 실행은 이미 executor 스레드가 담당한다.

    done = threading.Event()

    def _on_done(_future: Any) -> None:
        done.set()

    add_callback = getattr(future, "add_done_callback", None)
    remove_callback = getattr(future, "remove_done_callback", None)
    if callable(add_callback):
        add_callback(_on_done)
        completed = done.wait(timeout=_timeout_or_none(timeout_sec))
        if callable(remove_callback):
            remove_callback(_on_done)
    else:
        # done callback 을 지원하지 않는 future 대역(테스트/구버전)용 폴백.
        deadline = time.monotonic() + _timeout_or_none(timeout_sec)
        while not future.done():
            if time.monotonic() >= deadline:
                completed = False
                break
            done.wait(timeout=_POLL_INTERVAL_SEC)
        else:
            completed = True

    if not completed:
        try:
            future.cancel()
        except Exception:  # noqa: BLE001 - 취소 실패는 타임아웃 보고를 막지 않는다
            pass
        raise error_cls(timeout_msg)


def _timeout_or_none(timeout_sec: float) -> float:
    """음수/None 타임아웃을 Event.wait 의 '무한 대기'로 정규화한다."""
    try:
        value = float(timeout_sec)
    except (TypeError, ValueError):
        return -1.0
    return value if value > 0 else -1.0
