"""시뮬레이터 세계 모델(순수 파이썬, ROS 비의존).

RoboClaw는 ``ros2_control``을 사용하지 않는다. ``robo_claw_core``는
``/joint_states``를 구독하고 ``cmd_vel``(geometry_msgs/Twist)을 발행할 뿐이므로
(hardware_interface.cpp 참고), 시뮬레이터가 재현해야 하는 것은 "Twist를 받아
pose를 적분하고 센서를 합성하는" 최소한의 운동학이다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Pose2D:
    """2D 평면 pose(m, m, rad)."""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0

    def moved(self, dx: float, dy: float) -> Pose2D:
        return replace(self, x=self.x + dx, y=self.y + dy)

    def distance_to(self, other: Pose2D | tuple[float, float]) -> float:
        ox, oy = (other.x, other.y) if isinstance(other, Pose2D) else other
        return math.hypot(ox - self.x, oy - self.y)


@dataclass(frozen=True)
class Twist2D:
    """속도 명령(m/s, rad/s)."""

    vx: float = 0.0
    vyaw: float = 0.0


@dataclass(frozen=True)
class BoxObstacle:
    """축 정렬 사각형 장애물(중심 + 크기, m)."""

    name: str
    x: float
    y: float
    width: float
    height: float

    def contains(self, px: float, py: float, margin: float = 0.0) -> bool:
        half_w = self.width / 2.0 + margin
        half_h = self.height / 2.0 + margin
        return abs(px - self.x) <= half_w and abs(py - self.y) <= half_h


@dataclass(frozen=True)
class CircleObstacle:
    """원형 장애물(중심 + 반지름, m)."""

    name: str
    x: float
    y: float
    radius: float

    def contains(self, px: float, py: float, margin: float = 0.0) -> bool:
        return math.hypot(px - self.x, py - self.y) <= self.radius + margin


@dataclass(frozen=True)
class SimObject:
    """파지/정리 시나리오용 객체. 정답(class_id)을 시뮬레이터가 소유한다."""

    name: str
    class_id: str
    x: float
    y: float
    radius: float = 0.06
    graspable: bool = True


Obstacle = BoxObstacle | CircleObstacle


@dataclass
class World:
    """그리드 세계 상태. ``apply_twist``가 유일한 상태 변경 경로다."""

    width: float = 8.0
    height: float = 6.0
    resolution: float = 0.05
    robot: Pose2D = field(default_factory=Pose2D)
    robot_radius: float = 0.22
    obstacles: list[Obstacle] = field(default_factory=list)
    objects: list[SimObject] = field(default_factory=list)
    max_linear_speed: float = 0.5
    max_angular_speed: float = 1.5
    # 계측 값
    elapsed_sec: float = 0.0
    traveled_m: float = 0.0
    collisions: int = 0
    last_command: Twist2D = field(default_factory=Twist2D)

    # --- 그리드 ---
    @property
    def cols(self) -> int:
        return max(1, int(round(self.width / self.resolution)))

    @property
    def rows(self) -> int:
        return max(1, int(round(self.height / self.resolution)))

    def inside_bounds(self, x: float, y: float, margin: float = 0.0) -> bool:
        return margin <= x <= self.width - margin and margin <= y <= self.height - margin

    def is_free(self, x: float, y: float, margin: float = 0.0) -> bool:
        """(x, y)가 주행 가능한가. 월드 경계 밖은 점유로 본다."""
        if not self.inside_bounds(x, y, margin):
            return False
        return not any(obstacle.contains(x, y, margin) for obstacle in self.obstacles)

    def add_obstacle(self, obstacle: Obstacle) -> None:
        self.obstacles.append(obstacle)

    def add_object(self, obj: SimObject) -> None:
        self.objects.append(obj)

    def remove_object(self, name: str) -> bool:
        for index, obj in enumerate(self.objects):
            if obj.name == name:
                del self.objects[index]
                return True
        return False

    def object_by_name(self, name: str) -> SimObject | None:
        for obj in self.objects:
            if obj.name == name:
                return obj
        return None

    # --- 운동 ---
    def clamp_twist(self, twist: Twist2D) -> Twist2D:
        """속도 상한 적용. 하드웨어 안전 한계를 시뮬레이터에서도 동일하게 건다."""
        vx = max(-self.max_linear_speed, min(self.max_linear_speed, twist.vx))
        vyaw = max(-self.max_angular_speed, min(self.max_angular_speed, twist.vyaw))
        return Twist2D(vx=vx, vyaw=vyaw)

    def apply_twist(self, twist: Twist2D, dt: float) -> Pose2D:
        """Twist를 dt 동안 적분한다.

        충돌 시 해당 sub-step을 버리고 정지한다(관통 금지). 이동량이 그리드 한 칸을
        넘으면 sub-step으로 나눠 얇은 벽을 통과하는 터널링을 막는다.
        """
        if dt <= 0.0:
            return self.robot

        command = self.clamp_twist(twist)
        self.last_command = command
        self.elapsed_sec += dt

        travel = command.vx * dt
        steps = max(1, int(math.ceil(abs(travel) / max(self.resolution, 1e-6))))
        step_travel = travel / steps
        step_dt = dt / steps

        pose = self.robot
        for _ in range(steps):
            yaw = _normalize_angle(pose.yaw + command.vyaw * step_dt)
            next_pose = Pose2D(
                x=pose.x + step_travel * math.cos(yaw),
                y=pose.y + step_travel * math.sin(yaw),
                yaw=yaw,
            )
            if not self.is_free(next_pose.x, next_pose.y, self.robot_radius):
                self.collisions += 1
                self.robot = replace(pose, yaw=_normalize_angle(pose.yaw))
                return self.robot

            self.traveled_m += abs(step_travel)
            pose = next_pose

        self.robot = pose
        return self.robot

    def teleport(self, pose: Pose2D) -> bool:
        """시나리오 초기화/nm 설정용 순간 이동. 점유 위치면 거부한다."""
        if not self.is_free(pose.x, pose.y, self.robot_radius):
            return False
        self.robot = pose
        return True


def _normalize_angle(angle: float) -> float:
    """[-pi, pi) 로 정규화."""
    return math.atan2(math.sin(angle), math.cos(angle))
