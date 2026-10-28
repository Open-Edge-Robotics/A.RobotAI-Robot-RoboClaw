"""자리 요청(요청자 측) 스킬 단위 테스트 (ROS2 불필요).

서비스 이름 해석과 응답 종합은 순수 함수라 ROS 없이 검증한다. 스킬 실행은
ROS 호출 부분을 monkeypatch 해서 재시도 상한과 fail-closed 동작만 확인한다.
"""

from types import SimpleNamespace

import pytest
from robo_claw_agent.domain.yield_slot import ACCEPTED, BUSY, DENIED, NOT_NEEDED
from robo_claw_agent.skills.yield_skill import (
    MAX_RETRY_WAIT_SEC,
    PeerYieldOutcome,
    RequestYieldSlotSkill,
    resolve_yield_slot_services,
    summarize_outcomes,
)

pytestmark = pytest.mark.unit

_SERVICE_TYPE = "robo_claw_msgs/srv/YieldSlot"


# --- 서비스 이름 해석 ---------------------------------------------------------


def test_resolves_peer_from_namespace_segment():
    services = [
        ("/Former0047/robo_claw_agent_node/yield_slot", [_SERVICE_TYPE]),
        ("/Stretch3/robo_claw_agent_node/yield_slot", [_SERVICE_TYPE]),
    ]

    resolved = resolve_yield_slot_services(services)

    assert resolved == {
        "Former0047": "/Former0047/robo_claw_agent_node/yield_slot",
        "Stretch3": "/Stretch3/robo_claw_agent_node/yield_slot",
    }


def test_excludes_self_and_other_service_types():
    services = [
        ("/Former0047/robo_claw_agent_node/yield_slot", [_SERVICE_TYPE]),
        ("/Former0047/robo_claw_agent_node/peer_message", ["robo_claw_msgs/srv/PeerMessage"]),
        ("/robo_claw_channel_node/send_message", ["robo_claw_msgs/srv/SendMessage"]),
    ]

    resolved = resolve_yield_slot_services(services, exclude_agent_id="Former0047")

    assert resolved == {}


def test_ignores_services_without_the_yield_suffix():
    services = [("/Former0047/robo_claw_agent_node/other", [_SERVICE_TYPE])]

    assert resolve_yield_slot_services(services) == {}


def test_ignores_names_without_a_namespace():
    services = [("/yield_slot", [_SERVICE_TYPE])]

    assert resolve_yield_slot_services(services) == {}


# --- 응답 종합 ---------------------------------------------------------------


def test_accepted_peer_makes_the_request_successful():
    summary = summarize_outcomes(
        [
            PeerYieldOutcome("Former0047", ACCEPTED, "yield_prepared", yield_spot=(0.0, 3.0)),
            PeerYieldOutcome("Butler", NOT_NEEDED, "not_blocking"),
        ]
    )

    assert summary["success"] is True
    assert summary["reason"] == "peer_yielded"
    assert summary["yielded_peers"] == ["Former0047"]
    assert summary["yield_spots"] == {"Former0047": {"x": 0.0, "y": 3.0}}


def test_all_peers_not_blocking_means_nav_failure_had_another_cause():
    summary = summarize_outcomes(
        [
            PeerYieldOutcome("Former0047", NOT_NEEDED, "not_blocking"),
            PeerYieldOutcome("Butler", NOT_NEEDED, "not_blocking"),
        ]
    )

    assert summary["success"] is False
    assert summary["reason"] == "no_peer_blocking"
    assert summary["blocking_peers"] == []


def test_busy_peer_provides_the_shortest_retry_hint():
    summary = summarize_outcomes(
        [
            PeerYieldOutcome("Former0047", BUSY, "manipulation_in_progress", retry_after_sec=5.0),
            PeerYieldOutcome("Butler", BUSY, "agent_busy", retry_after_sec=3.0),
        ]
    )

    assert summary["success"] is False
    assert summary["reason"] == "peers_busy"
    assert summary["retry_after_sec"] == 3.0
    assert set(summary["blocking_peers"]) == {"Former0047", "Butler"}


def test_refused_peer_has_no_retry_hint():
    summary = summarize_outcomes(
        [PeerYieldOutcome("Former0047", DENIED, "no_safe_spot", retry_after_sec=0.0)]
    )

    assert summary["reason"] == "yield_refused"
    assert summary["retry_after_sec"] == 0.0


def test_no_outcomes_reports_no_peer_available():
    summary = summarize_outcomes([])

    assert summary["success"] is False
    assert summary["reason"] == "no_peer_available"


# --- 스킬 실행 (ROS 호출 대체) -----------------------------------------------


def _skill_with_fake_peer(outcomes_per_call: list[list[PeerYieldOutcome]]):
    """ROS 호출을 대체한 RequestYieldSlotSkill 인스턴스."""
    calls: list[float] = []
    skill = RequestYieldSlotSkill()
    skill.set_node(SimpleNamespace(_agent_id="Stretch3"))

    pending = list(outcomes_per_call)

    def _resolve_services():
        return {"Former0047": "/Former0047/robo_claw_agent_node/yield_slot"}

    def _requester_pose():
        return None

    def _query_peers(peer_names, target, radius, requester_pose):
        calls.append(1.0)
        return pending.pop(0) if pending else []

    def _sleep(wait_sec):
        calls.append(wait_sec)
        return True

    skill._resolve_services = _resolve_services  # type: ignore[method-assign]
    skill._requester_pose = _requester_pose  # type: ignore[method-assign]
    skill._query_peers = _query_peers  # type: ignore[method-assign]
    skill._sleep = _sleep  # type: ignore[method-assign]
    return skill, calls


