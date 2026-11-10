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
import math
import time
from collections.abc import Mapping
from typing import Any

from geometry_msgs.msg import PoseStamped
from sensor_msgs.msg import JointState
from std_srvs.srv import Empty, SetBool

from ._ros_sync import wait_for_future_sync
from .agx_health import (
    ARM_STATUS_NO_SOLUTION,
    diagnose_arm_status,
    diagnose_motion_faults,
    diagnose_motor_status,
)
from .exceptions import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)
from .types import MotionOutcome

logger = logging.getLogger(__name__)

_POSITION_KEYS = ("x", "y", "z")
_REPUBLISH_INTERVAL_SEC = 0.5
#: joint_states 피드백 로그 간격. 드라이버는 수십~수백 Hz 로 피드백을 발행하므로
#: 콜백마다 INFO 를 남기면 로그가 폭주한다.
_FEEDBACK_LOG_INTERVAL_SEC = 10.0
#: /move_home 이 이동시키는 목표 자세(agx_arm_ctrl 의 move_j([0]*arm_joint_count)).
#: 드라이버 home 은 "수직 zero 자세" 이며, 이 백엔드는 서비스 응답만으로 성공을
#: 선언하지 않고 이 자세에 실제로 수렴했는지 확인한다.
_HOME_JOINT_TARGET = 0.0
_GRIPPER_KEYWORD = "gripper"
#: 모터 진단 토픽 기본값. ``ManipulationConfig`` 에 필드가 없으면 이 값을 쓴다(새 ROS
#: 파라미터를 만들지 않기 위해 getattr 기본값으로만 노출한다).
_DEFAULT_MOTOR_STATUS_TOPIC = "feedback/motor_status"


