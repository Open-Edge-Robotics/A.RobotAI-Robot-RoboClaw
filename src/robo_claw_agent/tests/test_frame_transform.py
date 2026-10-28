"""로봇 간 맵 좌표계 정합 단위 테스트 (ROS2 불필요).

두 로봇이 각자 SLAM 맵을 쓸 때 coordinate 비교가 성립하려면 변환이 필요하다.
공유 장소 이름만으로 변환을 추정하고, 추정이 위험하면 **변환을 만들지 않는지**
(fail-closed) 검증한다.
"""

import math

import pytest
from robo_claw_agent.domain.frame_transform import (
    MAX_RMS_RESIDUAL_M,
    Transform2D,
    fit_frame_transform,
    fit_similarity_transform,
    shared_place_pairs,
)
from robo_claw_agent.domain.yield_slot import Pose2D

pytestmark = pytest.mark.unit


def _rotate(point, degrees):
    rad = math.radians(degrees)
    cos_r, sin_r = math.cos(rad), math.sin(rad)
    return (cos_r * point[0] - sin_r * point[1], sin_r * point[0] + cos_r * point[1])


def _make_peer_points(own_points, *, degrees=90.0, scale=1.0, tx=5.0, ty=-2.0):
    """내 좌표를 '동료 좌표'로 되돌린다(변환의 역방향 샘플 생성)."""
    peer = []
    for point in own_points:
        shifted = ((point[0] - tx) / scale, (point[1] - ty) / scale)
        peer.append(_rotate(shifted, -degrees))
    return peer


# --- 변환 적용 ---------------------------------------------------------------


def test_identity_transform_keeps_pose():
    pose = Transform2D().apply(Pose2D(1.0, 2.0, 0.3))

    assert (pose.x, pose.y) == pytest.approx((1.0, 2.0))
    assert pose.yaw == pytest.approx(0.3)


def test_transform_applies_rotation_translation_and_scale():
    transform = Transform2D(scale=1.0, rotation_rad=math.pi / 2, tx=2.0, ty=1.0)

    pose = transform.apply(Pose2D(1.0, 0.0, 0.0))

    assert pose.x == pytest.approx(2.0, abs=1e-9)
    assert pose.y == pytest.approx(2.0, abs=1e-9)
    assert pose.yaw == pytest.approx(math.pi / 2)


# --- 정합 추정 ---------------------------------------------------------------


