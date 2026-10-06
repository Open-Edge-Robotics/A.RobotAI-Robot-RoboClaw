"""자율/반응형 스킬의 행동 트리(BT) 실행 공통 헬퍼.

스레드 시작, blackboard 주입, 반응형 틱 루프처럼 여러 스킬에서 반복되던
패턴을 모은다. (자율 행동 스킬의 사이클 루프는 고유 로직이라 별도 유지)
"""

import logging
import threading
import time
from collections.abc import Callable, Iterable
from typing import Any

from .core import NodeStatus

logger = logging.getLogger(__name__)


def start_bt_thread(run_fn: Callable[[], None]) -> threading.Thread:
    """BT 루프 함수를 데몬 스레드로 시작한다."""
    thread = threading.Thread(target=run_fn, daemon=True)
    thread.start()
    return thread


def wire_blackboard(nodes: Iterable[Any], blackboard: dict[str, Any]) -> None:
    """노드들에 공유 blackboard를 주입한다."""
    for n in nodes:
        if hasattr(n, "_blackboard"):
            n._blackboard = blackboard


def run_reactive_tick_loop(
    root: Any,
    active_event: threading.Event,
    *,
    log_prefix: str,
    interval_sec: float = 0.5,
) -> None:
    """RUNNING이 아니면 종료하는 표준 BT 틱 루프 (반응형 스킬용)."""
    while active_event.is_set():
        try:
            status = root.tick()
            if status != NodeStatus.RUNNING:
                logger.info("%s BT terminated status=%s", log_prefix, status.value)
                break
            time.sleep(interval_sec)
        except Exception as exc:
            logger.exception("%s BT tick exception: %s", log_prefix, exc)
            break
