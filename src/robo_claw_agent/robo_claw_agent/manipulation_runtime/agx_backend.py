"""AGX(agx_arm_ctrl) 팔 드라이버 토픽 기반 manipulation 백엔드.

MoveIt 없이 `agx_arm_ctrl` 드라이버가 노출하는 표준 메시지 토픽만 사용한다.

- 조인트 목표: ``control/move_j`` (``sensor_msgs/JointState``)
- 포즈 목표: ``control/move_p`` (``geometry_msgs/PoseStamped``)
- 직선 포즈 목표: ``control/move_l``
- 완료 판정: ``feedback/joint_states``, ``feedback/tcp_pose``

MoveIt 서비스(`moveit_msgs`)를 거치지 않으므로 ROS 배포판이 다른 팔 제어 PC
(예: RoboClaw humble 컨테이너 ↔ 팔 PC jazzy)와도 통신할 수 있다. 대신 경로 계획과
충돌 검사 기능은 없으므로 joint/pose 한계는 상위 런타임(`robot_limits`)과
드라이버 자체 제한에 의존한다.

완료 판정은 피드백 수렴으로만 한다. 피드백이 아예 없거나(도메인/RMW 불일치,
드라이버 미기동) 목표에 도달하지 못하면 ``ManipulationError`` 로 실패한다.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import rclpy
from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import JointState

from .exceptions import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)

_POSITION_KEYS = ("x", "y", "z")


class AgxDriverBackend:
    """AGX 팔 드라이버 토픽 기반 백엔드"""

    def __init__(self, node: Any, config: Any) -> None:
        self._node = node
        self._config = config
        self._latest_joint_state: JointState | None = None
        self._latest_tcp_pose: PoseStamped | None = None
        self._joint_feedback_seen = False
        self._tcp_feedback_seen = False

        try:
            self._move_joint_pub = node.create_publisher(
                JointState, config.agx_move_joint_topic, 10
            )
            self._move_pose_pub = node.create_publisher(PoseStamped, config.agx_move_pose_topic, 10)
            self._move_linear_pub = node.create_publisher(
                PoseStamped, config.agx_move_linear_topic, 10
            )
            node.create_subscription(
                JointState,
                config.agx_joint_state_topic,
                self._on_joint_state,
                10,
            )
            node.create_subscription(
                PoseStamped,
                config.agx_tcp_pose_topic,
                self._on_tcp_pose,
                10,
            )
        except Exception as exc:  # noqa: BLE001 - 드라이버 인터페이스 생성 실패
            raise ManipulationRuntimeUnavailableError(
                f"AGX 드라이버 토픽 인터페이스 생성 실패: {exc}"
            ) from exc

    # ── 피드백 ──

    def _on_joint_state(self, msg: JointState) -> None:
        self._latest_joint_state = msg
        self._joint_feedback_seen = True

    def _on_tcp_pose(self, msg: PoseStamped) -> None:
        self._latest_tcp_pose = msg
        self._tcp_feedback_seen = True

    def _spin_once(self) -> None:
        try:
            rclpy.spin_once(self._node, timeout_sec=0.05)
        except Exception:  # noqa: BLE001 - 다른 executor 가 spin 중이면 sleep 으로 대체
            time.sleep(0.05)

    def _wait_until(self, predicate: Any, label: str, feedback_seen: Any) -> None:
        """목표 수렴까지 대기한다. 피드백/목표 도달 실패는 예외로 알린다."""
        start = time.monotonic()
        motion_deadline = start + float(self._config.agx_motion_timeout_sec)
        feedback_deadline = start + float(self._config.agx_feedback_timeout_sec)

        while True:
            if predicate():
                return

            now = time.monotonic()
            if not feedback_seen() and now > feedback_deadline:
                raise ManipulationError(
                    f"{label}: 팔 피드백을 받지 못했습니다. 드라이버 기동, CAN, "
                    f"ROS_DOMAIN_ID/RMW 설정을 확인하세요."
                )
            if now > motion_deadline:
                raise ManipulationError(
                    f"{label}: {float(self._config.agx_motion_timeout_sec):.1f}초 내에 "
                    f"목표에 도달하지 못했습니다."
                )
            self._spin_once()

    # ── 목표 판정 ──

    def _joints_reached(self, targets: Mapping[str, float]) -> bool:
        state = self._latest_joint_state
        if state is None:
            return False

        current = {
            str(name).strip(): float(position)
            for name, position in zip(state.name, state.position, strict=False)
        }
        tolerance = float(self._config.agx_joint_tolerance_rad)
        for joint_name, target in targets.items():
            if joint_name not in current:
                return False
            if abs(current[joint_name] - target) > tolerance:
                return False
        return True

    def _pose_reached(self, targets: Mapping[str, float]) -> bool:
        pose = self._latest_tcp_pose
        if pose is None:
            return False

        tolerance = float(self._config.agx_pose_tolerance_m)
        actual = {
            "x": pose.pose.position.x,
            "y": pose.pose.position.y,
            "z": pose.pose.position.z,
        }
        return all(abs(actual[key] - float(targets[key])) <= tolerance for key in _POSITION_KEYS)

    # ── 백엔드 인터페이스 ──

    def move_to_named_pose(self, group_name: str, target_name: str) -> None:
        joint_map = self._config.named_pose_joint_values.get(str(target_name).strip())
        if not joint_map:
            raise ManipulationConfigError(
                f"AGX 백엔드는 named pose '{target_name}' 의 조인트 값을 알 수 없습니다. "
                "SRDF group_state 대신 manipulation_named_poses_json 에 조인트 맵을 정의하세요."
            )
        self.move_to_joint_target(group_name, joint_map)

    def move_to_joint_target(self, group_name: str, joint_values: Mapping[str, float]) -> None:
        del group_name  # 드라이버는 MoveIt planning group 개념이 없다

        targets = {
            str(name).strip(): float(value)
            for name, value in joint_values.items()
            if str(name).strip()
        }
        if not targets:
            raise ManipulationConfigError("joint target 이 비어 있습니다.")

        msg = JointState()
        msg.name = list(targets.keys())
        msg.position = [targets[name] for name in msg.name]
        self._move_joint_pub.publish(msg)

        self._wait_until(
            lambda: self._joints_reached(targets),
            "joint 목표",
            lambda: self._joint_feedback_seen,
        )

    def move_to_pose_target(
        self,
        group_name: str,
        pose: Any,
        end_effector_link: str,
        cartesian: bool = False,
    ) -> None:
        del group_name, end_effector_link  # 드라이버가 tool/그룹을 자체 관리한다

        base_frame = str(self._config.base_frame or "").strip()
        frame = str(getattr(pose, "frame_id", "") or base_frame).strip()
        if base_frame and frame and frame != base_frame:
            raise ManipulationConfigError(
                f"AGX 드라이버는 base frame('{base_frame}') 기준 포즈 목표만 받습니다: '{frame}'"
            )

        targets: dict[str, float] = {}
        for key in _POSITION_KEYS:
            value = pose.position.get(key)
            if value is None:
                raise ManipulationConfigError(f"pose target 에 '{key}' 값이 없습니다.")
            targets[key] = float(value)

        msg = PoseStamped()
        msg.header.frame_id = base_frame
        msg.pose.position.x = targets["x"]
        msg.pose.position.y = targets["y"]
        msg.pose.position.z = targets["z"]
        orientation = getattr(pose, "orientation", None) or {}
        msg.pose.orientation.x = float(orientation.get("x", 0.0))
        msg.pose.orientation.y = float(orientation.get("y", 0.0))
        msg.pose.orientation.z = float(orientation.get("z", 0.0))
        msg.pose.orientation.w = float(orientation.get("w", 1.0))

        publisher = self._move_linear_pub if cartesian else self._move_pose_pub
        publisher.publish(msg)

        self._wait_until(
            lambda: self._pose_reached(targets),
            "pose 목표",
            lambda: self._tcp_feedback_seen,
        )
