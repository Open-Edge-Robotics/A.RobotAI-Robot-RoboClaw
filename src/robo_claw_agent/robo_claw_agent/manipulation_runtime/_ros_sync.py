"""ROS2 동기식 future 대기 공통 헬퍼.

``backend.py``(MoveIt 서비스) 와 ``stretch_backend.py``(FollowJointTrajectory
액션 / Trigger 서비스) 에서 동일하게 쓰던 "spin_once + sleep fallback + 타임아웃
취소" 대기 루프를 하나로 묶었다.

``agent_node`` 가 ``MultiThreadedExecutor`` 를 쓰면 ``rclpy.spin_once`` 가 차단되므로
``except`` 경로의 ``time.sleep`` 이 실제 주 경로가 된다.
"""

import time
from typing import Any

import rclpy


def wait_for_future_sync(
    node: Any,
    future: Any,
    timeout_sec: float,
    timeout_msg: str,
    *,
    error_cls: type = Exception,
) -> None:
    """``future.done()`` 까지 동기 대기.

    타임아웃 시 ``future.cancel()`` 후 ``error_cls(timeout_msg)`` 를 발생시킨다.
    대기 루프 도중 이미 다른 executor 가 spin 중이면 ``spin_once`` 가 예외를 던지며,
    이 경우 ``time.sleep`` 으로 fallback 한다(데드락 방지).
    """

    start = time.time()
    while not future.done():
        try:
            rclpy.spin_once(node, timeout_sec=0.05)
        except Exception:  # noqa: BLE001 - executor 중복 spin 등 무해한 차단
            time.sleep(0.05)
        if time.time() - start > timeout_sec:
            future.cancel()
            raise error_cls(timeout_msg)
