"""head assist lateral align"""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _head_assist_helpers import (  # noqa: F401
    ServoGripperToObjectSkill,
    _aruco_skill,
    _lateral_skill,
    _make_node,
    _RotationRecorder,
    _stub_joint_positions,
    aruco_calib,
    ee_state,
    head_assist,
    math,
    pytest,
)


def test_lateral_align_rotates_for_moderate_error(monkeypatch):
    skill = _lateral_skill(monkeypatch)
    runtime = _RotationRecorder()
    bearing = ee_state._arm_reach_bearing_rad() + math.radians(6.0)
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target)

    assert info["rotated"] is True
    assert info["angle_deg"] == pytest.approx(6.0, abs=0.1)
    assert runtime.calls[0][0] == "base"
    assert runtime.calls[0][1]["rotate_mobile_base"] == pytest.approx(
        math.radians(6.0), abs=1e-3
    )
def test_lateral_align_rejects_implausibly_large_correction(monkeypatch):
    """상한을 넘는 각도는 오검출로 보고 회전하지 않는다."""
    skill = _lateral_skill(monkeypatch)
    runtime = _RotationRecorder()
    bearing = ee_state._arm_reach_bearing_rad() + math.radians(40.0)
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target)

    assert info["rotated"] is False
    assert "오검출" in info["reason"]
    assert runtime.calls == []
def test_lateral_align_refuses_rotation_with_extended_arm(monkeypatch):
    """팔이 뻗은 상태에서는 회전을 거부한다(_ROTATE_SAFE_WRIST_EXTENSION_M)."""
    skill = _lateral_skill(monkeypatch, wrist_extension=0.35)
    runtime = _RotationRecorder()
    bearing = ee_state._arm_reach_bearing_rad() + math.radians(6.0)
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target, allow_retract=False)

    assert info["rotated"] is False
    assert runtime.calls == []
def test_lateral_align_retracts_before_rotating_when_allowed(monkeypatch):
    """allow_retract=True 면 안전 길이까지 접고 → 회전 → 원래 길이로 복원한다."""
    skill = _lateral_skill(monkeypatch, wrist_extension=0.35)
    runtime = _RotationRecorder()
    bearing = ee_state._arm_reach_bearing_rad() + math.radians(6.0)
    target = (0.7 * math.cos(bearing), 0.7 * math.sin(bearing), 0.7)

    info = head_assist.lateral_align(skill, runtime, {}, target, allow_retract=True)

    assert info["rotated"] is True
    assert [call[0] for call in runtime.calls] == ["arm", "base", "arm"]
    assert runtime.calls[0][1]["wrist_extension"] == pytest.approx(0.15)
    assert runtime.calls[2][1]["wrist_extension"] == pytest.approx(0.35)
def test_decompose_base_error_maps_axes_correctly():
    """FK 로 확인한 축 대응: -y = 팔 신장 방향, +x = 좌우, z = lift."""
    along, lateral, vertical = ee_state._decompose_base_error((0.0, -0.10, 0.05))
    assert along == pytest.approx(0.10, abs=1e-3)  # 더 뻗어야 함
    assert lateral == pytest.approx(0.0, abs=5e-3)
    assert vertical == pytest.approx(0.05)

    along, lateral, vertical = ee_state._decompose_base_error((0.10, 0.0, 0.0))
    assert along == pytest.approx(0.0, abs=5e-3)
    assert lateral == pytest.approx(0.10, abs=1e-3)  # 좌우 = 베이스 회전 필요
def test_marker_residual_averages_wrist_markers():
    """두 손목 마커의 잔차를 평균낸다 (URDF - 측정)."""
    fresh = 9_500_000_000  # 0.5s 전
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, fresh),
            "link_aruco_inner_wrist": (0.02, -0.60, 0.70, fresh),
            "wrist_top": (0.0, -0.62, 0.72, fresh),
            "link_aruco_top_wrist": (0.02, -0.62, 0.72, fresh),
        }
    )

    result = aruco_calib._marker_tf_residual(skill, {})

    assert result is not None
    residual, diagnostics = result
    assert residual == pytest.approx((0.02, 0.0, 0.0))
    assert set(diagnostics["pairs_used"]) == {"wrist_inside", "wrist_top"}
def test_marker_residual_rejects_stale_marker_tf():
    """마커 TF 가 오래됐으면(지금 안 보임) 보정하지 않는다."""
    stale = 1_000_000_000  # 9s 전
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, stale),
            "link_aruco_inner_wrist": (0.02, -0.60, 0.70, stale),
        }
    )

    assert aruco_calib._marker_tf_residual(skill, {}) is None
def test_marker_residual_rejects_oversized_residual():
    """잔차가 상한을 넘으면 마커 오검출로 보고 버린다."""
    fresh = 9_500_000_000
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, fresh),
            "link_aruco_inner_wrist": (0.50, -0.60, 0.70, fresh),
        }
    )

    assert aruco_calib._marker_tf_residual(skill, {}) is None
def test_marker_residual_rejects_disagreeing_pairs():
    """두 마커가 서로 다른 답을 내면 신뢰하지 않는다."""
    fresh = 9_500_000_000
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, fresh),
            "link_aruco_inner_wrist": (0.02, -0.60, 0.70, fresh),
            "wrist_top": (0.0, -0.62, 0.72, fresh),
            "link_aruco_top_wrist": (-0.08, -0.62, 0.72, fresh),
        }
    )

    assert aruco_calib._marker_tf_residual(skill, {}) is None
def test_marker_residual_none_when_aruco_node_not_running():
    """aruco 노드 미실행(마커 TF 없음) -> None. 보정 없이 그대로 진행한다."""
    fresh = 9_500_000_000
    skill = _aruco_skill({"link_aruco_inner_wrist": (0.02, -0.60, 0.70, fresh)})

    assert aruco_calib._marker_tf_residual(skill, {}) is None
def test_marker_residual_none_without_tf_buffer():
    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node({}))

    assert aruco_calib._marker_tf_residual(skill, {}) is None
def test_residual_cache_latches_and_applies_offset():
    """마커가 안 보이게 되어도 래치된 잔차가 유지된다."""
    cache = aruco_calib.MarkerResidualCache()
    assert cache.residual is None
    assert cache.apply((1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)

    fresh = 9_500_000_000
    skill = _aruco_skill(
        {
            "wrist_inside": (0.0, -0.60, 0.70, fresh),
            "link_aruco_inner_wrist": (0.02, -0.60, 0.70, fresh),
        }
    )
    assert cache.refresh(skill, {}) is True
    assert cache.apply((1.0, 2.0, 3.0)) == pytest.approx((1.02, 2.0, 3.0))

    # 마커가 사라져도 래치된 값은 유지된다.
    empty_skill = _aruco_skill({})
    assert cache.refresh(empty_skill, {}) is False
    assert cache.apply((1.0, 2.0, 3.0)) == pytest.approx((1.02, 2.0, 3.0))
def test_marker_frame_pairs_configurable_via_json():
    pairs = aruco_calib._marker_frame_pairs(
        {"aruco_marker_frame_pairs_json": '[["m_a","u_a"],["m_b","u_b"]]'}
    )
    assert pairs == [("m_a", "u_a"), ("m_b", "u_b")]

    # 깨진 JSON 은 기본값으로 안전하게 폴백.
    assert aruco_calib._marker_frame_pairs(
        {"aruco_marker_frame_pairs_json": "{not json"}
    ) == list(aruco_calib._DEFAULT_MARKER_FRAME_PAIRS)
