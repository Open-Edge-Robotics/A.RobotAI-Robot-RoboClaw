"""동료 메시지 토큰이 기존 gRPC peer 토큰을 재사용하는지 검증한다.

토큰을 여러 개로 나누면 다른 팀과 협업할 때 배포·회전 관리가 어려워진다. 그래서
채널-에이전트 간 PeerMessage 인증은 기존 `GRPC_PEER_TOKEN` 값을 그대로 쓰고,
ROS 파라미터(`peer_message_token`)로만 덮어쓸 수 있게 유지한다.
"""

from __future__ import annotations

import pytest
from robo_claw_agent.agent_node.params import declare_agent_parameters

pytestmark = pytest.mark.unit


class _RecordingNode:
    """`declare_parameter` 호출만 기록하는 최소 노드."""

    def __init__(self) -> None:
        self.declared: dict[str, object] = {}

    def declare_parameter(self, name, default=None):
        self.declared[name] = default
        return None


def _declared_defaults(monkeypatch, env: dict[str, str]) -> dict[str, object]:
    for key in ("GRPC_PEER_TOKEN", "RC_PEER_MESSAGE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    node = _RecordingNode()
    declare_agent_parameters(node)
    return node.declared


def test_peer_message_token_reuses_grpc_peer_token(monkeypatch):
    declared = _declared_defaults(monkeypatch, {"GRPC_PEER_TOKEN": "shared-token"})

    assert declared["peer_message_token"] == "shared-token"


def test_peer_message_token_is_empty_without_grpc_peer_token(monkeypatch):
    declared = _declared_defaults(monkeypatch, {})

    assert declared["peer_message_token"] == ""


def test_no_separate_peer_message_token_env_is_introduced(monkeypatch):
    """토큰을 늘리지 않는다 — 별도 env 값은 무시된다."""
    declared = _declared_defaults(
        monkeypatch,
        {"GRPC_PEER_TOKEN": "shared-token", "RC_PEER_MESSAGE_TOKEN": "another-token"},
    )

    assert declared["peer_message_token"] == "shared-token"
