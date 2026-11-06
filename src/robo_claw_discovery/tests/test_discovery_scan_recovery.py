"""스캔 스레드 예외 복구 회귀 테스트.

도메인에 다른 RMW 구현(Fast DDS 등)의 participant 가 섞이면
``get_node_names()`` 가 RCLError 를 던진다. 이때 스캔 스레드가 죽어
capabilities 퍼블리시가 영구히 멈추면 안 된다.
"""

import json

import pytest

pytestmark = pytest.mark.ros2

rclpy = pytest.importorskip("rclpy")


from robo_claw_discovery.discovery_node import DiscoveryNode  # noqa: E402

EMPTY_NODE_NAME_ERROR = (
    "Failed to get node names: empty node name returned by the RMW layer, at ./src/rcl/graph.c:360"
)


class _MessageRecorder:
    """publisher 를 대체해 퍼블리시된 payload 를 모은다."""

    def __init__(self) -> None:
        self.payloads: list[str] = []

    def publish(self, msg) -> None:  # noqa: ANN001 - std_msgs/String
        self.payloads.append(msg.data)


@pytest.fixture
def rclpy_context():
    rclpy.init()
    try:
        yield
    finally:
        rclpy.shutdown()


def test_scan_publishes_partial_capabilities_when_node_names_fail(rclpy_context):
    """노드 이름 조회가 계속 실패해도 스캔은 살아남고 부분 결과를 퍼블리시한다."""
    node = DiscoveryNode(scan_interval_sec=1.0)
    node._scan_thread.join(timeout=10.0)  # __init__ 의 최초 스캔 완료 대기
    recorder = _MessageRecorder()
    node._pub = recorder

    def fail_get_node_names():
        raise RuntimeError(EMPTY_NODE_NAME_ERROR)

    node.get_node_names = fail_get_node_names

    try:
        node._scan()
        node._scan_thread.join(timeout=10.0)

        assert node._scan_thread.is_alive() is False, "스캔 스레드가 예외로 종료됨"
        assert recorder.payloads, "실패 시에도 capabilities 퍼블리시가 있어야 한다"

        capabilities = json.loads(recorder.payloads[-1])
        assert capabilities["nodes"] == []
        assert capabilities["agents"] == []
        for key in ("topics", "services", "actions"):
            assert key in capabilities
    finally:
        node.destroy_node()


def test_scan_keeps_running_after_consecutive_failures(rclpy_context):
    """연속 실패에도 다음 스캔이 정상 수행되어 퍼블리시가 이어진다."""
    node = DiscoveryNode(scan_interval_sec=1.0)
    node._scan_thread.join(timeout=10.0)  # __init__ 의 최초 스캔 완료 대기
    recorder = _MessageRecorder()
    node._pub = recorder

    calls = {"count": 0}

    def flaky_get_node_names():
        calls["count"] += 1
        raise RuntimeError(EMPTY_NODE_NAME_ERROR)

    node.get_node_names = flaky_get_node_names

    try:
        for _ in range(3):
            node._scan()
            node._scan_thread.join(timeout=10.0)

        assert len(recorder.payloads) == 3
        assert all(json.loads(payload)["nodes"] == [] for payload in recorder.payloads)
        assert calls["count"] >= 3  # 재시도가 일어나도 스캔 횟수는 유지된다
    finally:
        node.destroy_node()
