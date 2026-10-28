"""자리 양보 협상 도메인 규칙 단위 테스트 (ROS2 불필요).

``domain.yield_slot`` 은 순수 계산만 수행한다. 결정적 중재(두 로봇이 동시에
요청해도 정확히 한쪽만 양보)와 fail-closed 판정이 핵심 계약이다.
"""

import math

import pytest
from robo_claw_agent.domain.yield_slot import (
    ACCEPTED,
    BUSY,
    DENIED,
    NOT_NEEDED,
    RECEIVER_WINS,
    REQUESTER_WINS,
    Pose2D,
    YieldReceiverState,
    YieldRequest,
    arbitration_outcome,
    evaluate_yield_request,
    plan_yield,
    select_yield_spot,
    validate_yield_request,
)

pytestmark = pytest.mark.unit


# --- 헬퍼 -------------------------------------------------------------------


def _request(
    *,
    requester_id: str = "Stretch3",
    target: Pose2D | None = None,
    radius_m: float = 0.8,
    timeout_sec: float = 5.0,
    requester_pose: Pose2D | None = None,
) -> YieldRequest:
    return YieldRequest(
        requester_id=requester_id,
        target=target or Pose2D(2.0, 3.0, 0.0),
        radius_m=radius_m,
        timeout_sec=timeout_sec,
        # 기본 요청자는 아직 목표에 도착하지 않은 상태(단독 점유 상황).
        requester_pose=requester_pose if requester_pose is not None else Pose2D(5.0, 5.0, 0.0),
    )


def _state(
    *,
    agent_id: str = "Former0047",
    pose: Pose2D | None = None,
    manipulation_active: bool = False,
    allow_yield: bool = True,
    battery_percent: float | None = 80.0,
) -> YieldReceiverState:
    return YieldReceiverState(
        agent_id=agent_id,
        pose=pose if pose is not None else Pose2D(2.1, 3.05, 0.0),
        manipulation_active=manipulation_active,
        allow_yield=allow_yield,
        battery_percent=battery_percent,
    )


# --- 요청 검증 ---------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"radius_m": 0.0},
        {"radius_m": -0.5},
        {"radius_m": float("nan")},
        {"radius_m": 5.0},
        {"timeout_sec": 0.0},
        {"timeout_sec": -1.0},
        {"timeout_sec": float("inf")},
        {"timeout_sec": 600.0},
    ],
)
def test_invalid_numeric_parameters_are_rejected(override):
    request = _request(**override)

    message = validate_yield_request(request)

    assert message, f"{override} 는 거부되어야 합니다"


def test_non_finite_target_is_rejected():
    request = _request(target=Pose2D(float("nan"), 1.0, 0.0))

    assert validate_yield_request(request)


def test_empty_requester_id_is_rejected():
    request = _request(requester_id="  ")

    assert validate_yield_request(request)


def test_valid_request_passes_validation():
    assert validate_yield_request(_request()) == ""


# --- 수신측 판정 -------------------------------------------------------------


def test_not_blocking_when_receiver_is_far_from_target():
    decision = evaluate_yield_request(_request(), _state(pose=Pose2D(6.0, 9.0, 0.0)))

    assert decision.decision == NOT_NEEDED
    assert decision.reason == "not_blocking"
    assert decision.distance_m == pytest.approx(math.dist((6.0, 9.0), (2.0, 3.0)))


def test_blocking_idle_receiver_accepts():
    decision = evaluate_yield_request(_request(), _state())

    assert decision.decision == ACCEPTED
    assert decision.accepted is True


def test_disabled_yield_is_denied_without_distance_claim():
    decision = evaluate_yield_request(_request(), _state(allow_yield=False))

    assert decision.decision == DENIED
    assert decision.reason == "yield_disabled"


def test_unknown_pose_is_denied():
    state = YieldReceiverState(agent_id="Former0047", pose=None)

    decision = evaluate_yield_request(_request(), state)

    assert decision.decision == DENIED
    assert decision.reason == "pose_unknown"


def test_manipulation_in_progress_is_busy_with_retry_hint():
    decision = evaluate_yield_request(_request(), _state(manipulation_active=True))

    assert decision.decision == BUSY
    assert decision.reason == "manipulation_in_progress"
    # 양보는 가능하지만 파지/조작이 끝난 뒤에만 가능하므로 재시도를 안내한다.
    assert decision.retry_after_sec > 0.0


