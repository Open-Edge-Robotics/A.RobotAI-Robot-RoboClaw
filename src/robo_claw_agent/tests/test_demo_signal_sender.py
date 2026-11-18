"""데모 cue 전송 어댑터 테스트 — loopback 소켓으로 실제 바이트를 확인한다."""

import socket
import threading
from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.demo_signal import sender as cue_sender
from robo_claw_agent.skills.demo_signal.protocol import build_config

pytestmark = pytest.mark.unit


def _config(host, port, *, protocol="tcp", payload="shoe pick", timeout=1.0):
    config, reason = build_config(
        enabled=True,
        protocol=protocol,
        host=host,
        port=port,
        payload=payload,
        terminator="none",
        timeout_sec=timeout,
    )
    assert reason == ""
    assert config is not None
    return config


class _TcpReceiver:
    """단일 연결을 받아 바이트를 보관하는 loopback TCP 리스너."""

    def __init__(self, expected=1):
        self.expected = expected
        self.received: list[bytes] = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def _run(self):
        for _ in range(self.expected):
            try:
                self.sock.settimeout(3.0)
                conn, _addr = self.sock.accept()
            except Exception:
                return
            with conn:
                conn.settimeout(3.0)
                try:
                    self.received.append(conn.recv(4096))
                except Exception:
                    self.received.append(b"")

    def __exit__(self, *_exc):
        # 수신 스레드가 accept/recv 를 마칠 때까지 기다린 뒤 리스너를 닫는다.
        self._thread.join(timeout=3.0)
        try:
            self.sock.close()
        except Exception:
            pass


def test_tcp_sends_exact_utf8_bytes_without_terminator():
    with _TcpReceiver() as receiver:
        outcome = cue_sender.send_cue(_config("127.0.0.1", receiver.port))

    assert outcome["status"] == "sent"
    assert outcome["bytes"] == len(b"shoe pick")
    assert receiver.received == [b"shoe pick"]


def test_udp_sends_configured_payload():
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("127.0.0.1", 0))
    server.settimeout(3.0)
    try:
        outcome = cue_sender.send_cue(_config("127.0.0.1", server.getsockname()[1], protocol="udp"))
        data, _addr = server.recvfrom(4096)
    finally:
        server.close()

    assert outcome["status"] == "sent"
    assert data == b"shoe pick"


def test_connection_refused_is_reported_not_raised():
    closed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    closed.bind(("127.0.0.1", 0))
    port = closed.getsockname()[1]
    closed.close()

    outcome = cue_sender.send_cue(_config("127.0.0.1", port))

    assert outcome["status"] == "failed"
    assert outcome["reason"] == "send_failed"
    assert outcome["error"]
    assert "payload" not in outcome


def test_socket_timeout_is_reported_as_failure():
    class _TimeoutSocket:
        def settimeout(self, _value):
            return None

        def connect(self, _address):
            raise TimeoutError("timed out")

        def close(self):
            return None

    outcome = cue_sender.send_cue(
        _config("127.0.0.1", 5005), socket_factory=lambda *_args: _TimeoutSocket()
    )

    assert outcome["status"] == "failed"
    assert outcome["error"] == "TimeoutError" or outcome["error"] == "timeout"


def test_node_parameters_are_honored_and_missing_parameters_disable_cue():
    seen = {}

    class _Socket:
        def settimeout(self, value):
            seen["timeout"] = value

        def connect(self, address):
            seen["address"] = address

        def sendall(self, data):
            seen["data"] = data

        def close(self):
            return None

    params = {
        "cloid_demo_signal_enabled": True,
        "cloid_demo_signal_protocol": "tcp",
        "cloid_demo_signal_host": "192.168.50.86",
        "cloid_demo_signal_port": 5005,
        "cloid_demo_signal_payload": "shoe pick",
        "cloid_demo_signal_terminator": "none",
        "cloid_demo_signal_timeout_sec": 0.5,
    }
    node = SimpleNamespace(get_parameter=lambda name: SimpleNamespace(value=params[name]))

    outcome = cue_sender.send_demo_cue(node, socket_factory=lambda *_args: _Socket())

    assert outcome["status"] == "sent"
    assert seen["address"] == ("192.168.50.86", 5005)
    assert seen["data"] == b"shoe pick"
    assert seen["timeout"] == 0.5

    def _missing(_name):
        raise KeyError("undeclared")

    skipped = cue_sender.send_demo_cue(SimpleNamespace(get_parameter=_missing))
    assert skipped["status"] == "skipped"
    assert skipped["reason"] == "disabled"
