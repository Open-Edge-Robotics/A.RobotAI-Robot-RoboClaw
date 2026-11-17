"""조작용 액션 서버(control_msgs/FollowJointTrajectory).

RoboClaw 조작 스택은 팔·그리퍼를 모두 같은 ``FollowJointTrajectory`` 로 제어한다
(``manipulation_runtime/stretch_backend.py``). 시뮬레이터도 같은 메시지를 제공해
스킬 코드를 수정 없이 실행할 수 있게 한다.

궤적은 시뮬레이션 시간(``world.elapsed_sec``) 기준으로 샘플링해 백엔드의 관절 목표로
넘기고, 종료 후 도달 오차를 goal tolerance 와 비교한다.
"""

from __future__ import annotations

import time

from control_msgs.action import FollowJointTrajectory
from rclpy.action import ActionServer
from rclpy.node import Node

_POLL_SEC = 0.02


def _set_error(result, code: int, message: str) -> None:
    """control_msgs 버전에 따라 error_string/error_msg 가 달라 흡수한다."""
    result.error_code = code
    if hasattr(result, "error_string"):
        result.error_string = message
    else:  # pragma: no cover - 구버전 메시지
        result.error_msg = message


# goal tolerance 가 비어 있을 때 쓰는 기본 허용 오차(rad 또는 m).
_DEFAULT_TOLERANCE = 0.05


class SimManipulation:
    """관절 궤적 액션 서버 + 파지 상태 수집."""

    def __init__(self, node: Node, backend, action_name: str) -> None:  # noqa: ANN001
        self.node = node
        self.backend = backend
        self.action_name = action_name
        self.grasped: set[str] = set()
        self.server = ActionServer(node, FollowJointTrajectory, action_name, self._execute)

    def destroy(self) -> None:
        self.server.destroy()

    def grasped_objects(self) -> list[str]:
        return sorted(self.grasped)

    # ------------------------------------------------------------------ 실행
    def _execute(self, goal_handle):  # noqa: ANN001, ANN202
        result = FollowJointTrajectory.Result()
        trajectory = goal_handle.request.trajectory
        names = list(trajectory.joint_names)
        points = list(trajectory.points)
        if not names or not points:
            goal_handle.abort()
            _set_error(result, FollowJointTrajectory.Result.INVALID_GOAL, "빈 궤적입니다")
            return result

        schedule = [(self._point_sec(point), list(point.positions)) for point in points]
        tolerance = self._tolerance(goal_handle.request)
        deadline = schedule[-1][0] + max(self._node_timeout(), 1.0)
        started = self._now()

        while True:
            elapsed = self._now() - started
            self._apply(schedule, elapsed, names)
            if goal_handle.is_cancel_requested:
                goal_handle.canceled()
                return result
            if elapsed >= schedule[-1][0]:
                break
            if elapsed > deadline:
                goal_handle.abort()
                result.error_code = FollowJointTrajectory.Result.GOAL_TOLERANCE_VIOLATED
                result.error_string = "trajectory 시간 초과"
                return result
            time.sleep(_POLL_SEC)

        self._apply(schedule, schedule[-1][0], names)
        error = self._final_error(names, schedule[-1][1])
        if error > tolerance:
            goal_handle.abort()
            result.error_code = FollowJointTrajectory.Result.GOAL_TOLERANCE_VIOLATED
            result.error_string = f"goal tolerance 초과: {error:.3f} > {tolerance:.3f}"
            return result

        goal_handle.succeed()
        result.error_code = FollowJointTrajectory.Result.SUCCESSFUL
        return result

    # ---------------------------------------------------------------- 내부
    def _now(self) -> float:
        return float(self.backend.world.elapsed_sec)

    def _node_timeout(self) -> float:
        return float(self.node.get_parameter("manipulation_timeout_sec").value)

    def _point_sec(self, point) -> float:  # noqa: ANN001
        duration = point.time_from_start
        return float(duration.sec) + float(duration.nanosec) * 1e-9

    def _tolerance(self, request) -> float:  # noqa: ANN001
        for candidate in (
            getattr(request, "goal_tolerance", []),
            getattr(request, "path_tolerance", []),
        ):
            for tolerance in candidate or []:
                if tolerance.name:
                    return max(float(tolerance.position), _DEFAULT_TOLERANCE)
        return _DEFAULT_TOLERANCE

    def _apply(
        self, schedule: list[tuple[float, list[float]]], elapsed: float, names: list[str]
    ) -> None:
        target = schedule[0][1]
        for sec, positions in schedule:
            if elapsed >= sec:
                target = positions
            else:
                break
        self.backend.set_joint_targets(dict(zip(names, target, strict=False)))
        self._record_grasp()

    def _record_grasp(self) -> None:
        grasped = self.backend.grasp_state().grasped_object
        if grasped:
            self.grasped.add(grasped)

    def _final_error(self, names: list[str], target: list[float]) -> float:
        positions = self.backend.joint_positions()
        errors = [
            abs(positions.get(name, 0.0) - value)
            for name, value in zip(names, target, strict=False)
        ]
        return max(errors) if errors else 0.0
