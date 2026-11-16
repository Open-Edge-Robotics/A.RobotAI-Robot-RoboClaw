"""동료 메시지 인증과 peer별 스킬 스코프 회귀 테스트 (ROS2 불필요).

`PeerMessage` ROS 서비스는 지금까지 인증이 없어, 같은 DDS 도메인의 다른 노드가
임의의 `peer_name` 을 신고하고 협동 인박스에 메시지를 넣을 수 있었습니다. gRPC
계층에는 peer 토큰 검증이 있었지만 ROS 서비스 경로는 그 검증을 우회합니다.

이 파일은 (1) 채널-에이전트 간 공유 토큰 검증, (2) peer별 스킬 스코프가 실제로
강제되는지 검증합니다.
"""

from __future__ import annotations

import pytest
from robo_claw_agent.skills.cooperation_skill.peer_auth import (
    effective_peer_skill_allowlist,
    parse_peer_skill_scope,
    verify_peer_message_token,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 토큰 검증
# ---------------------------------------------------------------------------


def test_matching_common_token_is_accepted():
    ok, reason = verify_peer_message_token(
        expected_common="shared-secret",
        tokens_by_peer={},
        peer_name="robot_b",
        supplied="shared-secret",
    )

    assert ok is True
    assert reason == ""


def test_mismatched_token_is_rejected():
    ok, reason = verify_peer_message_token(
        expected_common="shared-secret",
        tokens_by_peer={},
        peer_name="robot_b",
        supplied="wrong-secret",
    )

    assert ok is False
    assert reason == "peer_token_mismatch"


def test_missing_token_is_rejected_when_expected():
    ok, reason = verify_peer_message_token(
        expected_common="shared-secret",
        tokens_by_peer={},
        peer_name="robot_b",
        supplied="",
    )

    assert ok is False
    assert reason == "peer_token_missing"


def test_per_peer_token_overrides_common_token():
    ok, reason = verify_peer_message_token(
        expected_common="shared-secret",
        tokens_by_peer={"robot_b": "robot-b-secret"},
        peer_name="robot_b",
        supplied="shared-secret",
    )

    assert ok is False
    assert reason == "peer_token_mismatch"

    ok, reason = verify_peer_message_token(
        expected_common="shared-secret",
        tokens_by_peer={"robot_b": "robot-b-secret"},
        peer_name="robot_b",
        supplied="robot-b-secret",
    )
    assert ok is True


def test_peer_token_lookup_is_case_insensitive():
    ok, _ = verify_peer_message_token(
        expected_common="",
        tokens_by_peer={"Robot-B": "robot-b-secret"},
        peer_name="robot-b",
        supplied="robot-b-secret",
    )

    assert ok is True


def test_unconfigured_token_is_not_enforced():
    ok, reason = verify_peer_message_token(
        expected_common="",
        tokens_by_peer={},
        peer_name="robot_b",
        supplied="",
    )

    assert ok is True
    assert reason == "peer_token_not_configured"


def test_configured_token_rejects_unknown_peer():
    """토큰 맵에 없는 peer 는 공통 토큰이 없으면 거부한다."""
    ok, reason = verify_peer_message_token(
        expected_common="",
        tokens_by_peer={"robot_b": "robot-b-secret"},
        peer_name="robot_c",
        supplied="robot-b-secret",
    )

    assert ok is False
    assert reason == "peer_token_mismatch"


# ---------------------------------------------------------------------------
# peer별 스킬 스코프
# ---------------------------------------------------------------------------


def test_parse_peer_skill_scope_rejects_invalid_json():
    scope, error = parse_peer_skill_scope("{not json")

    assert scope == {}
    assert error != ""


def test_parse_peer_skill_scope_normalizes_names():
    scope, error = parse_peer_skill_scope('{"Robot-B": ["navigate_to", "say", ""]}')

    assert error == ""
    assert scope == {"robot-b": ["navigate_to", "say"]}


def test_parse_peer_skill_scope_rejects_non_list():
    scope, error = parse_peer_skill_scope('{"robot-b": "navigate_to"}')

    assert scope == {}
    assert error != ""


def test_peer_scope_intersects_global_allowlist():
    """peer 스코프는 전역 allowlist 를 넓힐 수 없고 좁히기만 한다."""
    effective = effective_peer_skill_allowlist(
        global_allowlist={"navigate_to", "say", "adaptive_pick_object"},
        peer_scope={"robot-b": ["say", "delete_file"]},
        peer_name="robot-b",
    )

    assert effective == {"say"}


def test_peer_without_scope_keeps_global_allowlist():
    effective = effective_peer_skill_allowlist(
        global_allowlist={"navigate_to", "say"},
        peer_scope={"robot-b": ["say"]},
        peer_name="robot-c",
    )

    assert effective == {"navigate_to", "say"}


def test_peer_scope_lookup_is_case_insensitive():
    effective = effective_peer_skill_allowlist(
        global_allowlist={"navigate_to", "say"},
        peer_scope={"robot-b": ["say"]},
        peer_name="Robot-B",
    )

    assert effective == {"say"}


def test_empty_scope_entry_blocks_every_skill():
    """스코프를 빈 목록으로 두면 그 peer 는 아무 스킬도 위임할 수 없다."""
    effective = effective_peer_skill_allowlist(
        global_allowlist={"navigate_to", "say"},
        peer_scope={"robot-b": []},
        peer_name="robot-b",
    )

    assert effective == set()
