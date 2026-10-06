"""Stretch driver 직접 제어 백엔드.

MoveIt 대신 Hello Robot Stretch 의 자체 driver 노드가 제공하는 제어
인터페이스로 팔을 움직인다.

- Action: ``/stretch_controller/follow_joint_trajectory``
  (control_msgs/FollowJointTrajectory, position 모드 단일 point)
- Service: ``/home_the_robot``, ``/stow_the_robot`` (std_srvs/Trigger)

MuJoCo 시뮬(stretch_mujoco_driver)과 실기 stretch_driver 가 동일 인터페이스를
제공하므로 이 백엔드 하나로 양쪽을 모두 다룬다.

본 파일은 1단계(joint 기반 제어) 까지 구현한다. ``move_to_pose_target`` 은
2단계에서 직교 기하학 IK 로 채운다.
"""

import logging
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import rclpy
from builtin_interfaces.msg import Duration
from control_msgs.action import FollowJointTrajectory
from geometry_msgs.msg import Twist
from sensor_msgs.msg import JointState
from std_srvs.srv import Trigger
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint

from ._ros_sync import wait_for_future_sync
from .exceptions import (
    ManipulationConfigError,
    ManipulationContactError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)
from .math import rpy_from_quaternion
from .stretch_kinematics import (
    _WRIST_PITCH_OFFSET,
    _WRIST_PITCH_SCALE,
    _WRIST_ROLL_OFFSET,
    _WRIST_ROLL_SCALE,
    _check_exclusive,
    _fk_full,
    _fk_position,
    _ik_position,
    _validate_joint_names,
    compute_align_rotation_from_bearing_rad,
    compute_align_rotation_rad,
    compute_centering_rotation_rad,
)
from .types import ManipulationConfig, PoseGoal

logger = logging.getLogger(__name__)

# 순수 키네마틱/검증 심볼 재노출 — 기존 호출자/테스트(`sb._fk_position`,
# `sb._validate_joint_names` 등) 호환. 실제 구현은 stretch_kinematics.py.
__all__ = [
    "StretchDriverBackend",
    "_validate_joint_names",
    "_check_exclusive",
    "_build_goal",
    "_fk_full",
    "_fk_position",
    "_ik_position",
    "check_reachable",
    "compute_align_rotation_rad",
    "compute_align_rotation_from_bearing_rad",
    "compute_centering_rotation_rad",
]


def check_reachable(xyz: Sequence[float]) -> bool:
    """base_link 기준 xyz 위치가 회전 없이 현재 자세에서 IK로 도달 가능한지 확인.

    ``_ik_position``의 얇은 bool 래퍼. ``compute_align_rotation_rad``와 함께
    "회전 없이 되는지 → 안 되면 얼마나 회전해야 하는지"를 스킬 계층에서 조합해 쓴다.
    """
    return _ik_position(xyz) is not None


# ── 제어 인터페이스 이름 ──
_ACTION_NAME = "/stretch_controller/follow_joint_trajectory"
_SWITCH_POSITION_SERVICE = "/switch_to_position_mode"
_SWITCH_NAVIGATION_SERVICE = "/switch_to_navigation_mode"
# home/stow 는 stretch_driver 의 Trigger 서비스로 직접 매핑.
_NAMED_SERVICE = {
    "home": "/home_the_robot",
    "stow": "/stow_the_robot",
}


def _result_error_names() -> dict[int, str]:
    """FollowJointTrajectory.Result error_code → 사람 읽기 이름.

    control_msgs 배포판에 따라 OLD_HEADER 계열 상수명이 다르다.
    존재하지 않는 상수를 직접 참조하면 모듈 import 자체가 실패하므로 런타임에
    있는 상수만 매핑한다.
    """
    names = (
        "SUCCESSFUL",
        "INVALID_GOAL",
        "INVALID_JOINTS",
        "OLD_HEADER_TIMING_OUT",
        "OLD_HEADER_TIMESTAMP",
        "PATH_TOLERANCE_VIOLATED",
        "GOAL_TOLERANCE_VIOLATED",
    )
    result_type = FollowJointTrajectory.Result
    return {int(getattr(result_type, name)): name for name in names if hasattr(result_type, name)}


_RESULT_ERROR_NAMES = _result_error_names()

# 동기 대기 기본 타임아웃
_GOAL_TIMEOUT_SEC = 20.0
_SERVICE_TIMEOUT_SEC = 30.0
_SERVER_WAIT_SEC = 5.0