def test_skill_retries_busy_peers_then_succeeds():
    skill, _ = _skill_with_fake_peer(
        [
            [PeerYieldOutcome("Former0047", BUSY, "agent_busy", retry_after_sec=3.0)],
            [PeerYieldOutcome("Former0047", ACCEPTED, "yield_prepared", yield_spot=(0.0, 3.0))],
        ]
    )

    result = skill.execute({"target_x": 2.0, "target_y": 3.0, "max_attempts": 2})

    assert result["success"] is True
    assert result["yielded_peers"] == ["Former0047"]


def test_skill_stops_after_max_attempts():
    skill, calls = _skill_with_fake_peer(
        [
            [PeerYieldOutcome("Former0047", BUSY, "agent_busy", retry_after_sec=3.0)],
            [PeerYieldOutcome("Former0047", BUSY, "agent_busy", retry_after_sec=3.0)],
            [PeerYieldOutcome("Former0047", ACCEPTED, "yield_prepared")],
        ]
    )

    result = skill.execute({"target_x": 2.0, "target_y": 3.0, "max_attempts": 2})

    assert result["success"] is False
    assert result["reason"] == "peers_busy"
    # 질의 2회 + 대기 1회. 세 번째 응답은 사용되지 않는다.
    assert len(calls) == 3


def test_skill_does_not_wait_when_retry_hint_exceeds_the_cap():
    skill, calls = _skill_with_fake_peer(
        [[PeerYieldOutcome("Former0047", BUSY, "agent_busy", retry_after_sec=60.0)]]
    )

    result = skill.execute({"target_x": 2.0, "target_y": 3.0, "max_attempts": 3})

    assert result["success"] is False
    assert calls == [1.0], "상한을 넘는 재시도 대기는 하지 않아야 합니다"
    assert MAX_RETRY_WAIT_SEC < 60.0


@pytest.mark.parametrize(
    "params",
    [
        {"target_y": 3.0},
        {"target_x": "abc", "target_y": 3.0},
        {"target_x": float("nan"), "target_y": 3.0},
        {"target_x": 1.0, "target_y": 2.0, "radius_m": 0.05},
        {"target_x": 1.0, "target_y": 2.0, "max_attempts": 9},
    ],
)
def test_skill_rejects_invalid_parameters(params):
    skill, calls = _skill_with_fake_peer([])

    result = skill.execute(params)

    assert result["success"] is False
    assert calls == []


def test_skill_fails_closed_when_no_peer_service_exists():
    skill = RequestYieldSlotSkill()
    skill.set_node(SimpleNamespace(_agent_id="Stretch3", get_service_names_and_types=lambda: []))

    result = skill.execute({"target_x": 2.0, "target_y": 3.0})

    assert result["success"] is False
    assert "동료" in result["message"]


# --- gRPC 중계 (서브넷 분리 환경) --------------------------------------------


def _skill_with_grpc_peers(outcomes, *, connected_peers=("Former0047",)):
    """로컬 ROS 서비스가 없는 환경을 흉내내는 스킬 인스턴스."""
    skill = RequestYieldSlotSkill()
    grpc_calls: list[str] = []

    class _Node:
        _agent_id = "Stretch3"
        _call_peer_yield_client = object()
        _list_peers_client = object()

        @staticmethod
        def get_service_names_and_types():
            return []

    skill.set_node(_Node())
    skill._requester_pose = lambda: None  # type: ignore[method-assign]

    def _grpc_peer_names():
        return sorted(connected_peers)

    def _call_peer_via_grpc(peer_name, target, radius, requester_pose):
        grpc_calls.append(peer_name)
        return outcomes.get(peer_name)

    skill._grpc_peer_names = _grpc_peer_names  # type: ignore[method-assign]
    skill._call_peer_via_grpc = _call_peer_via_grpc  # type: ignore[method-assign]
    return skill, grpc_calls


def test_grpc_fallback_is_used_when_no_local_service_exists():
    skill, grpc_calls = _skill_with_grpc_peers(
        {
            "Former0047": PeerYieldOutcome(
                "Former0047", ACCEPTED, "yield_prepared", yield_spot=(0.0, 3.0)
            )
        }
    )

    result = skill.execute({"target_x": 2.0, "target_y": 3.0})

    assert result["success"] is True
    assert result["yielded_peers"] == ["Former0047"]
    assert grpc_calls == ["Former0047"]


def test_grpc_fallback_reports_busy_peer():
    skill, _ = _skill_with_grpc_peers(
        {"Former0047": PeerYieldOutcome("Former0047", BUSY, "agent_busy", retry_after_sec=3.0)}
    )

    result = skill.execute({"target_x": 2.0, "target_y": 3.0, "max_attempts": 1})

    assert result["success"] is False
    assert result["reason"] == "peers_busy"


def test_explicit_peer_name_skips_discovery():
    skill, grpc_calls = _skill_with_grpc_peers(
        {"Target": PeerYieldOutcome("Target", NOT_NEEDED, "not_blocking")}
    )

    result = skill.execute({"target_x": 2.0, "target_y": 3.0, "peer_name": "Target"})

    assert grpc_calls == ["Target"]
    assert result["reason"] == "no_peer_blocking"


def test_no_connected_peers_fails_closed():
    skill = RequestYieldSlotSkill()

    class _Node:
        _agent_id = "Stretch3"

        @staticmethod
        def get_service_names_and_types():
            return []

    skill.set_node(_Node())
    skill._grpc_peer_names = lambda: []  # type: ignore[method-assign]

    result = skill.execute({"target_x": 2.0, "target_y": 3.0})

    assert result["success"] is False
    assert "동료" in result["message"]
