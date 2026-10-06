"""태스크 큐 — 순차 실행 기반 요청 처리.

여러 사용자 요청이 동시에 들어와도 순차적으로 처리하도록 보장한다.
ExecuteTask action goal과 ExecuteSkill service 요청이 모두 이 큐를 통과한다.

동작:
1. 요청이 들어오면 큐에 적재하고 즉시 반환하지 않고 대기한다.
2. 큐 워커 스레드가 하나씩 꺼내서 실행 콜백을 호출한다.
3. 실행이 완료되면 대기 중인 호출자에게 결과를 전달한다.
4. 큐가 가득 차면 거절한다(max_size 초과).
"""

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# 큐를 우회하고 즉시 실행해도 안전한 읽기 전용 스킬들.
READONLY_SKILLS: frozenset[str] = frozenset({
    "get_status",
    "query_state",
    "list_peer_robots",
    "query_peer_status",
    "query_peer_capabilities",
    "rag_status",
    "rag_search",
    "rag_list",
    "list_files",
    "list_butler_scripts",
    "get_detections",
    "get_distance",
    "ros_command",
})

# 큐를 우회해 **즉시** 실행해야 하는 정지/중단 스킬.
# 단일 워커 큐 뒤에 서면 실행 중인 장기 태스크가 끝날 때까지 정지 명령이 지연된다.
# 특히 분해된 복합 명령은 "단계 수 × (1+재시도)"만큼 워커를 점유하므로, 정지 요청이
# 서비스 타임아웃으로 실패할 수 있다. 진행 중인 동작을 멈추는 것이 목적인 스킬이므로
# 대기열과 무관하게 선점 실행한다.
PREEMPTIVE_SKILLS: frozenset[str] = frozenset({
    "emergency_stop",
    "stop",
    "stop_patrol",
    "stop_explore",
    "stop_monitor",
    "stop_autonomous",
})


def is_readonly_skill(skill_name: str) -> bool:
    """읽기 전용 스킬인지 판정한다."""
    return skill_name in READONLY_SKILLS


def bypasses_queue(skill_name: str) -> bool:
    """큐를 거치지 않고 즉시 실행할 스킬인지 판정한다."""
    return skill_name in READONLY_SKILLS or skill_name in PREEMPTIVE_SKILLS


@dataclass
class QueuedTask:
    """큐에 적재되는 하나의 태스크 요청."""

    task_id: str
    instruction: str
    execute_fn: Callable[[], Any]
    # 실행 전 콜백 (대기 중 → 실행 전환 시 피드백용)
    on_start: Callable[[], None] | None = None
    # 대기 중 콜백 (큐에 적재되었을 때 피드백용)
    on_queued: Callable[[], None] | None = None
    # 취소 플래그
    cancelled: bool = False


@dataclass
class TaskQueueMetrics:
    """큐 운영 메트릭."""

    total_received: int = 0
    total_completed: int = 0
    total_rejected: int = 0
    total_cancelled: int = 0
    current_queue_depth: int = 0