def test_critical_battery_denies_yield():
    decision = evaluate_yield_request(_request(), _state(battery_percent=5.0))

    assert decision.decision == DENIED
    assert decision.reason == "battery_critical"


def test_unknown_battery_does_not_block_yield():
    decision = evaluate_yield_request(_request(), _state(battery_percent=None))

    assert decision.decision == ACCEPTED


def test_boundary_distance_is_treated_as_blocking():
    # 정확히 radius 거리면 점유로 본다(경계 포함).
    state = _state(pose=Pose2D(2.0 + 0.8, 3.0, 0.0))

    decision = evaluate_yield_request(_request(radius_m=0.8), state)

    assert decision.decision == ACCEPTED


# --- 중재 (동시 요청) --------------------------------------------------------


@pytest.mark.parametrize(
    "requester_id,receiver_id",
    [("Stretch3", "Former0047"), ("Former0047", "Stretch3"), ("A", "B")],
)
def test_arbitration_is_antisymmetric(requester_id, receiver_id):
    forward = arbitration_outcome(requester_id, receiver_id)
    backward = arbitration_outcome(receiver_id, requester_id)

    assert {forward, backward} == {REQUESTER_WINS, RECEIVER_WINS}


def test_simultaneous_requests_yield_exactly_one_robot():
    # 두 로봇이 서로 같은 지점을 두고 동시에 자리를 요청하는 상황.
    target = Pose2D(2.0, 3.0, 0.0)
    stretch = _state(agent_id="Stretch3", pose=Pose2D(2.05, 3.0, 0.0))
    former = _state(agent_id="Former0047", pose=Pose2D(2.0, 3.05, 0.0))

    stretch_sees_former = evaluate_yield_request(
        _request(
            requester_id="Stretch3",
            target=target,
            requester_pose=Pose2D(2.05, 3.0, 0.0),
        ),
        former,
    )
    former_sees_stretch = evaluate_yield_request(
        _request(
            requester_id="Former0047",
            target=target,
            requester_pose=Pose2D(2.0, 3.05, 0.0),
        ),
        stretch,
    )

    accepted = [d for d in (stretch_sees_former, former_sees_stretch) if d.accepted]
    assert len(accepted) == 1, (
        "동시 요청에서 정확히 한 대만 양보해야 교착/진동이 없다: "
        f"{stretch_sees_former}, {former_sees_stretch}"
    )


def test_lower_priority_requester_gets_busy_not_accepted():
    # 두 로봇 모두 같은 반경 안(동시 점유)이고 requester_id > receiver_id 이므로
    # 수신측이 자기 자리를 지킨다.
    contested = Pose2D(2.0, 3.1, 0.0)
    decision = evaluate_yield_request(
        _request(requester_id="Zeta", requester_pose=contested),
        _state(agent_id="Former0047"),
    )

    assert decision.decision == BUSY
    assert decision.reason == "requester_lower_priority"
    assert decision.retry_after_sec > 0.0


def test_occupant_yields_even_with_lexicographic_priority():
    # 수신측 id 가 사전순으로 앞서도(중재상 우선이어도) 요청자가 아직 도착하지
    # 않았다면 자리를 점유한 쪽이 비켜준다.
    decision = evaluate_yield_request(
        _request(requester_id="Zeta", requester_pose=Pose2D(6.0, 6.0, 0.0)),
        _state(agent_id="Former0047"),
    )

    assert decision.decision == ACCEPTED


def test_self_request_never_yields():
    contested = Pose2D(2.0, 3.05, 0.0)
    decision = evaluate_yield_request(
        _request(requester_id="Former0047", requester_pose=contested), _state()
    )

    assert decision.decision == BUSY
    assert decision.reason == "requester_lower_priority"


# --- 양보 자리 선정 ----------------------------------------------------------


def _candidates():
    """수신측(2.1, 3.05) 기준 후보들. 요청자 목표는 (2.0, 3.0)."""
    return [
        (2.1, 4.2, 1.0),  # 요청자 목표에서 1.20m → clear radius 미달로 제외
        (2.1, 3.4, 1.0),  # 요청자 목표에서 0.40m → 제외
        (0.0, 0.0, 0.2),  # 개방도 0.2m → 제외
        (2.1, 0.0, 1.0),  # 유효 · 수신측에서 3.05m
        (0.0, 3.0, 1.0),  # 유효 · 수신측에서 2.10m (가장 가까움)
    ]


