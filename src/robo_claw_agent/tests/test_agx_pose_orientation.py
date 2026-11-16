"""AGX pose 목표의 자세(orientation) 검증 회귀 테스트.

이전에는 `_pose_reached` 가 위치(x/y/z)만 비교해, 자세가 크게 틀린 상태에서도
"도달"로 보고했습니다. 스킬은 그 값을 완료로 사용자에게 알리므로 완료 오보고가
됩니다. 이 파일은 (1) 자세 오차가 결과 payload 에 보고되는지, (2) 허용치가
설정되면 자세 오차를 도달 실패로 처리하는지 검증한다.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from robo_claw_agent.manipulation_runtime import agx_backend

pytestmark = pytest.mark.unit


def _quat_from_rpy(roll: float = 0.0, pitch: float = 0.0, yaw: float = 0.0) -> tuple:
    """RPY(rad) → quaternion (x, y, z, w)."""
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


class _Backend:
    """`_pose_reached`/자세 오차 계산만 검증하기 위한 최소 상태."""

    def __init__(self, *, position, orientation, tolerance=0.01, orientation_tolerance=0.0):
        quat = _quat_from_rpy(*orientation)
        self._latest_tcp_pose = SimpleNamespace(
            pose=SimpleNamespace(
                position=SimpleNamespace(x=position[0], y=position[1], z=position[2]),
                orientation=SimpleNamespace(x=quat[0], y=quat[1], z=quat[2], w=quat[3]),
            )
        )
        self._tcp_pose_time = 100.0
        self._config = SimpleNamespace(
            agx_pose_tolerance_m=tolerance,
            agx_pose_orientation_tolerance_rad=orientation_tolerance,
        )
        self.last_motion = None

    # 대상 메서드가 사용하는 헬퍼만 그대로 위임한다.
    def _feedback_is_fresh(self, stamp) -> bool:
        return True

    def _orientation_tolerance_rad(self) -> float:
        return agx_backend.AgxDriverBackend._orientation_tolerance_rad(self)

    def _orientation_verified(self) -> bool:
        return agx_backend.AgxDriverBackend._orientation_verified(self)

    def _orientation_error_rad(self, targets):
        return agx_backend.AgxDriverBackend._orientation_error_rad(self, targets)

    def _pose_reached(self, targets):
        return agx_backend.AgxDriverBackend._pose_reached(self, targets)


def _targets(position=(0.3, 0.0, 0.4), orientation=(0.0, 0.0, 0.0)):
    quat = _quat_from_rpy(*orientation)
    return {
        "x": position[0],
        "y": position[1],
        "z": position[2],
        "qx": quat[0],
        "qy": quat[1],
        "qz": quat[2],
        "qw": quat[3],
    }


def test_orientation_error_is_zero_for_matching_pose():
    backend = _Backend(position=(0.3, 0.0, 0.4), orientation=(0.0, 0.0, 0.0))

    error = agx_backend.AgxDriverBackend._orientation_error_rad(backend, _targets())

    assert error is not None
    assert error == pytest.approx(0.0, abs=1e-6)


def test_orientation_error_reports_yaw_deviation():
    backend = _Backend(position=(0.3, 0.0, 0.4), orientation=(0.0, 0.0, math.pi / 2))

    error = agx_backend.AgxDriverBackend._orientation_error_rad(backend, _targets())

    assert error is not None
    assert error == pytest.approx(math.pi / 2, abs=1e-3)


def test_position_only_still_reports_reached_when_orientation_not_enforced():
    """허용치가 0(기본)이면 기존 동작(위치 기준 도달)을 유지한다."""
    backend = _Backend(position=(0.3, 0.0, 0.4), orientation=(0.0, 0.0, math.pi / 2))

    assert backend._pose_reached(_targets()) is True
    assert backend._orientation_verified() is False


def test_orientation_tolerance_makes_bad_orientation_unreached():
    backend = _Backend(
        position=(0.3, 0.0, 0.4),
        orientation=(0.0, 0.0, math.pi / 2),
        orientation_tolerance=0.1,
    )

    assert backend._pose_reached(_targets()) is False


def test_orientation_within_tolerance_is_reached():
    backend = _Backend(
        position=(0.3, 0.0, 0.4),
        orientation=(0.0, 0.0, 0.05),
        orientation_tolerance=0.1,
    )

    assert backend._pose_reached(_targets()) is True


def test_missing_orientation_target_keeps_position_behaviour():
    """자세 허용치가 없으면(기본) 목표에 자세 값이 없어도 위치만으로 판정한다."""
    backend = _Backend(position=(0.3, 0.0, 0.4), orientation=(0.0, 0.0, 0.0))
    targets = {"x": 0.3, "y": 0.0, "z": 0.4}

    assert backend._pose_reached(targets) is True


def test_orientation_tolerance_without_orientation_target_fails_closed():
    """자세 허용치를 켰는데 목표에 자세가 없으면 검증 불가이므로 도달로 보지 않는다."""
    backend = _Backend(
        position=(0.3, 0.0, 0.4),
        orientation=(0.0, 0.0, 0.0),
        orientation_tolerance=0.1,
    )
    targets = {"x": 0.3, "y": 0.0, "z": 0.4}

    assert backend._pose_reached(targets) is False
