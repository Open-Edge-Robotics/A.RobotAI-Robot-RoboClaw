"""
ChannelNode HTTP 핸들러 단위 테스트 (ROS2 없이 HTTP 로직만 검증)
"""

import json
from io import BytesIO
from unittest.mock import MagicMock, patch


class MockSocket:
    """테스트용 소켓 모의 객체"""

    def __init__(self, data: bytes):
        self._data = BytesIO(data)
        self.output = BytesIO()

    def makefile(self, mode: str, *args, **kwargs):
        if "r" in mode:
            return self._data
        return self.output


def make_request_bytes(method: str, path: str, body: dict | None = None) -> bytes:
    """HTTP 요청 바이트 생성 헬퍼"""
    body_bytes = json.dumps(body or {}).encode("utf-8") if body else b""
    lines = [
        f"{method} {path} HTTP/1.1",
        "Host: localhost",
        "Content-Type: application/json",
        f"Content-Length: {len(body_bytes)}",
        "",
        body_bytes.decode("utf-8") if body_bytes else "",
    ]
    return "\r\n".join(lines).encode("utf-8")


class TestChannelNodeHTTP:
    """HTTP 핸들러 직접 테스트"""

    def _make_handler_class(self, node_mock):
        """채널 노드를 모킹한 핸들러 클래스 생성"""
        from robo_claw_channel.channel_node import ChannelNode

        # ChannelNode._make_handler를 우회해 핸들러 클래스만 가져옴
        with patch.object(ChannelNode, "__init__", lambda self: None):
            node = ChannelNode.__new__(ChannelNode)
            node.send_task = node_mock.send_task
            node.call_skill = node_mock.call_skill
            node.get_status = node_mock.get_status
            # 실제 _make_handler 호출 (노드는 모킹됨)
            ChannelNode._make_handler.__func__(node)  # type: ignore
            return node._make_handler()

    def test_imports(self):
        """패키지 임포트 검증"""
        from robo_claw_channel.channel_node import ChannelNode, main

        assert ChannelNode is not None
        assert main is not None


class TestChannelNodeUnit:
    """ChannelNode 내부 메서드 단위 검증"""

    def setup_method(self):
        """ROS2 초기화 없이 노드 속성만 모킹"""
        from robo_claw_channel.channel_node import ChannelNode

        with patch.object(ChannelNode, "__init__", return_value=None):
            self.node = ChannelNode.__new__(ChannelNode)
            self.node._timeout = 5.0
            self.node._agent_ns = "/robo_claw_agent_node"
            self.node._task_client = MagicMock()
            self.node._skill_client = MagicMock()
            self.node._query_client = MagicMock()

    def test_send_task_no_server(self):
        """에이전트 서버 없을 때 오류 반환"""
        self.node._task_client.wait_for_server.return_value = False  # type: ignore
        result = self.node.send_task("앞으로 이동")
        assert result["success"] is False
        assert "연결 실패" in result["error"]

    def test_call_skill_no_service(self):
        """서비스 없을 때 오류 반환"""
        self.node._skill_client.wait_for_service.return_value = False  # type: ignore
        result = self.node.call_skill("navigate", "{}")
        assert result["success"] is False

    def test_get_status_no_service(self):
        """상태 조회 서비스 없을 때 오류 반환"""
        self.node._query_client.wait_for_service.return_value = False  # type: ignore
        result = self.node.get_status()
        assert result["success"] is False


def test_security_policy_allows_public_health_without_auth():
    from robo_claw_channel.channel_node import HttpSecurityPolicy

    policy = HttpSecurityPolicy(
        readonly_token="",
        control_token="",
        allowed_cidrs=["127.0.0.1/32"],
        rate_limit_per_minute=10,
        allowed_skills=[],
        blocked_skills=[],
    )

    result = policy.authorize(
        client_ip="127.0.0.1",
        method="GET",
        path="/health",
        headers={},
    )

    assert result.allowed is True
    assert result.role == "control"


def test_security_policy_requires_control_token_for_task():
    from robo_claw_channel.channel_node import HttpSecurityPolicy

    policy = HttpSecurityPolicy(
        readonly_token="read-token",
        control_token="write-token",
        allowed_cidrs=["127.0.0.1/32"],
        rate_limit_per_minute=10,
        allowed_skills=[],
        blocked_skills=[],
    )

    denied = policy.authorize(
        client_ip="127.0.0.1",
        method="POST",
        path="/task",
        headers={"Authorization": "Bearer read-token"},
    )
    allowed = policy.authorize(
        client_ip="127.0.0.1",
        method="POST",
        path="/task",
        headers={"Authorization": "Bearer write-token"},
    )

    assert denied.allowed is False
    assert denied.status_code == 401
    assert allowed.allowed is True
    assert allowed.role == "control"


def test_security_policy_blocks_disallowed_ip():
    from robo_claw_channel.channel_node import HttpSecurityPolicy

    policy = HttpSecurityPolicy(
        readonly_token="",
        control_token="",
        allowed_cidrs=["127.0.0.1/32"],
        rate_limit_per_minute=10,
        allowed_skills=[],
        blocked_skills=[],
    )

    result = policy.authorize(
        client_ip="10.0.0.8",
        method="GET",
        path="/health",
        headers={},
    )

    assert result.allowed is False
    assert result.status_code == 403


def test_security_policy_enforces_rate_limit():
    from robo_claw_channel.channel_node import HttpSecurityPolicy

    policy = HttpSecurityPolicy(
        readonly_token="",
        control_token="",
        allowed_cidrs=["127.0.0.1/32"],
        rate_limit_per_minute=1,
        allowed_skills=[],
        blocked_skills=[],
    )

    first = policy.authorize(
        client_ip="127.0.0.1",
        method="GET",
        path="/health",
        headers={},
    )
    second = policy.authorize(
        client_ip="127.0.0.1",
        method="GET",
        path="/health",
        headers={},
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.status_code == 429


def test_security_policy_blocks_restricted_skill():
    from robo_claw_channel.channel_node import HttpSecurityPolicy

    policy = HttpSecurityPolicy(
        readonly_token="",
        control_token="",
        allowed_cidrs=["127.0.0.1/32"],
        rate_limit_per_minute=10,
        allowed_skills=["get_status"],
        blocked_skills=["ros_command"],
    )

    assert policy.is_skill_allowed("get_status") is None
    assert "allowlist" in str(policy.is_skill_allowed("navigate"))
    assert "위험 스킬" in str(policy.is_skill_allowed("ros_command"))