def test_select_yield_spot_skips_near_and_narrow_candidates():
    spot = select_yield_spot(
        _candidates(),
        receiver_pose=Pose2D(2.1, 3.05, 0.0),
        requester_target=Pose2D(2.0, 3.0, 0.0),
    )

    assert spot is not None
    assert (round(spot.x, 3), round(spot.y, 3)) == (0.0, 3.0)


def test_select_yield_spot_prefers_the_closest_valid_move():
    candidates = [(3.0, 0.0, 1.0), (2.6, 0.0, 1.0)]

    spot = select_yield_spot(
        candidates,
        receiver_pose=Pose2D(2.5, 0.0, 0.0),
        requester_target=Pose2D(0.0, 0.0, 0.0),
    )

    assert spot is not None
    assert spot.x == pytest.approx(2.6)


def test_select_yield_spot_returns_none_when_no_candidate_qualifies():
    candidates = [(2.0, 3.1, 1.0), (1.9, 2.9, 1.0)]

    spot = select_yield_spot(
        candidates,
        receiver_pose=Pose2D(2.05, 3.0, 0.0),
        requester_target=Pose2D(2.0, 3.0, 0.0),
    )

    assert spot is None


def test_select_yield_spot_ignores_non_finite_candidates():
    candidates = [(float("nan"), 0.0, 2.0), (0.0, float("inf"), 2.0), (1.0, 0.0, 1.0)]

    spot = select_yield_spot(
        candidates,
        receiver_pose=Pose2D(2.5, 0.0, 0.0),
        requester_target=Pose2D(-1.0, 0.0, 0.0),
    )

    assert spot is not None
    assert spot.x == pytest.approx(1.0)


def test_select_yield_spot_rejects_a_spot_too_far_from_the_receiver():
    # 양보가 집 반대편으로 이동하는 일이 되면 안 된다.
    candidates = [(0.0, 0.0, 1.0)]

    spot = select_yield_spot(
        candidates,
        receiver_pose=Pose2D(9.0, 9.0, 0.0),
        requester_target=Pose2D(8.0, 8.0, 0.0),
    )

    assert spot is None


# --- 결합 판정 ---------------------------------------------------------------


def test_plan_yield_accepts_with_a_concrete_stand_aside_spot():
    decision = plan_yield(_request(), _state(), _candidates())

    assert decision.decision == ACCEPTED
    assert decision.yield_spot is not None
    assert (round(decision.yield_spot.x, 3), round(decision.yield_spot.y, 3)) == (0.0, 3.0)
    assert decision.as_dict()["decision"] == ACCEPTED


def test_plan_yield_denies_when_no_safe_spot_exists():
    decision = plan_yield(
        _request(),
        _state(),
        [(2.0, 3.1, 1.0), (1.9, 2.9, 1.0)],
    )

    assert decision.decision == DENIED
    assert decision.reason == "no_safe_spot"
    assert decision.yield_spot is None


def test_plan_yield_does_not_look_for_a_spot_when_not_needed():
    decision = plan_yield(_request(), _state(pose=Pose2D(9.0, 9.0, 0.0)), _candidates())

    assert decision.decision == NOT_NEEDED
    assert decision.yield_spot is None


def test_plan_yield_rejects_invalid_request_before_negotiation():
    decision = plan_yield(_request(radius_m=9.0), _state(), _candidates())

    assert decision.decision == DENIED
    assert decision.reason == "invalid_request"


# --- 실행 가능 게이트 --------------------------------------------------------


def test_execution_gate_downgrades_accepted_when_agent_busy():
    from robo_claw_agent.domain.yield_slot import apply_execution_gate

    decision = apply_execution_gate(
        plan_yield(_request(), _state(), _candidates()), agent_busy=True
    )

    assert decision.decision == BUSY
    assert decision.reason == "agent_busy"
    assert decision.retry_after_sec > 0.0
    # 수락했다가 실행하지 못하는 상황이므로 목적지를 약속하지 않는다.
    assert decision.yield_spot is None


def test_execution_gate_keeps_accepted_when_agent_is_idle():
    from robo_claw_agent.domain.yield_slot import apply_execution_gate

    decision = apply_execution_gate(
        plan_yield(_request(), _state(), _candidates()), agent_busy=False
    )

    assert decision.decision == ACCEPTED
    assert decision.yield_spot is not None