class TaskQueue:
    """순차 실행 기반 태스크 큐.

    단일 워커 스레드가 큐에서 하나씩 꺼내 실행한다.
    동시 실행을 허용하지 않아 _state / _current_skill 경쟁을 해결한다.
    """

    def __init__(self, max_size: int = 8) -> None:
        self._queue: queue.Queue[QueuedTask | None] = queue.Queue(maxsize=max_size)
        self._max_size = max_size
        self._worker: threading.Thread | None = None
        self._running = threading.Event()
        self._lock = threading.Lock()
        self._metrics = TaskQueueMetrics()
        self._counter = 0
        self._current_task: QueuedTask | None = None

    def start(self) -> None:
        """워커 스레드를 시작한다."""
        if self._worker is not None:
            return
        self._running.set()
        self._worker = threading.Thread(
            target=self._run_worker, daemon=True, name="task-queue-worker"
        )
        self._worker.start()
        logger.info("TaskQueue worker started (max_size=%d)", self._max_size)

    def stop(self) -> None:
        """워커 스레드를 중지한다."""
        self._running.clear()
        self._queue.put(None)  # sentinel
        if self._worker is not None:
            self._worker.join(timeout=5.0)
            self._worker = None

    def submit(self, task: QueuedTask) -> bool:
        """태스크를 큐에 적재한다. 큐가 가득 차면 False 반환."""
        with self._lock:
            self._counter += 1
            task.task_id = task.task_id or f"task-{self._counter}"
            self._metrics.total_received += 1
            self._metrics.current_queue_depth = self._queue.qsize() + (
                1 if self._current_task is not None else 0
            )

        try:
            self._queue.put_nowait(task)
        except queue.Full:
            with self._lock:
                self._metrics.total_rejected += 1
            logger.warning(
                "TaskQueue full (max_size=%d) — rejecting task: %s",
                self._max_size,
                task.task_id,
            )
            return False

        if task.on_queued is not None:
            try:
                task.on_queued()
            except Exception:
                logger.debug("on_queued callback error (ignored)", exc_info=True)

        logger.info(
            "TaskQueue: submitted '%s' (instruction: %s, depth=%d)",
            task.task_id,
            task.instruction[:80],
            self._queue.qsize(),
        )
        return True

    def cancel_task(self, task_id: str) -> bool:
        """큐에 대기 중인 태스크를 취소 표시한다.

        현재 실행 중인 태스크는 취소할 수 없다(실행 완료까지 대기).
        Returns: 큐에서 찾아 취소 표시했으면 True, 못 찾았으면 False.
        """
        with self._lock:
            if self._current_task is not None and self._current_task.task_id == task_id:
                logger.info(
                    "TaskQueue: task '%s' is currently running — cannot cancel",
                    task_id,
                )
                return False

        # 큐를 순회하며 취소 표시 — queue.Queue는 임의 접근을 지원하지 않으므로
        # 드레인 후 재적재한다.
        items: list[QueuedTask] = []
        found = False
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                items.append(None)
                break
            if item.task_id == task_id:
                item.cancelled = True
                found = True
                with self._lock:
                    self._metrics.total_cancelled += 1
                logger.info("TaskQueue: cancelled queued task '%s'", task_id)
            else:
                items.append(item)

        # 재적재
        for item in items:
            if item is not None:
                self._queue.put_nowait(item)

        return found

    @property
    def metrics(self) -> TaskQueueMetrics:
        with self._lock:
            return TaskQueueMetrics(
                total_received=self._metrics.total_received,
                total_completed=self._metrics.total_completed,
                total_rejected=self._metrics.total_rejected,
                total_cancelled=self._metrics.total_cancelled,
                current_queue_depth=self._queue.qsize()
                + (1 if self._current_task is not None else 0),
            )

    @property
    def is_busy(self) -> bool:
        """현재 실행 중인 태스크가 있는지 반환."""
        with self._lock:
            return self._current_task is not None

    def is_instruction_active(self, instruction: str) -> bool:
        """동일한 지시사항이 현재 실행 중이거나 대기 큐에 쌓여있는지 검사."""
        clean_target = instruction.strip()
        if not clean_target:
            return False
        with self._lock:
            if self._current_task is not None and self._current_task.instruction.strip() == clean_target:
                return True
            for item in list(self._queue.queue):
                if item is not None and not item.cancelled and item.instruction.strip() == clean_target:
                    return True
        return False

    def _run_worker(self) -> None:
        """워커 스레드 메인 루프 — 큐에서 하나씩 꺼내 실행한다."""
        logger.info("TaskQueue worker loop started")
        while self._running.is_set():
            try:
                task = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if task is None:
                # sentinel — 종료
                break

            if task.cancelled:
                logger.info("TaskQueue: skipping cancelled task '%s'", task.task_id)
                with self._lock:
                    self._current_task = None
                continue

            with self._lock:
                self._current_task = task

            if task.on_start is not None:
                try:
                    task.on_start()
                except Exception:
                    logger.debug("on_start callback error (ignored)", exc_info=True)

            logger.info(
                "TaskQueue: executing '%s' (instruction: %s)",
                task.task_id,
                task.instruction[:80],
            )
            try:
                task.execute_fn()
            except Exception:
                logger.exception(
                    "TaskQueue: unhandled exception in task '%s'", task.task_id
                )
            finally:
                with self._lock:
                    self._current_task = None
                    self._metrics.total_completed += 1
                self._queue.task_done()

        logger.info("TaskQueue worker loop ended")