# stretch_core joint_trajectory_server 가 guarded contact 감지 시 반환하는
# 비표준 error_code (FollowJointTrajectory.Result 상수가 아님). 이때
# error_string 은 "X contact detected." 형태다.
_CONTACT_DETECTED_ERROR_CODE = 100
_CONTACT_DETECTED_MARKER = "contact detected"

# /stow_the_robot의 서비스 응답만으로는 실제 관절 도달을 알 수 없다.
# 주행 안전 판정은 footprint를 벗어나기 쉬운 lift/extension/yaw를 확인한다.
_STOW_MAX_EXTENSION_M = 0.08
_STOW_LIFT_RANGE_M = (0.08, 0.35)
_STOW_MAX_YAW_RAD = 0.60
_JOINT_STATE_MAX_AGE_SEC = 1.0

# ── FK / 수치 IK / joint 검증 / 자세 매핑 상수는 stretch_kinematics.py 로 분리 ──


def _build_goal(joint_values: Mapping[str, float]) -> FollowJointTrajectory.Goal:
    """FollowJointTrajectory 단일 point goal 빌드. position 모드용.

    time_from_start=0 (position 모드는 즉시 move_by 실행),
    goal_time_tolerance=1s.
    """
    goal = FollowJointTrajectory.Goal()
    traj = JointTrajectory()
    traj.joint_names = list(joint_values.keys())
    point = JointTrajectoryPoint()
    point.positions = [float(v) for v in joint_values.values()]
    point.time_from_start = Duration(sec=0, nanosec=0)
    traj.points = [point]
    goal.trajectory = traj
    goal.goal_time_tolerance = Duration(sec=1, nanosec=0)
    # goal_tolerance / path_tolerance 는 빈 리스트(기본값)로 둔다.
    # stretch joint_trajectory_server(position 모드)는 tolerance 를 사용하지 않고,
    # 명시적으로 0.0 을 채우면 GOAL_TOLERANCE_VIOLATED 가 발생할 수 있다.
    return goal


