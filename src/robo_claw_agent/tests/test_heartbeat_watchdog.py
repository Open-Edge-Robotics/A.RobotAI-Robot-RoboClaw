"""상태 하트비트 워치독 테스트.

executor 가 멈춘 상황을 흉내 내어 복구 시도 → 재기동 유도 순서를 검증한다.
"""

import threading
import time

import pytest
from robo_claw_agent.agent_node import heartbeat_watchdog
from robo_claw_agent.agent_node.heartbeat_watchdog import (
    HeartbeatWatchdog,
    is_heartbeat_stale,
)

pytestmark = pytest.mark.unit


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_is_heartbeat_stale_ignores_missing_or_disabled():
    """하트비트가 없거나 감시가 꺼져 있으면 stale 로 보지 않는다."""
    assert is_heartbeat_stale(None, 1000.0, 30.0) is False
    assert is_heartbeat_stale(900.0, 2000.0, 0.0) is False
    assert is_heartbeat_stale(900.0, 2000.0, -1.0) is False


def test_is_heartbeat_stale_boundary():
    """제한 시간과 같거나 넘으면 stale 이다."""
    assert is_heartbeat_stale(1000.0, 1029.9, 30.0) is False
    assert is_heartbeat_stale(1000.0, 1030.0, 30.0) is True
    assert is_heartbeat_stale(1000.0, 1100.0, 30.0) is True


def test_detects_stall_and_escalates_after_recovery_attempt():
    """멈춤 감지 → 복구 1회 → 여전히 멈춤이면 재기동 콜백을 호출한다."""
    timeout_called = threading.Event()
    clock = _FakeClock()
    recovered = []

    watchdog = HeartbeatWatchdog(
        timeout_sec=30.0,
        check_interval_sec=0.01,
        clock=clock,
        recover=lambda: recovered.append(clock.now),
        on_timeout=timeout_called.set,
    )
    watchdog.start()

    clock.now += 31.0  # 첫 점검: 멈춤 감지 → 복구 시도
    deadline = time.monotonic() + 2.0
    while not recovered and time.monotonic() < deadline:
        time.sleep(0.01)
    assert recovered, "멈춤 감지 시 복구가 시도되어야 한다"
    assert watchdog.recovery_attempted is True

    clock.now += 31.0  # 두 번째 점검: 복구 후에도 멈춤 → 재기동
    assert timeout_called.wait(timeout=2.0) is True
    watchdog.stop()


def test_no_escalation_when_heartbeat_resumes_after_recovery():
    """복구 후 하트비트가 돌아오면 재기동하지 않는다."""
    timeout_called = threading.Event()
    clock = _FakeClock()
    recovered = []

    watchdog = HeartbeatWatchdog(
        timeout_sec=30.0,
        check_interval_sec=0.01,
        clock=clock,
        recover=lambda: recovered.append(1),
        on_timeout=timeout_called.set,
    )
    watchdog.start()

    clock.now += 31.0
    deadline = time.monotonic() + 2.0
    while not recovered and time.monotonic() < deadline:
        time.sleep(0.01)
    assert recovered

    watchdog.beat()  # 복구 성공(하트비트 재개)
    clock.now += 1.0
    time.sleep(0.1)

    assert timeout_called.is_set() is False
    watchdog.stop()


def test_watchdog_disabled_when_timeout_is_zero():
    """timeout_sec 0 이면 스레드를 띄우지 않는다."""
    watchdog = HeartbeatWatchdog(timeout_sec=0.0, on_timeout=lambda: None)
    watchdog.start()

    assert watchdog.enabled is False
    watchdog.stop()


def test_stop_prevents_timeout_callback():
    """stop() 이후에는 재기동 콜백이 호출되지 않는다."""
    timeout_called = threading.Event()
    clock = _FakeClock()
    watchdog = HeartbeatWatchdog(
        timeout_sec=30.0,
        check_interval_sec=0.01,
        clock=clock,
        on_timeout=timeout_called.set,
    )
    watchdog.start()
    watchdog.stop()

    clock.now += 300.0
    time.sleep(0.1)

    assert timeout_called.is_set() is False


class _SimTimeNode:
    """use_sim_time 파라미터와 ROS 시계만 흉내 내는 노드 대역."""

    def __init__(self, use_sim_time: bool | None, now_ns: int = 0) -> None:
        self._value = use_sim_time
        self._now_ns = now_ns

    def get_parameter(self, name: str):  # noqa: ANN201
        from types import SimpleNamespace

        if name != "use_sim_time" or self._value is None:
            raise KeyError(name)
        return SimpleNamespace(value=self._value)

    def get_clock(self):  # noqa: ANN201
        from types import SimpleNamespace

        node = self

        class _Clock:
            def now(self):  # noqa: ANN202
                return SimpleNamespace(nanoseconds=node._now_ns)

        return _Clock()


def test_clock_for_node_uses_ros_clock_when_sim_time() -> None:
    """use_sim_time 이면 벽시계가 아니라 ROS(sim) 시계로 잰다."""
    node = _SimTimeNode(use_sim_time=True, now_ns=12_500_000_000)

    clock = heartbeat_watchdog.clock_for_node(node)

    assert clock() == pytest.approx(12.5)


def test_clock_for_node_uses_monotonic_without_sim_time() -> None:
    """실기기(use_sim_time=false)에서는 기존 벽시계 감시를 유지한다."""
    node = _SimTimeNode(use_sim_time=False)

    assert heartbeat_watchdog.clock_for_node(node) is time.monotonic


def test_clock_for_node_defaults_to_monotonic_when_parameter_missing() -> None:
    node = _SimTimeNode(use_sim_time=None)

    assert heartbeat_watchdog.clock_for_node(node) is time.monotonic


def test_sim_clock_watchdog_ignores_wall_clock_slowdown() -> None:
    """sim 시간이 느리게 흘러도 sim 기준 30초가 지나기 전에는 종료하지 않는다."""
    node = _SimTimeNode(use_sim_time=True, now_ns=0)
    terminated: list[str] = []

    watchdog = heartbeat_watchdog.HeartbeatWatchdog(
        timeout_sec=30.0,
        check_interval_sec=0.05,
        clock=heartbeat_watchdog.clock_for_node(node),
        on_timeout=lambda: terminated.append("terminated"),
    )
    watchdog.start()
    try:
        # 벽시계로 0.2초 지나는 동안 sim 시간은 1초만 진행된다.
        node._now_ns = 1_000_000_000
        time.sleep(0.2)
    finally:
        watchdog.stop()

    assert terminated == []