def test_execution_gate_does_not_touch_non_accepted_decisions():
    from robo_claw_agent.domain.yield_slot import apply_execution_gate

    not_needed = evaluate_yield_request(_request(), _state(pose=Pose2D(9.0, 9.0, 0.0)))

    gated = apply_execution_gate(not_needed, agent_busy=True)

    assert gated.decision == NOT_NEEDED
    assert gated.reason == "not_blocking"


# --- 장소 이름 기반 협상 (맵 미공유 환경) ------------------------------------


def _place_request(*, requester_id="Stretch3", place="안방", requester_at_target=None):
    return YieldRequest(
        requester_id=requester_id,
        target=None,
        target_place=place,
        requester_at_target=requester_at_target,
    )


def test_place_only_request_passes_validation():
    assert validate_yield_request(_place_request()) == ""


def test_request_without_coords_or_place_is_rejected():
    request = YieldRequest(requester_id="Stretch3")

    assert validate_yield_request(request)


def test_unresolved_place_cannot_be_evaluated():
    """장소 이름은 수신측이 자기 맵에서 해석해야 한다. 해석 전엔 판정하지 않는다."""
    decision = evaluate_yield_request(_place_request(), _state())

    assert decision.decision == DENIED
    assert decision.reason == "target_unknown"


def test_place_resolved_to_receiver_frame_is_judged_with_receiver_coordinates():
    """장소를 수신측 좌표로 해석하면 수신측 pose와 비교해 판정한다.

    요청자가 동시 점유 여부를 보내지 않았으므로 보수적으로 동시 점유로 본다.
    """
    request = _place_request().with_target(Pose2D(2.0, 3.0, 0.0))

    decision = evaluate_yield_request(request, _state())

    assert decision.distance_m == pytest.approx(0.11, abs=0.01)
    assert decision.decision == BUSY
    assert decision.reason == "requester_lower_priority"


def test_with_target_keeps_the_place_name():
    request = _place_request().with_target(Pose2D(1.0, 2.0, 0.0))

    assert request.place_name() == "안방"
    assert request.target is not None


def test_requester_at_target_true_is_treated_as_contested():
    """요청자가 자기 맵에서 '나도 그 자리에 있다'고 판정하면 중재로 넘어간다."""
    request = _place_request(requester_id="Zeta", requester_at_target=True).with_target(
        Pose2D(2.0, 3.0, 0.0)
    )

    decision = evaluate_yield_request(request, _state(agent_id="Former0047"))

    assert decision.decision == BUSY
    assert decision.reason == "requester_lower_priority"


def test_requester_at_target_false_means_occupant_yields():
    request = _place_request(requester_id="Zeta", requester_at_target=False).with_target(
        Pose2D(2.0, 3.0, 0.0)
    )

    decision = evaluate_yield_request(request, _state(agent_id="Former0047"))

    assert decision.decision == ACCEPTED


def test_requester_at_target_overrides_foreign_pose():
    """다른 로봇의 좌표(다른 맵)를 넘겨도 요청자의 로컬 판정이 우선한다."""
    request = YieldRequest(
        requester_id="Zeta",
        target=Pose2D(2.0, 3.0, 0.0),
        # 다른 맵의 좌표라 목표와 겹쳐 보이지만, 요청자는 "나는 그 자리에 없다"고 판정했다.
        requester_pose=Pose2D(2.0, 3.05, 0.0),
        requester_at_target=False,
    )

    decision = evaluate_yield_request(request, _state(agent_id="Former0047"))

    assert decision.decision == ACCEPTED


def test_place_negotiation_yields_exactly_one_robot_when_both_are_there():
    """양쪽 모두 자기 맵에서 그 장소에 있다고 판정해도 한 대만 양보한다."""
    target = Pose2D(2.0, 3.0, 0.0)
    stretch = _state(agent_id="Stretch3", pose=target)
    former = _state(agent_id="Former0047", pose=target)

    stretch_sees_former = evaluate_yield_request(
        _place_request(requester_id="Stretch3", requester_at_target=True).with_target(target),
        former,
    )
    former_sees_stretch = evaluate_yield_request(
        _place_request(requester_id="Former0047", requester_at_target=True).with_target(target),
        stretch,
    )

    accepted = [d for d in (stretch_sees_former, former_sees_stretch) if d.accepted]
    assert len(accepted) == 1
