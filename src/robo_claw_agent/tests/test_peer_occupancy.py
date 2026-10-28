"""목표 지점 점유 판정 단위 테스트 (ROS2 불필요).

동료 자세 공유로 "내 목표 지점이 동료에게 막혀 있는가"를 판정하는 순수 규칙을
검증한다. 오래된 자세·다른 프레임·비정상 좌표는 점유로 보지 않는다.
"""

import pytest
from robo_claw_agent.domain.peer_occupancy import (
    DEFAULT_MAX_AGE_SEC,
    PeerPoseSample,
    describe_occupants,
    find_blocking_peers,
)

pytestmark = pytest.mark.unit

_NOW = 1000.0
_TARGET_X = 2.0
_TARGET_Y = 3.0


def _sample(
    agent_id: str = "Former0047",
    *,
    x: float = 2.1,
    y: float = 3.05,
    age_sec: float = 0.5,
    frame_id: str = "map",
    yieldable: bool = True,
    current_skill: str = "",
) -> PeerPoseSample:
    return PeerPoseSample(
        agent_id=agent_id,
        x=x,
        y=y,
        yaw=0.0,
        observed_at_sec=_NOW - age_sec,
        frame_id=frame_id,
        current_skill=current_skill,
        yieldable=yieldable,
    )


def _find(samples, **kwargs):
    params = {"target_x": _TARGET_X, "target_y": _TARGET_Y, "now_sec": _NOW}
    params.update(kwargs)
    return find_blocking_peers(samples, **params)


# --- 점유 판정 ---------------------------------------------------------------


def test_peer_standing_on_the_target_is_reported():
    occupants = _find([_sample()])

    assert [s.agent_id for s in occupants] == ["Former0047"]


def test_peer_far_from_the_target_is_not_reported():
    occupants = _find([_sample(x=6.0, y=9.0)])

    assert occupants == []


def test_boundary_distance_counts_as_occupying():
    # 정확히 반경 거리 — 자리 양보 판정과 같은 기준(경계 포함).
    occupants = _find([_sample(x=_TARGET_X + 0.8, y=_TARGET_Y)], radius_m=0.8)

    assert len(occupants) == 1


def test_occupants_are_sorted_by_distance():
    occupants = _find(
        [
            _sample("Far", x=2.5, y=3.0),
            _sample("Near", x=2.05, y=3.0),
        ],
        radius_m=1.0,
    )

    assert [s.agent_id for s in occupants] == ["Near", "Far"]


def test_radius_argument_is_respected():
    samples = [_sample(x=2.9, y=3.0)]  # 목표에서 0.9m

    assert _find(samples, radius_m=0.8) == []
    assert len(_find(samples, radius_m=1.0)) == 1


# --- fail-closed --------------------------------------------------------------


def test_stale_pose_is_not_treated_as_occupying():
    occupants = _find([_sample(age_sec=DEFAULT_MAX_AGE_SEC + 1.0)])

    assert occupants == []


def test_pose_with_other_frame_is_ignored():
    occupants = _find([_sample(frame_id="odom")])

    assert occupants == []


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_non_finite_pose_is_ignored(bad):
    occupants = _find([_sample(x=bad)])

    assert occupants == []


def test_self_is_excluded():
    occupants = _find([_sample("Stretch3")], exclude_agent_id="Stretch3")

    assert occupants == []


@pytest.mark.parametrize("radius", [0.0, -1.0, float("nan")])
def test_invalid_radius_yields_no_occupants(radius):
    assert _find([_sample()], radius_m=radius) == []


def test_future_pose_is_ignored():
    """시계 오차로 미래 시각이 된 자세는 신뢰하지 않는다."""
    occupants = _find([_sample(age_sec=-2.0)])

    assert occupants == []


# --- 요약 --------------------------------------------------------------------


def test_description_marks_manipulating_peer():
    text = describe_occupants(
        [_sample(yieldable=False)], target_x=_TARGET_X, target_y=_TARGET_Y
    )

    assert "Former0047" in text
    assert "조작 중" in text


def test_description_is_empty_without_occupants():
    assert describe_occupants([], target_x=_TARGET_X, target_y=_TARGET_Y) == ""
