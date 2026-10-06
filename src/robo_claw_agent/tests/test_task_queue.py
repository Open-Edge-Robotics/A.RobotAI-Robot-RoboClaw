"""태스크 큐(TaskQueue) 순차 실행 단위 테스트.

ROS2 불필요. threading 기반 큐의 순차 실행, 거부, 취소, 메트릭을 검증한다.
conftest 가 agent_node 패키지를 빈 stub 으로 등록하므로, task_queue.py 를
직접 파일에서 로드한다.
"""

import importlib.util
import os
import threading
import time

# task_queue.py 를 직접 로드 (agent_node.__init__ 의 rclpy 의존성 우회)
_tq_path = os.path.join(
    os.path.dirname(__file__), "..", "robo_claw_agent", "agent_node", "task_queue.py"
)
_spec = importlib.util.spec_from_file_location(
    "robo_claw_agent.agent_node.task_queue", _tq_path
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
QueuedTask = _mod.QueuedTask
TaskQueue = _mod.TaskQueue


def _make_task(
    instruction: str,
    execute_fn,
    on_queued=None,
    on_start=None,
) -> QueuedTask:
    return QueuedTask(
        task_id="",
        instruction=instruction,
        execute_fn=execute_fn,
        on_queued=on_queued,
        on_start=on_start,
    )


class TestTaskQueueSequential:
    """순차 실행 보장 테스트."""

    def test_tasks_execute_sequentially(self):
        """두 태스크가 동시에 제출되어도 순차적으로 실행된다."""
        tq = TaskQueue(max_size=8)
        tq.start()
        try:
            results: list[str] = []
            lock = threading.Lock()

            def make_fn(label: str):
                def _fn():
                    with lock:
                        results.append(f"{label}-start")
                    time.sleep(0.05)
                    with lock:
                        results.append(f"{label}-end")
                return _fn

            t1 = _make_task("task1", make_fn("A"))
            t2 = _make_task("task2", make_fn("B"))
            tq.submit(t1)
            tq.submit(t2)

            time.sleep(0.3)

            # A가 완전히 끝난 후 B가 시작해야 함
            assert results == ["A-start", "A-end", "B-start", "B-end"]
        finally:
            tq.stop()

    def test_no_concurrent_execution(self):
        """두 태스크가 절대 동시에 실행되지 않는다."""
        tq = TaskQueue(max_size=8)
        tq.start()
        try:
            concurrent_count = 0
            max_concurrent = 0
            lock = threading.Lock()

            def _fn():
                nonlocal concurrent_count, max_concurrent
                with lock:
                    concurrent_count += 1
                    max_concurrent = max(max_concurrent, concurrent_count)
                time.sleep(0.1)
                with lock:
                    concurrent_count -= 1

            for i in range(3):
                tq.submit(_make_task(f"task-{i}", _fn))

            time.sleep(0.5)
            assert max_concurrent == 1
        finally:
            tq.stop()


class TestTaskQueueReject:
    """큐 가득 참 거부 테스트."""

    def test_full_queue_rejects(self):
        """max_size를 초과하면 거절된다."""
        tq = TaskQueue(max_size=2)
        tq.start()
        try:
            block_event = threading.Event()
            done_event = threading.Event()

            def _block_fn():
                block_event.wait(timeout=5.0)
                done_event.set()

            # 첫 태스크: 블로킹 (워커가 꺼내서 실행 중)
            t1 = _make_task("blocking", _block_fn)
            assert tq.submit(t1) is True

            # 워커가 t1을 꺼낼 때까지 대기
            block_event.wait(timeout=2.0)
            time.sleep(0.05)

            # 두 번째: 큐에 적재 (큐 공간 2개 중 1개 사용)
            t2 = _make_task("queued1", lambda: time.sleep(0.01))
            assert tq.submit(t2) is True

            # 세 번째: 큐에 적재 (큐 공간 2개 중 2개 사용)
            t3 = _make_task("queued2", lambda: time.sleep(0.01))
            assert tq.submit(t3) is True

            # 네 번째: 거절되어야 함 (큐가 가득 참)
            t4 = _make_task("rejected", lambda: None)
            assert tq.submit(t4) is False

            # 정리
            block_event.set()
            done_event.wait(timeout=5.0)
            time.sleep(0.2)

            m = tq.metrics
            assert m.total_rejected >= 1
        finally:
            tq.stop()


class TestTaskQueueCallbacks:
    """콜백 테스트."""

    def test_on_queued_called(self):
        """submit 시 on_queued 콜백이 호출된다."""
        tq = TaskQueue(max_size=4)
        tq.start()
        try:
            called = threading.Event()
            t = _make_task("test", lambda: time.sleep(0.05), on_queued=called.set)
            tq.submit(t)
            assert called.wait(timeout=1.0)
        finally:
            tq.stop()

    def test_on_start_called(self):
        """실행 시작 시 on_start 콜백이 호출된다."""
        tq = TaskQueue(max_size=4)
        tq.start()
        try:
            called = threading.Event()
            done = threading.Event()
            t = _make_task(
                "test",
                lambda: done.set(),
                on_start=called.set,
            )
            tq.submit(t)
            assert called.wait(timeout=2.0)
            done.wait(timeout=2.0)
        finally:
            tq.stop()


class TestTaskQueueMetrics:
    """메트릭 테스트."""

    def test_metrics_after_completion(self):
        """태스크 완료 후 메트릭이 정확하다."""
        tq = TaskQueue(max_size=4)
        tq.start()
        try:
            done1 = threading.Event()
            done2 = threading.Event()

            tq.submit(_make_task("t1", done1.set))
            tq.submit(_make_task("t2", done2.set))

            done1.wait(timeout=2.0)
            done2.wait(timeout=2.0)
            time.sleep(0.1)

            m = tq.metrics
            assert m.total_received == 2
            assert m.total_completed == 2
            assert m.total_rejected == 0
        finally:
            tq.stop()

    def test_is_busy_flag(self):
        """실행 중일 때 is_busy가 True, 유휴일 때 False."""
        tq = TaskQueue(max_size=4)
        tq.start()
        try:
            block = threading.Event()
            release = threading.Event()

            def _fn():
                block.set()
                release.wait(timeout=5.0)

            tq.submit(_make_task("blocking", _fn))
            block.wait(timeout=2.0)
            time.sleep(0.05)
            assert tq.is_busy is True

            release.set()
            time.sleep(0.2)
            assert tq.is_busy is False
        finally:
            tq.stop()


class TestTaskQueueCancel:
    """취소 테스트."""

    def test_cancel_queued_task(self):
        """대기 중인 태스크를 취소하면 실행되지 않는다."""
        tq = TaskQueue(max_size=4)
        tq.start()
        try:
            block = threading.Event()
            release = threading.Event()
            executed: list[str] = []

            def _block_fn():
                block.set()
                release.wait(timeout=5.0)
                executed.append("block")

            def _cancel_fn():
                executed.append("cancelled")

            t1 = _make_task("blocking", _block_fn)
            t2 = _make_task("to_cancel", _cancel_fn)
            tq.submit(t1)
            tq.submit(t2)

            block.wait(timeout=2.0)
            # t1 실행 중, t2 큐 대기
            result = tq.cancel_task(t2.task_id)
            assert result is True

            release.set()
            time.sleep(0.2)

            assert "cancelled" not in executed
        finally:
            tq.stop()

    def test_cancel_running_task_fails(self):
        """현재 실행 중인 태스크는 취소할 수 없다."""
        tq = TaskQueue(max_size=4)
        tq.start()
        try:
            block = threading.Event()
            release = threading.Event()

            def _fn():
                block.set()
                release.wait(timeout=5.0)

            t = _make_task("running", _fn)
            tq.submit(t)
            block.wait(timeout=2.0)

            result = tq.cancel_task(t.task_id)
            assert result is False

            release.set()
            time.sleep(0.1)
        finally:
            tq.stop()


class TestTaskQueueStop:
    """stop 테스트."""

    def test_stop_gracefully(self):
        """stop이 워커를 정상 종료한다."""
        tq = TaskQueue(max_size=4)
        tq.start()
        done = threading.Event()
        tq.submit(_make_task("task", done.set))
        done.wait(timeout=2.0)
        tq.stop()
        # stop 후에도 메트릭 조회 가능
        m = tq.metrics
        assert m.total_completed >= 1


class TestQueueBypassPolicy:
    """큐를 우회해 즉시 실행할 스킬 정책.

    단일 워커 큐는 순차 실행을 보장하지만, 그 뒤에 정지 명령이 줄을 서면 실행
    중인 장기 태스크가 끝날 때까지 로봇을 멈출 수 없다. 특히 복합 명령 분해는
    "단계 수 × (1+재시도)"만큼 워커를 점유하므로 노출 시간이 크게 늘어난다.
    """

    def test_stop_skills_bypass_the_queue(self):
        for name in (
            "emergency_stop",
            "stop",
            "stop_patrol",
            "stop_explore",
            "stop_monitor",
            "stop_autonomous",
        ):
            assert _mod.bypasses_queue(name) is True, name

    def test_readonly_skills_bypass_the_queue(self):
        for name in ("get_status", "rag_search", "get_distance"):
            assert _mod.bypasses_queue(name) is True, name
            assert _mod.is_readonly_skill(name) is True, name

    def test_action_skills_still_go_through_the_queue(self):
        for name in ("navigate_to", "grasp", "move_relative", "patrol", "explore"):
            assert _mod.bypasses_queue(name) is False, name

    def test_stop_skills_are_not_classified_as_readonly(self):
        """정지 스킬은 물리 상태를 바꾸므로 '읽기 전용'과는 구분되어야 한다."""
        assert _mod.is_readonly_skill("emergency_stop") is False
        assert _mod.is_readonly_skill("stop") is False