def test_exact_correspondences_recover_the_transform():
    own = [(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]
    peer = _make_peer_points(own, degrees=90.0, scale=1.0, tx=5.0, ty=-2.0)

    fit = fit_similarity_transform(peer, own)

    assert fit.ok is True
    assert fit.reason == "fitted"
    assert fit.correspondences == 3
    assert fit.rms_residual_m == pytest.approx(0.0, abs=1e-6)
    assert fit.transform is not None
    # 추정된 변환으로 동료 좌표를 옮기면 내 좌표가 나와야 한다.
    for peer_point, own_point in zip(peer, own, strict=True):
        mapped = fit.transform.apply(Pose2D(peer_point[0], peer_point[1], 0.0))
        assert (mapped.x, mapped.y) == pytest.approx(own_point, abs=1e-6)


def test_two_correspondences_are_enough():
    own = [(0.0, 0.0), (2.0, 0.0)]
    peer = _make_peer_points(own, degrees=45.0, tx=1.0, ty=1.0)

    assert fit_similarity_transform(peer, own).ok is True


def test_identical_frames_report_identity():
    own = [(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]

    fit = fit_similarity_transform(own, own)

    assert fit.ok is True
    assert fit.reason == "identity"
    assert fit.transform is not None
    assert fit.transform.is_identity() is True


def test_noise_within_threshold_is_accepted():
    own = [(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]
    peer = _make_peer_points(own, degrees=90.0, tx=5.0, ty=-2.0)
    peer = [(x + 0.05, y - 0.03) for x, y in peer]

    fit = fit_similarity_transform(peer, own)

    assert fit.ok is True
    assert fit.rms_residual_m is not None
    assert fit.rms_residual_m < MAX_RMS_RESIDUAL_M


# --- fail-closed -------------------------------------------------------------


def test_single_correspondence_is_rejected():
    fit = fit_similarity_transform([(1.0, 2.0)], [(3.0, 4.0)])

    assert fit.ok is False
    assert fit.reason == "insufficient_correspondences"


def test_mismatched_lengths_are_rejected():
    fit = fit_similarity_transform([(0.0, 0.0), (1.0, 1.0)], [(0.0, 0.0)])

    assert fit.ok is False
    assert fit.reason == "length_mismatch"


def test_non_finite_points_are_rejected():
    fit = fit_similarity_transform([(float("nan"), 0.0), (3.0, 0.0)], [(0.0, 0.0), (3.0, 0.0)])

    assert fit.ok is False
    assert fit.reason == "non_finite_correspondence"


def test_points_clustered_in_one_spot_are_rejected():
    """대응점이 한 곳에 몰리면 회전을 결정할 수 없다."""
    own = [(1.0, 1.0), (1.1, 1.05)]
    peer = [(1.0, 1.0), (1.1, 1.05)]

    fit = fit_similarity_transform(peer, own)

    assert fit.ok is False
    assert fit.reason == "degenerate_spread"


def test_implausible_scale_is_rejected():
    """서로 다른 방을 같은 이름으로 매칭하면 축척이 튄다 — 그때는 쓰지 않는다."""
    own = [(0.0, 0.0), (2.0, 0.0)]
    peer = [(0.0, 0.0), (20.0, 0.0)]  # 10배

    fit = fit_similarity_transform(peer, own)

    assert fit.ok is False
    assert fit.reason == "implausible_scale"


def test_large_residual_is_rejected():
    own = [(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]
    peer = [(0.0, 0.0), (3.0, 0.0), (0.0, 4.0)]
    # 한 점만 크게 어긋나게 해 대응이 틀린 상황을 만든다.
    peer[2] = (5.0, 5.0)

    fit = fit_similarity_transform(peer, own)

    assert fit.ok is False
    assert fit.reason in {"residual_too_large", "implausible_scale"}


# --- 장소 이름 대응 ----------------------------------------------------------


def test_shared_places_are_matched_ignoring_spacing_and_case():
    peer = {"Living Room": (1.0, 1.0), "안방": (2.0, 2.0), "화장실": (9.0, 9.0)}
    own = {"livingroom": (0.0, 0.0), "안 방": (1.0, 0.0)}

    source, target, names = shared_place_pairs(peer, own)

    assert names == ["Living Room", "안방"]
    assert source == [(1.0, 1.0), (2.0, 2.0)]
    assert target == [(0.0, 0.0), (1.0, 0.0)]


def test_frame_transform_from_place_names():
    own = {"거실": (0.0, 0.0), "안방": (3.0, 0.0), "주방": (0.0, 4.0)}
    peer_points = _make_peer_points(list(own.values()), degrees=90.0, tx=5.0, ty=-2.0)
    peer = dict(zip(list(own), peer_points, strict=True))

    fit = fit_frame_transform(peer, own)

    assert fit.ok is True
    assert fit.transform is not None
    mapped = fit.transform.apply(Pose2D(*peer["안방"], 0.0))
    assert (mapped.x, mapped.y) == pytest.approx((3.0, 0.0), abs=1e-6)


def test_place_transform_requires_two_common_places():
    fit = fit_frame_transform({"거실": (1.0, 1.0), "창고": (5.0, 5.0)}, {"거실": (0.0, 0.0)})

    assert fit.ok is False
    assert fit.reason == "insufficient_correspondences"


def test_no_common_places_means_no_transform():
    fit = fit_frame_transform({"거실": (1.0, 1.0)}, {"주방": (0.0, 0.0)})

    assert fit.ok is False


def test_fit_dict_is_json_serializable_shape():
    own = {"거실": (0.0, 0.0), "안방": (3.0, 0.0)}
    peer_points = _make_peer_points(list(own.values()))
    peer = dict(zip(list(own), peer_points, strict=True))

    payload = fit_frame_transform(peer, own).as_dict()

    assert payload["ok"] is True
    assert "transform" in payload
    assert set(payload["transform"]) == {"scale", "rotation_rad", "tx", "ty"}