class StretchDriverBackend:
    """stretch_driver 노드 인터페이스 기반 manipulation 백엔드.

    ManipulationBackend Protocol 의 3 메서드를 구현한다.
    execute_gripper_preset 은 runtime 이 move_to_joint_target 으로 위임하므로
    본 백엔드에는 별도 진입점이 없다.
    """

    def __init__(self, node: Any, config: ManipulationConfig) -> None:
        self._owner_node = node
        self._node = self._create_control_node()
        self._config = config
        self._joint_positions: dict[str, float] = {}
        self._joint_state_monotonic = 0.0
        self._joint_state_lock = threading.Lock()
        self._joint_state_subscription = self._node.create_subscription(
            JointState, "/joint_states", self._on_joint_state, 10
        )

        try:
            self._action_client = rclpy.action.ActionClient(
                self._node, FollowJointTrajectory, _ACTION_NAME
            )
        except Exception as exc:  # noqa: BLE001 - ROS 초기화 계열 예외 폭넓게
            raise ManipulationRuntimeUnavailableError(
                f"FollowJointTrajectory action client 생성 실패: {exc}"
            ) from exc

        self._service_clients: dict[str, Any] = {
            alias: self._node.create_client(Trigger, service_name)
            for alias, service_name in _NAMED_SERVICE.items()
        }
        self._switch_position_client = self._node.create_client(Trigger, _SWITCH_POSITION_SERVICE)
        self._switch_navigation_client = self._node.create_client(
            Trigger, _SWITCH_NAVIGATION_SERVICE
        )
        self._cmd_vel_pub = None
        if self._config.cmd_vel_topic:
            self._cmd_vel_pub = self._node.create_publisher(Twist, self._config.cmd_vel_topic, 10)

    def _on_joint_state(self, message: JointState) -> None:
        positions = {
            str(name): float(position)
            for name, position in zip(message.name, message.position, strict=False)
        }
        with self._joint_state_lock:
            self._joint_positions.update(positions)
            self._joint_state_monotonic = time.monotonic()

    def is_stowed(self) -> bool:
        """현재 joint state가 주행 안전 footprint 안인지 반환한다.

        상태가 없거나 오래된 경우에는 안전하지 않은 것으로 판정한다.
        /stow_the_robot 서비스의 성공 응답을 실제 자세 도달로 오인하지 않기
        위해 주행 직전 이 값을 다시 확인한다.
        """
        with self._joint_state_lock:
            positions = dict(self._joint_positions)
            state_time = self._joint_state_monotonic

        if not positions or state_time <= 0.0:
            return False
        if time.monotonic() - state_time > _JOINT_STATE_MAX_AGE_SEC:
            return False

        extension = positions.get("wrist_extension")
        lift = positions.get("joint_lift")
        if extension is None or lift is None:
            return False
        if extension < -0.01 or extension > _STOW_MAX_EXTENSION_M:
            return False
        if not _STOW_LIFT_RANGE_M[0] <= lift <= _STOW_LIFT_RANGE_M[1]:
            return False

        yaw = positions.get("joint_wrist_yaw")
        return yaw is None or abs(yaw) <= _STOW_MAX_YAW_RAD

    def _create_control_node(self) -> Any:
        """Agent executor wait set과 분리된 Stretch 제어 전용 node 생성."""
        try:
            return rclpy.create_node(f"robo_claw_stretch_backend_{uuid.uuid4().hex[:12]}")
        except Exception as exc:  # noqa: BLE001 - ROS 초기화/컨텍스트 예외
            logger.warning(
                "Failed to create dedicated Stretch control node, falling back to agent node: %s",
                exc,
            )
            return self._owner_node

    # ── 동기 대기 헬퍼 ──

    def _log_info(self, message: str, *args: Any) -> None:
        try:
            self._node.get_logger().info(message % args if args else message)
        except Exception:  # noqa: BLE001 - logging fallback
            logger.info(message, *args)

    def _log_warning(self, message: str, *args: Any) -> None:
        try:
            self._node.get_logger().warning(message % args if args else message)
        except Exception:  # noqa: BLE001 - logging fallback
            logger.warning(message, *args)

    def _spin_until(self, future, timeout_sec: float, timeout_msg: str) -> None:
        """future.done() 까지 동기 대기. 공통 헬퍼로 위임.

        agent_node 가 MultiThreadedExecutor 를 쓰면 spin_once 가 차단되므로
        except 경로의 time.sleep 이 실제 주 경로가 된다.
        """
        try:
            rclpy.spin_until_future_complete(self._node, future, timeout_sec=timeout_sec)
        except Exception as exc:  # noqa: BLE001 - 환경별 executor fallback
            self._log_warning("future 대기 중 spin_until 실패, fallback 사용: %s", exc)
            wait_for_future_sync(
                self._node,
                future,
                timeout_sec,
                timeout_msg,
                error_cls=ManipulationError,
            )

        if not future.done():
            future.cancel()
            raise ManipulationError(timeout_msg)

    def _send_goal(self, goal: FollowJointTrajectory.Goal) -> None:
        self._log_info("Stretch trajectory action 서버 대기 시작: %s", _ACTION_NAME)
        if not self._action_client.wait_for_server(timeout_sec=_SERVER_WAIT_SEC):
            raise ManipulationRuntimeUnavailableError(
                f"action 서버 {_ACTION_NAME} 가 {_SERVER_WAIT_SEC}s 내에 활성화되지 않았습니다. "
                "stretch_driver / stretch_mujoco_driver 가 실행 중인지 확인하세요."
            )

        self._log_info(
            "Stretch trajectory goal 전송: joints=%s",
            list(goal.trajectory.joint_names),
        )
        send_goal_future = self._action_client.send_goal_async(goal)
        self._spin_until(
            send_goal_future,
            _GOAL_TIMEOUT_SEC,
            f"trajectory goal 수락 대기 타임아웃({_GOAL_TIMEOUT_SEC}s)",
        )

        goal_handle = send_goal_future.result()
        if goal_handle is None:
            raise ManipulationError("trajectory goal 응답을 받지 못했습니다.")
        if not goal_handle.accepted:
            raise ManipulationError("stretch driver 가 trajectory goal 을 거부했습니다.")

        self._log_info("Stretch trajectory goal 수락 완료")
        get_result_future = goal_handle.get_result_async()
        self._log_info("Stretch trajectory result 대기 시작")
        try:
            self._spin_until(
                get_result_future,
                _GOAL_TIMEOUT_SEC,
                f"trajectory 실행 타임아웃({_GOAL_TIMEOUT_SEC}s)",
            )
        except ManipulationError:
            # result future 취소만으로는 이미 수락된 hardware goal이 멈추지 않는다.
            # 실제 action goal을 취소한 뒤 원래 timeout/failure를 다시 전파한다.
            try:
                cancel_future = goal_handle.cancel_goal_async()
                self._spin_until(
                    cancel_future,
                    min(5.0, _GOAL_TIMEOUT_SEC),
                    "trajectory goal 취소 응답 타임아웃",
                )
                self._log_warning("trajectory timeout 후 실제 goal 취소를 요청했습니다.")
            except Exception as cancel_exc:  # noqa: BLE001
                self._log_warning("trajectory goal 취소 실패: %s", cancel_exc)
            raise

        wrapped = get_result_future.result()
        result = getattr(wrapped, "result", wrapped)
        if result is None:
            raise ManipulationError("trajectory 실행 결과를 받지 못했습니다.")

        error_code = getattr(result, "error_code", None)
        error_string = str(getattr(result, "error_string", "") or "")
        contact_detected = self._is_contact_detected(error_code, error_string)
        if contact_detected:
            name = "CONTACT_DETECTED"
        else:
            name = _RESULT_ERROR_NAMES.get(error_code, str(error_code))
        self._log_info(
            "Stretch trajectory result 수신: error_code=%s (%s), error_string=%s",
            error_code,
            name,
            error_string,
        )
        if error_code != FollowJointTrajectory.Result.SUCCESSFUL:
            if contact_detected:
                # guarded motion 이 contact 로 trajectory 를 조기 중단한 경우.
                # goal 거부/tolerance 위반이 아니라 "접촉 지점에서 멈춤"이므로
                # 별도 예외로 구분한다. 준비자세 컨텍스트에서는 tolerate_contact
                # 로 수용하고, 그 외에서는 일반 실패로 전파된다(서브클래스).
                raise ManipulationContactError(
                    f"contact detected 로 trajectory 중단: error_code={error_code} "
                    f"({name}), error_string={error_string}"
                )
            raise ManipulationError(
                f"trajectory 실행 실패: error_code={error_code} ({name}), error_string={error_string}"
            )

    @staticmethod
    def _is_contact_detected(error_code: Any, error_string: str) -> bool:
        """stretch_driver 가 guarded contact 로 goal 을 중단시켰는지 판별.

        stretch_core joint_trajectory_server 는 contact 시 비표준 error_code=100
        과 "X contact detected." error_string 을 반환한다. error_code 만으로
        단정하면 stretch_core 상수 변경에 취약하므로 error_string 도 함께 본다.
        """
        if _CONTACT_DETECTED_MARKER in error_string.lower():
            return True
        try:
            return int(error_code) == _CONTACT_DETECTED_ERROR_CODE
        except (TypeError, ValueError):
            return False

    def _call_trigger_service(self, client: Any, service_name: str, timeout_sec: float) -> Any:
        self._log_info("Stretch 서비스 호출 시작: %s", service_name)
        if not client.wait_for_service(timeout_sec=_SERVER_WAIT_SEC):
            raise ManipulationRuntimeUnavailableError(
                f"서비스 {service_name} 가 {_SERVER_WAIT_SEC}s 내에 활성화되지 않았습니다."
            )
        future = client.call_async(Trigger.Request())
        self._spin_until(
            future,
            timeout_sec,
            f"서비스 {service_name} 호출 타임아웃({timeout_sec}s)",
        )
        res = future.result()
        if res is None:
            raise ManipulationError(f"서비스 {service_name} 응답을 받지 못했습니다.")
        if not res.success:
            raise ManipulationError(f"서비스 {service_name} 가 실패를 반환했습니다.")
        self._log_info("Stretch 서비스 호출 완료: %s", service_name)
        return res

    def _publish_stop_twist(self) -> None:
        if self._cmd_vel_pub is None:
            return
        try:
            self._cmd_vel_pub.publish(Twist())
            time.sleep(0.05)
        except Exception as exc:  # noqa: BLE001 - 정지 보조 실패는 치명 아님
            self._log_warning("cmd_vel 정지 발행 실패: %s", exc)

    def _switch_to_position_mode(self) -> None:
        self._publish_stop_twist()
        self._call_trigger_service(
            self._switch_position_client,
            _SWITCH_POSITION_SERVICE,
            timeout_sec=_SERVER_WAIT_SEC,
        )
        self._log_info("Stretch driver position 모드 전환 완료")

    def _restore_navigation_mode(self) -> None:
        self._call_trigger_service(
            self._switch_navigation_client,
            _SWITCH_NAVIGATION_SERVICE,
            timeout_sec=_SERVER_WAIT_SEC,
        )
        self._log_info("Stretch driver navigation 모드 복귀 완료")

    def _run_in_position_mode(self, action_name: str, action) -> None:
        self._log_info("Stretch position 작업 시작: %s", action_name)
        self._switch_to_position_mode()
        try:
            action()
        finally:
            self._restore_navigation_mode()
            self._log_info("Stretch position 작업 종료: %s", action_name)

    # ── ManipulationBackend 구현 ──

    def move_to_joint_target(self, group_name: str, joint_values: Mapping[str, float]) -> None:
        del group_name  # Stretch 는 단일 joint 그룹 (이름 무의미)

        normalized = {str(name): float(value) for name, value in joint_values.items()}

        node = getattr(self, "_node", None)
        robot_limits = getattr(node, "_robot_limits_dict", None) if node is not None else None
        if robot_limits:
            from robo_claw_agent.skills.limits import check_joint_targets

            limit_error = check_joint_targets(normalized, robot_limits)
            if limit_error:
                raise ManipulationError(f"trajectory limits 위반: {limit_error}")

        err = _validate_joint_names(normalized)
        if err is not None:
            raise ManipulationError(err)
        err = _check_exclusive(list(normalized.keys()))
        if err is not None:
            raise ManipulationError(err)

        goal = _build_goal(normalized)
        self._run_in_position_mode(
            f"trajectory:{','.join(normalized.keys())}",
            lambda: self._send_goal(goal),
        )

    def move_to_named_pose(self, group_name: str, target_name: str) -> None:
        # home/stow → stretch_driver Trigger 서비스
        if target_name in _NAMED_SERVICE:
            client = self._service_clients[target_name]
            service_name = _NAMED_SERVICE[target_name]
            self._run_in_position_mode(
                f"named_pose:{target_name}",
                lambda: self._call_trigger_service(client, service_name, _SERVICE_TIMEOUT_SEC),
            )
            return

        # 그 외 named pose → 사전정의된 joint 값으로 이동
        joint_map = self._config.named_pose_joint_values.get(target_name)
        if joint_map is None:
            raise ManipulationConfigError(
                f"named pose '{target_name}' 의 joint 값이 설정에 정의되지 않았습니다. "
                "manipulation_named_poses_json 에 해당 pose 의 joint 맵을 추가하거나 "
                "home/stow 중 하나를 사용하세요."
            )
        self.move_to_joint_target(group_name, joint_map)

    def move_to_pose_target(
        self,
        group_name: str,
        pose: PoseGoal,
        end_effector_link: str,
        cartesian: bool = False,
    ) -> None:
        del end_effector_link, cartesian
        # TF 변환은 미구현. base_frame 기준 pose 만 지원.
        if pose.frame_id != self._config.base_frame:
            raise ManipulationConfigError(
                f"frame_id '{pose.frame_id}' 미지원. Stretch 백엔드는 base_frame "
                f"'{self._config.base_frame}' 기준 pose 만 지원합니다 "
                "(TF 변환은 후속 단계)."
            )

        x = float(pose.position["x"])
        y = float(pose.position["y"])
        z = float(pose.position["z"])

        sol = _ik_position((x, y, z))
        if sol is None:
            raise ManipulationError(
                f"도달 불가능한 pose 목표: ({x:.3f},{y:.3f},{z:.3f}). "
                "arm 가동 범위 밖이거나 수치 IK 가 수렴하지 않았습니다."
            )
        lift, ext, yaw = sol

        # orientation 근사 매핑: target quaternion -> RPY -> wrist pitch/roll.
        # position IK 가 joint_wrist_yaw 를 결정하므로 RPY yaw 성분은 무시.
        # EE 기본 orientation 이 비표준(mast 회전)이라 스케일/부호는 검증(TF echo)으로 조정.
        roll, pitch, _ = rpy_from_quaternion(pose.orientation)
        joints = {
            "joint_lift": float(lift),
            "wrist_extension": float(ext),
            "joint_wrist_yaw": float(yaw),
            "joint_wrist_pitch": float(-pitch * _WRIST_PITCH_SCALE + _WRIST_PITCH_OFFSET),
            "joint_wrist_roll": float(roll * _WRIST_ROLL_SCALE + _WRIST_ROLL_OFFSET),
        }
        self.move_to_joint_target(group_name, joints)