class AgxDriverBackend:
    """AGX 팔 드라이버 토픽 기반 백엔드"""

    def __init__(self, node: Any, config: Any) -> None:
        self._node = node
        self._config = config
        self._latest_joint_state: JointState | None = None
        self._latest_tcp_pose: PoseStamped | None = None
        self._latest_arm_status: Any = None
        self._latest_motor_status: Any = None
        self._joint_feedback_seen = False
        self._tcp_feedback_seen = False
        #: 피드백 수신 시각(monotonic). 오래된 샘플을 "목표 도달" 로 오판하지 않기 위해 쓴다.
        self._joint_state_time: float | None = None
        self._tcp_pose_time: float | None = None
        #: 진단 샘플 수신 순번. 이동 시작 시점 이후에 도착한 샘플만 이번 명령의 실패로 본다.
        self._arm_status_seq = 0
        self._motor_status_seq = 0
        self._home_client = None
        self._enable_client = None
        self._control_enable_client = None
        self._driver_ready = False
        self._last_feedback_log_time: float | None = None
        #: 같은 IK 경고(NO_SOLUTION)를 반복 출력하지 않기 위한 플래그.
        self._no_solution_warned = False
        #: 마지막 이동의 사후 검증 결과(스킬 계층이 "이동 완료" 와 "이미 그 자세" 를 구분).
        self.last_motion: MotionOutcome | None = None

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
                self._create_diagnostics_subscriptions(node, config, sub_kwargs)
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
                self._create_diagnostics_subscriptions(node, config, {})
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
        self._joint_state_time = time.monotonic()
        self._joint_feedback_seen = True
        joint_map = dict(zip(msg.name, msg.position, strict=False))
        g_val = float(joint_map.get("gripper") or 0.0)
        j1_val = float(joint_map.get("joint1") or 0.0)
        self._log_feedback_sample(g_val, j1_val)

    def _log_feedback_sample(self, g_val: float, j1_val: float) -> None:
        """피드백 샘플을 일정 간격으로만 남긴다."""
        now = time.monotonic()
        last = self._last_feedback_log_time
        if last is not None and now - last < _FEEDBACK_LOG_INTERVAL_SEC:
            return
        self._last_feedback_log_time = now
        self._log_info(f"AGX feedback: gripper={g_val:.6f}, joint1={j1_val:.3f}")

    def _on_tcp_pose(self, msg: PoseStamped) -> None:
        self._latest_tcp_pose = msg
        self._tcp_pose_time = time.monotonic()
        self._tcp_feedback_seen = True

    def _create_diagnostics_subscriptions(
        self, node: Any, config: Any, sub_kwargs: dict[str, Any]
    ) -> None:
        """드라이버 진단(팔/모터 상태) 구독. 진단을 발행하지 않는 환경에서는 생략된다."""
        self._create_diagnostic_subscription(
            node,
            str(getattr(config, "agx_arm_status_topic", "") or ""),
            self._on_arm_status,
            sub_kwargs,
            "arm_status",
        )
        self._create_diagnostic_subscription(
            node,
            str(getattr(config, "agx_motor_status_topic", _DEFAULT_MOTOR_STATUS_TOPIC) or ""),
            self._on_motor_status,
            sub_kwargs,
            "motor_status",
        )

    def _create_diagnostic_subscription(
        self, node: Any, topic: str, callback: Any, sub_kwargs: dict[str, Any], label: str
    ) -> None:
        """DiagnosticArray 구독 하나를 생성한다. 실패해도 이동 기능은 유지한다."""
        name = topic.strip()
        if not name:
            return
        try:
            from diagnostic_msgs.msg import DiagnosticArray
        except Exception:  # noqa: BLE001 - 진단 구독은 없어도 동작해야 한다
            logger.warning("diagnostic_msgs 를 사용할 수 없어 %s 감시를 생략합니다.", label)
            return
        try:
            node.create_subscription(DiagnosticArray, name, callback, 10, **sub_kwargs)
        except Exception as exc:  # noqa: BLE001 - 진단 구독 실패는 치명적이지 않다
            logger.warning("%s 구독 생성 실패(%s): %s", label, name, exc)

    def _on_arm_status(self, msg: Any) -> None:
        self._latest_arm_status = msg
        self._arm_status_seq += 1

    def _on_motor_status(self, msg: Any) -> None:
        self._latest_motor_status = msg
        self._motor_status_seq += 1

    def _raise_if_arm_fault(self, label: str, *, require_ik: bool = False) -> None:
        """드라이버가 ERROR 진단을 보낸 상태면 팔 명령을 즉시 거부한다.

        agx_arm_ctrl 은 IK 실패(NO_SOLUTION 등)를 서비스 응답으로 알려주지 않고
        /feedback/arm_status 로만 노출한다. 이 검사가 없으면 명령은 조용히 무시되고
        30초 뒤 "목표에 도달하지 못했다" 만 보고된다.

        ``arm_status=NO_SOLUTION(0x02)`` 이고 ``err_status`` 가 0/없는 엔트리는 직전 pose
        명령의 흔적이므로(`allow_no_solution`) 조인트/홈 이동에서만 무시하고 경고를 남긴다.
        판정은 엔트리 단위라, 같은 배열의 다른 엔트리에 통신/브레이크 오류가 있으면 거부한다.
        ``SINGULARITY_POINT``/``TARGET_POS_EXCEEDS_LIMIT`` 는 복구 경로가 검증되지 않아
        면제하지 않는다.
        """
        diagnosis = diagnose_arm_status(self._latest_arm_status, allow_no_solution=not require_ik)
        entry = diagnosis.blocking
        if entry is not None:
            detail = f"{entry.name}: {entry.message or '상세 없음'} [{entry.summary()}]"
            if require_ik and entry.arm_status == ARM_STATUS_NO_SOLUTION:
                raise ManipulationError(
                    f"{label}: 팔이 자세(IK) 목표를 해석하지 못한 상태입니다({detail}). "
                    "pose 대신 조인트(move_joints)나 정의된 프리셋(arm_pose)으로 다시 요청하세요."
                )
            raise ManipulationError(
                f"{label}: 팔 드라이버 상태 오류({detail}). "
                "드라이버 로그와 팔 전원/비상정지 상태를 확인하고, 오류가 해소된 뒤 다시 요청하세요."
            )
        if diagnosis.tolerated_joint_no_solution and not self._no_solution_warned:
            self._no_solution_warned = True
            logger.warning(
                "%s: 팔 상태 경고(NO_SOLUTION) - IK 를 쓰지 않는 이동은 계속 진행합니다.",
                label,
            )

    def _raise_if_motor_fault(self, label: str) -> None:
        """모터 진단에 실제 오류가 있으면 활성화 전에 거부한다.

        충돌/과전류/스톨/드라이버 오류만 본다. 모터가 아직 꺼져 있는 것은 활성화 전
        상태이므로 거부 사유가 아니다(구동 중 꺼짐은 ``_raise_if_new_diagnostics`` 가 본다).
        모터 진단을 한 번도 받지 못한 환경에서는 아무 것도 하지 않는다.
        """
        motor = diagnose_motor_status(self._latest_motor_status)
        if not motor.faults and not motor.detail:
            return
        raise ManipulationError(
            f"{label}: 팔 모터 오류({motor.describe()}) 상태라 모터를 활성화하지 않았습니다. "
            "충돌/과전류/스톨 여부와 드라이버 로그를 확인한 뒤 다시 요청하세요."
        )

    def _diagnostics_baseline(self) -> tuple[int, int]:
        """이동 시작 시점의 진단 샘플 순번. 이후 도착한 샘플만 이번 명령의 실패로 본다."""
        return (self._arm_status_seq, self._motor_status_seq)

    def _raise_if_new_diagnostics(
        self, label: str, baseline: tuple[int, int], *, allow_no_solution: bool
    ) -> None:
        """이동이 시작된 뒤 도착한 진단이 실패/모터 이상을 보고하면 즉시 중단한다.

        같은 명령을 계속 재발행하면 드라이버가 거부하는 상황에서 로그와 모션 요청만
        반복되므로, 새 샘플이 실패를 알리는 순간 대기를 끊고 실패로 보고한다.
        """
        arm_baseline, motor_baseline = baseline
        if self._arm_status_seq > arm_baseline:
            entry = diagnose_motion_faults(
                self._latest_arm_status, allow_no_solution=allow_no_solution
            )
            if entry is not None:
                raise ManipulationError(
                    f"{label}: 이동 중 팔 상태 오류({entry.name}: "
                    f"{entry.message or '상세 없음'} [{entry.summary()}]). "
                    "같은 명령을 반복 발행하지 않고 중단합니다. 이 필드만으로 전원이나 "
                    "비상정지 문제를 단정하지 말고 목표 미수렴과 드라이버 로그를 확인하세요."
                )
        if self._motor_status_seq > motor_baseline:
            motor = diagnose_motor_status(self._latest_motor_status)
            if motor.faults or motor.disabled or motor.detail:
                raise ManipulationError(
                    f"{label}: 이동 중 모터 상태 이상({motor.describe()}). "
                    "같은 명령을 반복 발행하지 않고 중단합니다. 모터 전원/충돌/과전류 "
                    "상태를 확인한 뒤 다시 요청하세요."
                )

    def _home_joint_targets(self) -> dict[str, float]:
        """드라이버 home(/move_home = move_j([0]*N)) 목표를 피드백 조인트 이름으로 만든다.

        그리퍼는 home 이동 대상이 아니므로 제외한다.
        """
        state = self._latest_joint_state
        if state is None:
            return {}
        return {
            str(name).strip(): _HOME_JOINT_TARGET
            for name in state.name
            if _GRIPPER_KEYWORD not in str(name).lower()
        }

    def _set_last_motion(self, *, already_at_target: bool, detail: str = "") -> None:
        self.last_motion = MotionOutcome(
            moved=not already_at_target,
            already_at_target=already_at_target,
            detail=detail,
        )

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
        *,
        diagnostics_baseline: tuple[int, int] | None = None,
        allow_no_solution: bool = True,
    ) -> None:
        """목표 수렴까지 대기한다. 피드백/목표 도달 실패는 예외로 알린다.

        이동이 시작된 뒤 도착한 드라이버 진단(``motion_status``/``err_status``/모터
        이상)은 목표 도달·재발행 판단보다 먼저 검사해, 실패가 확인되면 같은 명령을
        반복 발행하지 않고 즉시 중단한다.
        """
        start = time.monotonic()
        motion_deadline = start + float(self._config.agx_motion_timeout_sec)
        feedback_deadline = start + float(self._config.agx_feedback_timeout_sec)
        last_publish = start
        last_log = start
        if diagnostics_baseline is None:
            diagnostics_baseline = self._diagnostics_baseline()

        while True:
            self._raise_if_new_diagnostics(
                label, diagnostics_baseline, allow_no_solution=allow_no_solution
            )
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

    def _feedback_is_fresh(self, received_at: float | None) -> bool:
        """수신 시각이 ``agx_feedback_timeout_sec`` 이내인지 확인한다."""
        if received_at is None:
            return False
        return (time.monotonic() - received_at) <= float(self._config.agx_feedback_timeout_sec)

    def _joints_reached(self, targets: Mapping[str, float]) -> bool:
        """조인트 피드백이 목표에 도달했는지 판정한다.

        오래된 샘플은 도달로 인정하지 않는다(드라이버가 멈춘 뒤 캐시만 남은 경우
        "이동 완료" 로 오판하기 때문). NaN 은 ``abs(nan - target) > tol`` 이 False 라
        도달로 새어나가므로 유한수를 명시적으로 검사한다.
        """
        state = self._latest_joint_state
        if state is None or not self._feedback_is_fresh(self._joint_state_time):
            return False

        current = {
            str(name).strip(): float(position)
            for name, position in zip(state.name, state.position, strict=False)
        }
        tolerance = float(self._config.agx_joint_tolerance_rad)
        for joint_name, target in targets.items():
            if joint_name not in current:
                return False
            actual = current[joint_name]
            if not math.isfinite(actual) or not math.isfinite(target):
                return False
            if abs(actual - target) > tolerance:
                return False
        return True

    def _pose_reached(self, targets: Mapping[str, float]) -> bool:
        pose = self._latest_tcp_pose
        if pose is None or not self._feedback_is_fresh(self._tcp_pose_time):
            return False

        tolerance = float(self._config.agx_pose_tolerance_m)
        actual = {
            "x": pose.pose.position.x,
            "y": pose.pose.position.y,
            "z": pose.pose.position.z,
        }
        for key in _POSITION_KEYS:
            current = float(actual[key])
            target = float(targets[key])
            if not math.isfinite(current) or not math.isfinite(target):
                return False
            if abs(current - target) > tolerance:
                return False
        return True

    # ── 목표/명령 값 검증 ──

    def _finite_joint_values(self, label: str, values: Mapping[str, Any]) -> dict[str, float]:
        """조인트 목표를 float 로 바꾸고 유한수를 검사한다(활성화/발행 전에 호출)."""
        targets: dict[str, float] = {}
        for name, value in values.items():
            joint_name = str(name).strip()
            if not joint_name:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ManipulationConfigError(
                    f"{label} '{joint_name}' 값이 숫자가 아닙니다: {value!r}"
                ) from exc
            if not math.isfinite(numeric):
                raise ManipulationConfigError(
                    f"{label} '{joint_name}' 값이 유한한 수가 아닙니다: {value!r}"
                )
            targets[joint_name] = numeric
        return targets

    def _finite_pose_targets(self, pose: Any) -> dict[str, float]:
        targets: dict[str, float] = {}
        for key in _POSITION_KEYS:
            value = pose.position.get(key)
            if value is None:
                raise ManipulationConfigError(f"pose target 에 '{key}' 값이 없습니다.")
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ManipulationConfigError(
                    f"pose target '{key}' 값이 숫자가 아닙니다: {value!r}"
                ) from exc
            if not math.isfinite(numeric):
                raise ManipulationConfigError(
                    f"pose target '{key}' 값이 유한한 수가 아닙니다: {value!r}"
                )
            targets[key] = numeric
        return targets

    def _preserved_joint_command(self, targets: Mapping[str, float]) -> dict[str, float]:
        """생략된 관절을 최신 피드백 값으로 보존한 전체 명령을 만든다.

        드라이버가 생략된 관절을 0.0 으로 리셋하는 버그를 막기 위해 필요하다. 피드백에
        NaN/inf 가 섞여 있으면 그대로 발행하면 팔이 비정상 목표로 움직일 수 있으므로
        발행 전에 실패한다.
        """
        command: dict[str, float] = {}
        state = self._latest_joint_state
        if state is not None:
            for name, position in zip(state.name, state.position, strict=False):
                joint_name = str(name).strip()
                try:
                    numeric = float(position)
                except (TypeError, ValueError) as exc:
                    raise ManipulationError(
                        f"joint 목표: 현재 피드백 '{joint_name}' 값을 읽을 수 없어 명령을 "
                        "발행하지 않습니다. 드라이버 피드백 상태를 확인하세요."
                    ) from exc
                if not math.isfinite(numeric):
                    raise ManipulationError(
                        f"joint 목표: 현재 피드백 '{joint_name}' 값이 유한하지 않아 명령을 "
                        "발행하지 않습니다. 드라이버 피드백 상태를 확인하세요."
                    )
                command[joint_name] = numeric
        command.update(targets)
        return command

    def _raise_if_pose_command_not_finite(self, label: str, msg: PoseStamped) -> None:
        numbers = {
            "position.x": msg.pose.position.x,
            "position.y": msg.pose.position.y,
            "position.z": msg.pose.position.z,
            "orientation.x": msg.pose.orientation.x,
            "orientation.y": msg.pose.orientation.y,
            "orientation.z": msg.pose.orientation.z,
            "orientation.w": msg.pose.orientation.w,
        }
        invalid = [name for name, value in numbers.items() if not math.isfinite(float(value))]
        if invalid:
            raise ManipulationError(
                f"{label}: 유한하지 않은 값이 있어 명령을 발행하지 않습니다({', '.join(invalid)}). "
                "드라이버 피드백 상태를 확인하세요."
            )

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
        """드라이버 home 서비스를 호출하고, 실제 자세 수렴까지 검증한다.

        agx_arm_ctrl 의 ``/move_home`` 은 ``std_srvs/Empty`` 이고 내부 동작(미연결·
        미활성·모션 타임아웃)을 응답으로 알려주지 않는다. 응답만 보고 성공을 선언하면
        팔이 전혀 움직이지 않아도 "홈 이동 완료" 로 보고된다(실기 확인된 증상).
        따라서 서비스 호출 뒤 ``feedback/joint_states`` 가 드라이버 home 자세
        (모든 팔 조인트 0 rad)로 수렴했는지 확인하고, 수렴하지 않으면 실패로 알린다.
        """
        self.last_motion = None
        service_name = str(self._config.agx_home_service or "move_home").strip()
        if not service_name:
            raise ManipulationConfigError("manipulation_agx_home_service 가 비어 있습니다.")

        self._raise_if_motor_fault("home 목표")
        self._ensure_driver_ready()
        self._raise_if_arm_fault("home 목표")
        self._wait_for_feedback(
            lambda: self._feedback_is_fresh(self._joint_state_time), "home 목표"
        )
        home_targets = self._home_joint_targets()
        already_at_home = bool(home_targets) and self._joints_reached(home_targets)

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

        diagnostics_baseline = self._diagnostics_baseline()
        future = self._home_client.call_async(Empty.Request())
        wait_for_future_sync(
            self._node,
            future,
            float(self._config.agx_motion_timeout_sec),
            f"home 서비스 호출 타임아웃 ({float(self._config.agx_motion_timeout_sec):.1f}초)",
            error_cls=ManipulationError,
        )

        if not home_targets:
            # 피드백에 조인트 이름이 없으면 검증할 수 없다. 성공을 주장하지 않는다.
            raise ManipulationError(
                "home 서비스는 호출했지만 검증할 조인트 피드백이 없습니다. "
                "/feedback/joint_states 발행 상태를 확인하세요."
            )

        # 서비스 응답 직후에는 아직 이동 중일 수 있으므로, 목표 수렴을 제한 시간까지 기다린다.
        self._wait_until(
            lambda: self._joints_reached(home_targets),
            "home 목표",
            lambda: self._joint_feedback_seen,
            diagnostics_baseline=diagnostics_baseline,
        )
        self._set_last_motion(
            already_at_target=already_at_home,
            detail="driver home (팔 조인트 0 rad)",
        )

    def move_to_joint_target(self, group_name: str, joint_values: Mapping[str, float]) -> None:
        del group_name  # 드라이버는 MoveIt planning group 개념이 없다

        self.last_motion = None
        targets = self._finite_joint_values("joint 목표", joint_values)
        if not targets:
            raise ManipulationConfigError("joint target 이 비어 있습니다.")

        # targets 가 오직 그리퍼(gripper)만 포함하는 경우, 드라이버가 불필요한
        # 팔 move_j 를 수행하지 않도록 gripper 만 발행한다.
        # 팔 관절이 포함된 경우에만 드라이버의 생략 관절 0.0 보정을 위해
        # 현재 피드백의 팔 관절값을 유지한다.
        is_gripper_only = all("gripper" in name.lower() for name in targets)
        if not is_gripper_only:
            self._raise_if_motor_fault("joint 목표")
        self._wait_for_feedback(
            lambda: self._feedback_is_fresh(self._joint_state_time), "joint 목표"
        )
        cmd_positions = dict(targets) if is_gripper_only else self._preserved_joint_command(targets)

        self._ensure_driver_ready()
        if not is_gripper_only:
            self._raise_if_arm_fault("joint 목표")
        already_at_target = self._joints_reached(targets)

        msg = JointState()
        msg.name = list(cmd_positions.keys())
        msg.position = [cmd_positions[name] for name in msg.name]
        diagnostics_baseline = self._diagnostics_baseline()
        self._move_joint_pub.publish(msg)

        self._wait_until(
            lambda: self._joints_reached(targets),
            "joint 목표",
            lambda: self._joint_feedback_seen,
            republish_fn=lambda: self._move_joint_pub.publish(msg),
            diagnostics_baseline=diagnostics_baseline,
        )
        self._set_last_motion(already_at_target=already_at_target)

    def move_to_pose_target(
        self,
        group_name: str,
        pose: Any,
        end_effector_link: str,
        cartesian: bool = False,
    ) -> None:
        del group_name, end_effector_link  # 드라이버가 tool/그룹을 자체 관리한다

        self.last_motion = None
        base_frame = str(self._config.base_frame or "").strip()
        frame = str(getattr(pose, "frame_id", "") or base_frame).strip()
        if base_frame and frame and frame != base_frame:
            raise ManipulationConfigError(
                f"AGX 드라이버는 base frame('{base_frame}') 기준 포즈 목표만 받습니다: '{frame}'"
            )

        targets = self._finite_pose_targets(pose)

        self._raise_if_motor_fault("pose 목표")
        self._wait_for_feedback(lambda: self._feedback_is_fresh(self._tcp_pose_time), "pose 목표")

        msg = PoseStamped()
        msg.header.frame_id = base_frame
        msg.pose.position.x = targets["x"]
        msg.pose.position.y = targets["y"]
        msg.pose.position.z = targets["z"]
        orientation = getattr(pose, "orientation", None) or {}
        if not getattr(pose, "orientation_explicit", True):
            # 위치만 지정된 요청은 현재 end-effector 자세를 유지한다. identity 자세로
            # 고정하면 IK 불가(NO_SOLUTION)로 최대 신장에서 멈추는 원인이 된다.
            current = self._latest_tcp_pose.pose.orientation if self._latest_tcp_pose else None
            if current is not None:
                orientation = {
                    "x": current.x,
                    "y": current.y,
                    "z": current.z,
                    "w": current.w,
                }
        msg.pose.orientation.x = float(orientation.get("x", 0.0))
        msg.pose.orientation.y = float(orientation.get("y", 0.0))
        msg.pose.orientation.z = float(orientation.get("z", 0.0))
        msg.pose.orientation.w = float(orientation.get("w", 1.0))
        self._raise_if_pose_command_not_finite("pose 목표", msg)

        self._ensure_driver_ready()
        self._raise_if_arm_fault("pose 목표", require_ik=True)
        already_at_target = self._pose_reached(targets)

        publisher = self._move_linear_pub if cartesian else self._move_pose_pub
        diagnostics_baseline = self._diagnostics_baseline()
        publisher.publish(msg)

        self._wait_until(
            lambda: self._pose_reached(targets),
            "pose 목표",
            lambda: self._tcp_feedback_seen,
            republish_fn=lambda: publisher.publish(msg),
            diagnostics_baseline=diagnostics_baseline,
            allow_no_solution=False,
        )
        self._set_last_motion(already_at_target=already_at_target)
