"""명령 중재기(순수 파이썬).

``cmd_vel`` 외부 명령과 시뮬레이터 내부 내비게이션 목표가 동시에 존재할 수 있다.
RoboClaw 는 명령이 끊기면 정지하는 watchdog 을 갖고 있으므로(robo_claw_core 의
``command_timeout_sec``) 시뮬레이터도 같은 규칙으로 오래된 명령을 버린다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .nav import ControllerConfig, GoalPose, compute_twist, is_goal_reached, spin_twist
from .world import Twist2D, World

IDLE = "idle"
POSE = "pose"
SPIN = "spin"


@dataclass
class MotionArbiter:
    world: World
    config: ControllerConfig = field(default_factory=ControllerConfig)
    command_timeout_sec: float = 0.5
    external: Twist2D = field(default_factory=Twist2D)
    external_at: float = 0.0
    mode: str = IDLE
    goal: GoalPose | None = None
    target_yaw: float = 0.0
    started_at: float = 0.0
    spin_tolerance: float = 0.08

    # --- 외부 cmd_vel ---
    def set_external(self, twist: Twist2D, now: float) -> None:
        self.external = twist
        self.external_at = now

    def external_is_stale(self, now: float) -> bool:
        return (now - self.external_at) > self.command_timeout_sec

    # --- 내비게이션 목표 ---
    def start_goal(self, goal: GoalPose, now: float) -> None:
        self.mode = POSE
        self.goal = goal
        self.started_at = now

    def start_spin(self, target_yaw: float, now: float) -> None:
        self.mode = SPIN
        self.target_yaw = target_yaw
        self.started_at = now

    def clear(self) -> None:
        self.mode = IDLE
        self.goal = None

    def pose_goal_reached(self) -> bool:
        return (
            self.mode == POSE
            and self.goal is not None
            and is_goal_reached(self.world.robot, self.goal)
        )

    def spin_done(self, tolerance: float | None = None) -> bool:
        if self.mode != SPIN:
            return False
        limit = self.spin_tolerance if tolerance is None else tolerance
        error = self.target_yaw - self.world.robot.yaw
        error = (error + 3.141592653589793) % 6.283185307179586 - 3.141592653589793
        return abs(error) <= limit

    # --- 매 tick 명령 ---
    def command(self, now: float) -> Twist2D:
        if self.mode == POSE and self.goal is not None:
            return compute_twist(self.world.robot, self.goal, self.config)
        if self.mode == SPIN:
            return spin_twist(
                self.target_yaw,
                self.world.robot.yaw,
                yaw_tolerance=self.spin_tolerance,
                max_angular=self.config.max_angular,
            )
        if self.external_is_stale(now):
            return Twist2D()
        return self.external
