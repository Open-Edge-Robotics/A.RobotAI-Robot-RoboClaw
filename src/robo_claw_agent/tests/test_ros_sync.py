"""_ros_sync.wait_for_future_sync 동작 테스트.

핵심 회귀 조건: 이 함수는 **절대** ``rclpy.spin_once`` 를 호출하면 안 된다.
중첩 spin 은 상위 MultiThreadedExecutor 의 wait set 을 비워 모든 콜백을 멈춘다.
"""

import threading
import time

import pytest
import rclpy
from robo_claw_agent.manipulation_runtime._ros_sync import wait_for_future_sync

pytestmark = pytest.mark.unit


class _FakeFuture:
    """rclpy Future 최소 대역."""

    def __init__(self) -> None:
        self._done = False
        self._callbacks = []
        self.cancelled = False

    def add_done_callback(self, callback) -> None:
        self._callbacks.append(callback)
        if self._done:
            callback(self)

    def remove_done_callback(self, callback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def done(self) -> bool:
        return self._done

    def cancel(self) -> bool:
        self.cancelled = True
        return True

    def set_done(self) -> None:
        self._done = True
        for callback in list(self._callbacks):
            callback(self)


@pytest.fixture
def forbid_nested_spin(monkeypatch):
    """중첩 spin 호출을 즉시 실패로 만든다."""

    def _boom(*_args, **_kwargs):
        raise AssertionError("wait_for_future_sync must not call rclpy.spin_once")

    monkeypatch.setattr(rclpy, "spin_once", _boom)
    return _boom


def test_returns_without_nested_spin_when_future_completes(forbid_nested_spin):
    """다른 스레드가 완료시키는 future 를 중첩 spin 없이 기다린다."""
    future = _FakeFuture()

    def _complete_later() -> None:
        time.sleep(0.05)
        future.set_done()

    thread = threading.Thread(target=_complete_later)
    thread.start()
    try:
        wait_for_future_sync(object(), future, 2.0, "타임아웃")
    finally:
        thread.join(timeout=2.0)

    assert future.done() is True
    assert future.cancelled is False


def test_already_done_future_returns_immediately(forbid_nested_spin):
    """이미 완료된 future 는 즉시 반환한다."""
    future = _FakeFuture()
    future.set_done()

    started = time.monotonic()
    wait_for_future_sync(object(), future, 5.0, "타임아웃")

    assert time.monotonic() - started < 1.0


def test_timeout_cancels_future_and_raises_custom_error(forbid_nested_spin):
    """타임아웃 시 future 를 취소하고 지정한 예외 타입으로 실패한다."""
    future = _FakeFuture()

    with pytest.raises(RuntimeError, match="목표 미도달"):
        wait_for_future_sync(object(), future, 0.1, "목표 미도달", error_cls=RuntimeError)

    assert future.cancelled is True


def test_done_callback_is_removed_after_wait(forbid_nested_spin):
    """대기 종료 후 future 에 콜백을 남기지 않는다."""
    future = _FakeFuture()
    future.set_done()

    wait_for_future_sync(object(), future, 1.0, "타임아웃")

    assert future._callbacks == []
