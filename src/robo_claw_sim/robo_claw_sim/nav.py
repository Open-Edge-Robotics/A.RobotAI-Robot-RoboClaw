"""목표 추종 제어기(순수 파이썬).

전용 시뮬레이터의 1계층(T1)은 Nav2 없이도 ``NavigateToPose``/``Spin`` 의미론을
검증할 수 있어야 한다. 여기서는 그 최소 제어 법칙을 순수 함수로 두고,
ROS 액션 서버가 이를 호출한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .world import Pose2D, Twist2D


@dataclass(frozen=True)
class GoalPose:
    x: float
    y: float
    yaw: float | None = None
    tolerance: float = 0.25
    yaw_tolerance: float = 0.15
    label: str = ""

    def distance_to(self, pose: Pose2D) -> float:
        return math.hypot(self.x - pose.x, self.y - pose.y)


@dataclass(frozen=True)
class ControllerConfig:
    """제어 이득/한계. 시뮬레이터가 실제 로봇보다 빨라지지 않도록 기본값을 낮게 둔다."""

    linear_gain: float = 0.8
    angular_gain: float = 1.6
    max_linear: float = 0.45
    max_angular: float = 1.2
    # 목표 방향 오차가 이 값보다 크면 회전만 한다.
    rotate_in_place_threshold: float = 0.5


def compute_twist(
    pose: Pose2D,
    goal: GoalPose,
    config: ControllerConfig | None = None,
) -> Twist2D:
    """목표를 향한 Twist 명령. 도달했으면 영속도."""
    cfg = config or ControllerConfig()
    dx = goal.x - pose.x
    dy = goal.y - pose.y
    distance = math.hypot(dx, dy)
    bearing = math.atan2(dy, dx) - pose.yaw
    bearing_error = _normalize(bearing)

    if distance <= goal.tolerance:
        if goal.yaw is None:
            return Twist2D()
        yaw_error = _normalize(goal.yaw - pose.yaw)
        if abs(yaw_error) <= goal.yaw_tolerance:
            return Twist2D()
        return Twist2D(vx=0.0, vyaw=_clamp(cfg.angular_gain * yaw_error, cfg.max_angular))

    vyaw = _clamp(cfg.angular_gain * bearing_error, cfg.max_angular)
    if abs(bearing_error) > cfg.rotate_in_place_threshold:
        return Twist2D(vx=0.0, vyaw=vyaw)

    vx = _clamp(cfg.linear_gain * distance, cfg.max_linear)
    return Twist2D(vx=vx, vyaw=vyaw)


def spin_twist(
    target_yaw: float,
    current_yaw: float,
    yaw_tolerance: float = 0.1,
    max_angular: float = 1.0,
    gain: float = 1.5,
) -> Twist2D:
    """제자리 회전(nav2_msgs/Spin 대응)."""
    error = _normalize(target_yaw - current_yaw)
    if abs(error) <= yaw_tolerance:
        return Twist2D()
    return Twist2D(vx=0.0, vyaw=_clamp(gain * error, max_angular))


def is_goal_reached(pose: Pose2D, goal: GoalPose) -> bool:
    if goal.distance_to(pose) > goal.tolerance:
        return False
    if goal.yaw is None:
        return True
    return abs(_normalize(goal.yaw - pose.yaw)) <= goal.yaw_tolerance


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _normalize(angle: float) -> float:
    return math.atan2(math.sin(angle), math.cos(angle))
