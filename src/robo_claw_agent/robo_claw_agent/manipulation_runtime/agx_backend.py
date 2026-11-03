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

import logging
import time
from collections.abc import Mapping
from typing import Any

from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import JointState
from std_srvs.srv import Empty, SetBool

from ._ros_sync import wait_for_future_sync
from .exceptions import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)

logger = logging.getLogger(__name__)

_POSITION_KEYS = ("x", "y", "z")
_REPUBLISH_INTERVAL_SEC = 0.5


class AgxDriverBackend:
    """AGX 팔 드라이버 토픽 기반 백엔드"""

    def __init__(self, node: Any, config: Any) -> None:
        self._node = node
        self._config = config
        self._latest_joint_state: JointState | None = None
        self._latest_tcp_pose: PoseStamped | None = None
        self._joint_feedback_seen = False
        self._tcp_feedback_seen = False
        self._home_client = None
        self._enable_client = None
        self._control_enable_client = None
        self._driver_ready = False

        cb_group = None
        try:
            from rclpy.callback_groups import ReentrantCallbackGroup

            cb_group = ReentrantCallbackGroup()
        except ImportError:
            pass
        self._cb_group = cb_group

        sub_kwargs: dict[str, Any] = {}
        if self._cb_group is not None:
            sub_kwargs["callback_group"] = self._cb_group

        try:
            self._move_joint_pub = node.create_publisher(
                JointState, config.agx_move_joint_topic, 10
            )
            self._move_pose_pub = node.create_publisher(PoseStamped, config.agx_move_pose_topic, 10)
            self._move_linear_pub = node.create_publisher(
                PoseStamped, config.agx_move_linear_topic, 10
            )
            try:
                node.create_subscription(
                    JointState,
                    config.agx_joint_state_topic,
                    self._on_joint_state,
                    10,
                    **sub_kwargs,
                )
                node.create_subscription(
                    PoseStamped,
                    config.agx_tcp_pose_topic,
                    self._on_tcp_pose,
                    10,
                    **sub_kwargs,
                )
            except TypeError:
                # _FakeNode 처럼 callback_group 을 받지 않는 대역 노드 fallback
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

    # ── 드라이버 활성화 (enable_agx_arm / control_enable) ──

    def _call_set_bool_service(self, client_attr: str, service_name: str, label: str) -> None:
        name = str(service_name or "").strip()
        if not name:
            return

        client = getattr(self, client_attr, None)
        if client is None:
            try:
                client_kwargs: dict[str, Any] = {}
                if self._cb_group is not None:
                    client_kwargs["callback_group"] = self._cb_group
                try:
                    client = self._node.create_client(SetBool, name, **client_kwargs)
                except TypeError:
                    client = self._node.create_client(SetBool, name)
                setattr(self, client_attr, client)
            except Exception as exc:  # noqa: BLE001 - 서비스 클라이언트 생성 실패
                raise ManipulationRuntimeUnavailableError(
                    f"AGX {label} 서비스 클라이언트 생성 실패({name}): {exc}"
                ) from exc

        if not client.wait_for_service(timeout_sec=5.0):
            raise ManipulationRuntimeUnavailableError(
                f"AGX {label} 서비스({name})가 활성화되어 있지 않습니다. "
                "팔 드라이버 기동 상태를 확인하세요."
            )

        request = SetBool.Request()
        request.data = True
        future = client.call_async(request)
        wait_for_future_sync(
            self._node,
            future,
            float(self._config.agx_motion_timeout_sec),
            f"{label} 서비스({name}) 호출 타임아웃 ({float(self._config.agx_motion_timeout_sec):.1f}초)",
            error_cls=ManipulationError,
        )
        response = future.result()
        if response is not None and getattr(response, "success", True) is False:
            detail = str(getattr(response, "message", "") or "").strip()
            suffix = f": {detail}" if detail else ""
            raise ManipulationError(f"AGX {label} 실패({name}){suffix}")

    def _ensure_driver_ready(self) -> None:
        if self._driver_ready:
            return
        self._call_set_bool_service(
            "_enable_client",
            getattr(self._config, "agx_enable_service", "enable_agx_arm"),
            "모터 활성화",
        )
        self._call_set_bool_service(
            "_control_enable_client",
            getattr(self._config, "agx_control_enable_service", "control_enable"),
            "외부 제어 게이트 활성화",
        )
        self._driver_ready = True

    # ── 피드백 ──

    def _log_info(self, msg: str) -> None:
        try:
            self._node.get_logger().info(msg)
        except Exception:
            logger.info(msg)

    def _on_joint_state(self, msg: JointState) -> None:
        self._latest_joint_state = msg
        self._joint_feedback_seen = True
        joint_map = dict(zip(msg.name, msg.position, strict=False))
        g_val = float(joint_map.get("gripper") or 0.0)
        j1_val = float(joint_map.get("joint1") or 0.0)
        self._log_info(f"AGX feedback: gripper={g_val:.6f}, joint1={j1_val:.3f}")

    def _on_tcp_pose(self, msg: PoseStamped) -> None:
        self._latest_tcp_pose = msg
        self._tcp_feedback_seen = True

    def _spin_once(self) -> None:
        # agent_node 는 MultiThreadedExecutor 에서 실행되므로 rclpy.spin_once()를
        # 직접 호출하면 C++ RMW 레벨에서 데드락이 발생할 수 있다.
        # sleep 으로 백그라운드 executor 가 콜백을 처리하도록 유도한다.
        time.sleep(0.05)

    def _wait_for_feedback(self, feedback_seen: Any, label: str) -> None:
        start = time.monotonic()
        feedback_deadline = start + float(self._config.agx_feedback_timeout_sec)
        while not feedback_seen():
            if time.monotonic() > feedback_deadline:
                self._driver_ready = False
                raise ManipulationError(
                    f"{label}: 팔 피드백을 받지 못했습니다. 드라이버 기동, CAN, "
                    f"ROS_DOMAIN_ID/RMW 설정을 확인하세요."
                )
            self._spin_once()

    def _wait_until(
        self,
        predicate: Any,
        label: str,
        feedback_seen: Any,
        republish_fn: Any = None,
    ) -> None:
        """목표 수렴까지 대기한다. 피드백/목표 도달 실패는 예외로 알린다."""
        start = time.monotonic()
        motion_deadline = start + float(self._config.agx_motion_timeout_sec)
        feedback_deadline = start + float(self._config.agx_feedback_timeout_sec)
        last_publish = start
        last_log = start

        while True:
            if predicate():
                self._log_info(f"{label}: 목표 도달 완료 ({time.monotonic() - start:.2f}s 경과)")
                return

            now = time.monotonic()
            if not feedback_seen() and now > feedback_deadline:
                self._driver_ready = False
                raise ManipulationError(
                    f"{label}: 팔 피드백을 받지 못했습니다. 드라이버 기동, CAN, "
                    f"ROS_DOMAIN_ID/RMW 설정을 확인하세요."
                )
            if now > motion_deadline:
                self._driver_ready = False
                raise ManipulationError(
                    f"{label}: {float(self._config.agx_motion_timeout_sec):.1f}초 내에 "
                    f"목표에 도달하지 못했습니다."
                )
            if republish_fn is not None and (now - last_publish) >= _REPUBLISH_INTERVAL_SEC:
                republish_fn()
                last_publish = now
            if (now - last_log) >= 1.0:
                cur_gripper = (
                    dict(
                        zip(
                            self._latest_joint_state.name,
                            self._latest_joint_state.position,
                            strict=False,
                        )
                    ).get("gripper")
                    if self._latest_joint_state
                    else None
                )
                self._log_info(
                    f"{label} 대기 중: elapsed={now - start:.1f}s, current_gripper={cur_gripper}"
                )
                last_log = now
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
        if joint_map:
            self.move_to_joint_target(group_name, joint_map)
            return

        # 드라이버가 제공하는 home 루틴을 사용한다. SRDF group_state 를 흉내내기 위해
        # 조인트 값을 임의로 만들지 않는다(실기 자세 보장 불가).
        if str(target_name).strip() == "home":
            self.call_home_service()
            return

        raise ManipulationConfigError(
            f"AGX 백엔드는 named pose '{target_name}' 의 조인트 값을 알 수 없습니다. "
            "manipulation_named_poses_json 에 조인트 맵을 정의하거나 home 을 사용하세요."
        )

    def call_home_service(self) -> None:
        """드라이버의 home 서비스(std_srvs/Empty)를 호출한다."""
        self._ensure_driver_ready()
        service_name = str(self._config.agx_home_service or "move_home").strip()
        if not service_name:
            raise ManipulationConfigError("manipulation_agx_home_service 가 비어 있습니다.")

        try:
            if self._home_client is None:
                client_kwargs: dict[str, Any] = {}
                if self._cb_group is not None:
                    client_kwargs["callback_group"] = self._cb_group
                try:
                    self._home_client = self._node.create_client(
                        Empty, service_name, **client_kwargs
                    )
                except TypeError:
                    self._home_client = self._node.create_client(Empty, service_name)
        except Exception as exc:  # noqa: BLE001 - 서비스 클라이언트 생성 실패
            raise ManipulationRuntimeUnavailableError(
                f"AGX home 서비스 클라이언트 생성 실패({service_name}): {exc}"
            ) from exc

        if not self._home_client.wait_for_service(timeout_sec=5.0):
            raise ManipulationRuntimeUnavailableError(
                f"AGX home 서비스({service_name})가 활성화되어 있지 않습니다. "
                "팔 드라이버 기동 상태를 확인하세요."
            )

        future = self._home_client.call_async(Empty.Request())
        wait_for_future_sync(
            self._node,
            future,
            float(self._config.agx_motion_timeout_sec),
            f"home 서비스 호출 타임아웃 ({float(self._config.agx_motion_timeout_sec):.1f}초)",
            error_cls=ManipulationError,
        )

    def move_to_joint_target(self, group_name: str, joint_values: Mapping[str, float]) -> None:
        del group_name  # 드라이버는 MoveIt planning group 개념이 없다

        self._ensure_driver_ready()
        self._wait_for_feedback(lambda: self._joint_feedback_seen, "joint 목표")

        targets = {
            str(name).strip(): float(value)
            for name, value in joint_values.items()
            if str(name).strip()
        }
        if not targets:
            raise ManipulationConfigError("joint target 이 비어 있습니다.")

        # targets 가 오직 그리퍼(gripper)만 포함하는 경우, 드라이버가 불필요한
        # 팔 move_j 를 수행하지 않도록 gripper 만 발행한다.
        # 팔 관절이 포함된 경우에만 드라이버의 생략 관절 0.0 보정을 위해
        # 현재 피드백의 팔 관절값을 유지한다.
        is_gripper_only = all("gripper" in name.lower() for name in targets)
        cmd_positions: dict[str, float] = {}
        if not is_gripper_only and self._latest_joint_state is not None:
            for name, pos in zip(
                self._latest_joint_state.name,
                self._latest_joint_state.position,
                strict=False,
            ):
                cmd_positions[str(name).strip()] = float(pos)
        cmd_positions.update(targets)

        msg = JointState()
        msg.name = list(cmd_positions.keys())
        msg.position = [cmd_positions[name] for name in msg.name]
        self._move_joint_pub.publish(msg)

        self._wait_until(
            lambda: self._joints_reached(targets),
            "joint 목표",
            lambda: self._joint_feedback_seen,
            republish_fn=lambda: self._move_joint_pub.publish(msg),
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

        self._ensure_driver_ready()
        self._wait_for_feedback(lambda: self._tcp_feedback_seen, "pose 목표")

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
            republish_fn=lambda: publisher.publish(msg),
        )
