"""자리 양보 수신측 처리 단위 테스트 (ROS2 불필요).

``skills.yield_slot_service`` 의 판정 오케스트레이션을 fake 컨텍스트로 검증한다.
이동 실행·노드 어댑터는 ``YieldServerContext`` 뒤에 있으므로 여기서는 호출되지 않는다.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from robo_claw_agent.domain.yield_slot import (
    ACCEPTED,
    BUSY,
    DENIED,
    NOT_NEEDED,
    Pose2D,
    YieldRequest,
)
from robo_claw_agent.skills.yield_slot_service import (
    YieldServerContext,
    build_server_context,
    decide_yield,
    decision_message,
    fill_yield_response,
)

pytestmark = pytest.mark.unit


# --- 헬퍼 -------------------------------------------------------------------

_TARGET = Pose2D(2.0, 3.0, 0.0)
_DEFAULT_RECEIVER_POSE = Pose2D(2.1, 3.05, 0.0)
_DEFAULT_REQUESTER_POSE = Pose2D(5.0, 5.0, 0.0)
_CANDIDATES = [
    (2.1, 4.2, 1.0),  # 목표에서 1.20m → 제외
    (0.0, 0.0, 0.2),  # 개방도 부족
    (0.0, 3.0, 1.0),  # 유효 (수신측 2.10m)
]


def _ctx(
    *,
    agent_id="Former0047",
    pose=_DEFAULT_RECEIVER_POSE,
    busy=False,
    manipulation=False,
    battery=80.0,
    candidates=None,
    allow_yield=True,
):
    return YieldServerContext(
        agent_id=agent_id,
        pose_provider=lambda: pose,
        busy_provider=lambda: busy,
        manipulation_provider=lambda: manipulation,
        battery_provider=lambda: battery,
        candidates_provider=lambda: _CANDIDATES if candidates is None else candidates,
        allow_yield=allow_yield,
    )


def _req(*, requester_id="Stretch3", requester_pose=None, radius=0.8):
    return YieldRequest(
        requester_id=requester_id,
        target=_TARGET,
        radius_m=radius,
        timeout_sec=5.0,
        requester_pose=requester_pose if requester_pose is not None else _DEFAULT_REQUESTER_POSE,
    )


# --- 판정 오케스트레이션 -----------------------------------------------------


def test_occupant_accepts_and_gets_a_concrete_stand_aside_spot():
    decision = decide_yield(_ctx(), _req())

    assert decision.decision == ACCEPTED
    assert decision.yield_spot is not None
    assert (round(decision.yield_spot.x, 2), round(decision.yield_spot.y, 2)) == (0.0, 3.0)


def test_pose_unavailable_denies_without_claiming_anything():
    decision = decide_yield(_ctx(pose=None), _req())

    assert decision.decision == DENIED
    assert decision.reason == "pose_unknown"
    assert decision.yield_spot is None


def test_candidates_are_not_scanned_when_not_blocking():
    calls: list[int] = []

    ctx = YieldServerContext(
        agent_id="Former0047",
        pose_provider=lambda: Pose2D(9.0, 9.0, 0.0),
        busy_provider=lambda: False,
        manipulation_provider=lambda: False,
        battery_provider=lambda: None,
        candidates_provider=lambda: calls.append(1) or _CANDIDATES,
    )

    decision = decide_yield(ctx, _req())

    assert decision.decision == NOT_NEEDED
    assert calls == [], "지도 탐색은 수락 가능할 때만 수행해야 합니다"


def test_manipulation_blocks_yield():
    decision = decide_yield(_ctx(manipulation=True), _req())

    assert decision.decision == BUSY
    assert decision.reason == "manipulation_in_progress"


def test_busy_agent_is_not_promised_a_spot():
    decision = decide_yield(_ctx(busy=True), _req())

    assert decision.decision == BUSY
    assert decision.reason == "agent_busy"
    assert decision.yield_spot is None


def test_missing_candidates_denies_instead_of_guessing():
    decision = decide_yield(_ctx(candidates=[]), _req())

    assert decision.decision == DENIED
    assert decision.reason == "no_safe_spot"


def test_disabled_yield_is_denied_before_pose_lookup():
    decision = decide_yield(_ctx(allow_yield=False, pose=None), _req())

    assert decision.decision == DENIED
    assert decision.reason == "yield_disabled"


def test_invalid_request_is_denied_before_negotiation():
    decision = decide_yield(_ctx(), _req(radius=9.0))

    assert decision.decision == DENIED
    assert decision.reason == "invalid_request"


# --- 응답 매핑 ---------------------------------------------------------------


def test_response_carries_decision_and_spot():
    decision = decide_yield(_ctx(), _req())
    response = SimpleNamespace()

    fill_yield_response(response, decision, message="ok")

    assert response.success is True
    assert response.decision == ACCEPTED
    assert response.has_yield_spot is True
    assert (round(response.yield_spot_x, 2), round(response.yield_spot_y, 2)) == (0.0, 3.0)
    assert response.message == "ok"


def test_response_uses_sentinel_distance_when_unknown():
    response = SimpleNamespace()

    fill_yield_response(response, decide_yield(_ctx(pose=None), _req()), message="")

    assert response.decision == DENIED
    assert response.distance_m == -1.0
    assert response.has_yield_spot is False


def test_response_retry_hint_is_preserved():
    response = SimpleNamespace()

    fill_yield_response(response, decide_yield(_ctx(busy=True), _req()), message="")

    assert response.retry_after_sec > 0.0


# --- 메시지 ------------------------------------------------------------------


def test_message_explains_accepted_move():
    decision = decide_yield(_ctx(), _req())

    message = decision_message(decision, requester_id="Stretch3", moved=True)

    assert "Stretch3" in message
    assert "비켜" in message


def test_message_explains_busy_retry():
    decision = decide_yield(_ctx(busy=True), _req())

    message = decision_message(decision, requester_id="Stretch3", moved=False)

    assert "초 후" in message


# --- 컨텍스트 조립 -----------------------------------------------------------


def test_context_is_lazy_and_does_not_touch_ros_on_creation():
    node = MagicMock()

    ctx = build_server_context(node, agent_id="Former0047", allow_yield=True)

    assert ctx.agent_id == "Former0047"
    node.get_node_names.assert_not_called()


def test_busy_provider_falls_back_to_active_executions():
    node = SimpleNamespace(
        _task_queue=None,
        _skills=SimpleNamespace(active_executions=lambda: [{"skill_name": "tidy_home"}]),
    )

    ctx = build_server_context(node, agent_id="Former0047", allow_yield=True)

    assert ctx.busy_provider() is True


def test_manipulation_detection_uses_exclusive_resources():
    running = SimpleNamespace(exclusive_resources=("cloid_motion",))
    node = SimpleNamespace(
        _task_queue=None,
        _skills=SimpleNamespace(
            active_executions=lambda: [{"skill_name": "execute_cloid_motion"}],
            get_skill=lambda name: running,
        ),
    )

    ctx = build_server_context(node, agent_id="Former0047", allow_yield=True)

    assert ctx.manipulation_provider() is True


def test_background_loop_marks_agent_busy(monkeypatch):
    """자율 루프는 태스크 큐를 거치지 않으므로 큐가 비어 있어도 양보 이동 불가."""
    from robo_claw_agent.skills import yield_slot_service

    monkeypatch.setattr(yield_slot_service, "_background_loop_active", lambda: True)
    node = SimpleNamespace(_task_queue=SimpleNamespace(is_idle=lambda: True), _skills=None)

    ctx = build_server_context(node, agent_id="Former0047", allow_yield=True)

    assert ctx.busy_provider() is True


def test_idle_queue_without_background_loop_is_not_busy(monkeypatch):
    from robo_claw_agent.skills import yield_slot_service

    monkeypatch.setattr(yield_slot_service, "_background_loop_active", lambda: False)
    node = SimpleNamespace(_task_queue=SimpleNamespace(is_idle=lambda: True), _skills=None)

    ctx = build_server_context(node, agent_id="Former0047", allow_yield=True)

    assert ctx.busy_provider() is False


# --- 장소 이름 기반 판정 (맵 미공유) -----------------------------------------


def _place_ctx(**kwargs):
    resolver = kwargs.pop("resolver", lambda place: Pose2D(2.0, 3.0, 0.0))
    base = _ctx(**kwargs)
    return YieldServerContext(
        agent_id=base.agent_id,
        pose_provider=base.pose_provider,
        busy_provider=base.busy_provider,
        manipulation_provider=base.manipulation_provider,
        battery_provider=base.battery_provider,
        candidates_provider=base.candidates_provider,
        allow_yield=base.allow_yield,
        place_resolver=resolver,
    )


def _place_req(*, place="안방", requester_at_target=False):
    return YieldRequest(
        requester_id="Stretch3",
        target_place=place,
        requester_at_target=requester_at_target,
    )


def test_place_is_resolved_in_the_receiver_map_then_judged():
    decision = decide_yield(_place_ctx(), _place_req())

    assert decision.decision == ACCEPTED
    assert decision.yield_spot is not None


def test_unknown_place_is_denied_without_guessing():
    decision = decide_yield(_place_ctx(resolver=lambda place: None), _place_req())

    assert decision.decision == DENIED
    assert decision.reason == "unknown_place"
    assert decision.yield_spot is None


def test_place_resolver_failure_is_denied():
    def _boom(place):
        raise RuntimeError("semantic map unavailable")

    decision = decide_yield(_place_ctx(resolver=_boom), _place_req())

    assert decision.decision == DENIED
    assert decision.reason == "unknown_place"


def test_missing_resolver_denies_place_request():
    decision = decide_yield(_ctx(), _place_req())

    assert decision.decision == DENIED
    assert decision.reason == "unknown_place"


def test_place_request_without_safe_spot_denies():
    decision = decide_yield(_place_ctx(candidates=[]), _place_req())

    assert decision.decision == DENIED
    assert decision.reason == "no_safe_spot"


def test_place_request_from_a_contested_requester_uses_arbitration():
    decision = decide_yield(
        _place_ctx(), _place_req(requester_at_target=True)
    )

    assert decision.decision == BUSY
    assert decision.reason == "requester_lower_priority"


def test_coordinate_request_still_skips_place_resolution():
    calls: list[str] = []

    def _resolver(place):
        calls.append(place)
        return Pose2D(0.0, 0.0, 0.0)

    decision = decide_yield(_place_ctx(resolver=_resolver), _req())

    assert decision.decision == ACCEPTED
    assert calls == [], "좌표 요청에서는 장소 해석을 호출하지 않아야 합니다"
