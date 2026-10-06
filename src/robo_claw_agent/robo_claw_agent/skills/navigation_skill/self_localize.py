import logging
import math
import threading
import time
from typing import Any

from geometry_msgs.msg import PoseWithCovarianceStamped
from std_srvs.srv import Empty

from robo_claw_agent.geo_utils import yaw_from_quaternion
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.limits import check_relative_move

from .core import (
    _cancel_active_goal,
    _get_nav2_dependency_error,
    _send_navigation_goal,
    _send_spin_goal,
    _spin_time_allowance_sec,
    _wait_for_goal_result,
)

logger = logging.getLogger(__name__)

# AMCL 글로벌 로컬라이제이션 서비스 (파티클을 맵 전체 자유공간에 균일 분산)
_REINIT_SERVICE = "/reinitialize_global_localization"

# 공분산 수렴 판정 기본 임계값
_DEFAULT_XY_STD = 0.35  # 위치 표준편차 [m]
_DEFAULT_YAW_STD = 0.35  # 방향 표준편차 [rad]
_DEFAULT_MAX_ATTEMPTS = 4
_DEFAULT_FORWARD_STEP = 0.5  # 수렴 유도용 전진 거리 [m]
_DEFAULT_TIME_BUDGET_SEC = 240.0  # 전체 시간 예산

# PoseWithCovarianceStamped.pose.covariance(6x6 행 우선)에서 x/y/yaw 분산 인덱스
_COV_XX = 0
_COV_YY = 7
_COV_YAWYAW = 35


class SelfLocalizeSkill(BaseSkill):
    """초기 포즈 없이 AMCL 글로벌 로컬라이제이션으로 현재 위치를 스스로 찾는 스킬.

    파티클을 맵 전체에 흩뿌린 뒤(reinitialize_global_localization), 제자리 회전과
    소폭 전진을 반복하며 라이다 스캔으로 파티클을 수렴시킨다. `/amcl_pose`의 공분산이
    임계값 이하로 떨어지면 성공으로 판정하고 추정 포즈를 보고한다.
    """

    name = "self_localize"
    input_schema = {
        "type": "object",
        "properties": {
            "max_attempts": {"type": "integer"},
            "xy_std_threshold": {"type": "number"},
            "yaw_std_threshold": {"type": "number"},
            "forward_step": {"type": "number"},
        },
        "additionalProperties": False,
    }
    description = (
        "미리 만들어 둔 맵 위에서 로봇이 자신의 현재 위치를 모를 때, 사용자의 명시적인 지시('위치 다시 잡아' 등)에 "
        "의해서만 초기 위치를 직접 지정하지 않고 스스로 위치를 찾습니다(AMCL 글로벌 로컬라이제이션). "
        "주의: navigate_to나 주행 스킬이 실패했을 때 자동으로 이 스킬을 실행하지 마세요. "
        "파티클을 맵 전체에 분산시킨 뒤 제자리 회전과 소폭 전진을 반복하며 라이다로 위치를 수렴시키므로 로봇이 잠시 움직입니다. "
        "대칭적이거나 특징이 적은 공간에서는 위치를 확정하지 못할 수 있으며, 그 경우 실패로 보고합니다. "
        "선택 파라미터: max_attempts(int), xy_std_threshold(float, m), yaw_std_threshold(float, rad), "
        "forward_step(float, m). SLAM 모드로 구동 중이면 사용할 수 없습니다."
    )

    def __init__(self) -> None:
        super().__init__()
        self._cancelled = threading.Event()

    def cancel(self) -> bool:
        """협조적 중단: 진행 중인 회전/이동 goal을 취소하고 루프를 종료시킨다."""
        self._cancelled.set()
        try:
            cancelled, _ = _cancel_active_goal()
            return cancelled
        except Exception as e:  # noqa: BLE001
            logger.warning("Error while canceling self_localize: %s", e)
            return False

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        self._cancelled.clear()

        nav2_error = _get_nav2_dependency_error()
        if nav2_error:
            return {"success": False, "message": nav2_error}

        max_attempts = int(params.get("max_attempts", _DEFAULT_MAX_ATTEMPTS))
        xy_std_threshold = float(params.get("xy_std_threshold", _DEFAULT_XY_STD))
        yaw_std_threshold = float(params.get("yaw_std_threshold", _DEFAULT_YAW_STD))
        forward_step = float(params.get("forward_step", _DEFAULT_FORWARD_STEP))
        deadline = time.monotonic() + _DEFAULT_TIME_BUDGET_SEC

        # 1) AMCL 로컬라이제이션 모드 확인 + 파티클 전역 재분산 요청
        reinit_ok, reinit_msg = self._reinitialize_particles()
        if not reinit_ok:
            return {"success": False, "message": reinit_msg}

        # 2) 최신 amcl_pose를 상시 보관하는 구독 설정
        #    amcl_pose는 로봇이 움직일 때만 갱신되고 퍼블리셔 QoS가 버전마다 달라,
        #    최대 호환(BEST_EFFORT/VOLATILE) 구독으로 최신값을 붙잡아 둔다.
        latest_pose: dict[str, PoseWithCovarianceStamped] = {}
        pose_lock = threading.Lock()

        def _pose_cb(msg: PoseWithCovarianceStamped) -> None:
            with pose_lock:
                latest_pose["msg"] = msg

        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy

        pose_qos = QoSProfile(depth=5)
        pose_qos.reliability = ReliabilityPolicy.BEST_EFFORT
        pose_qos.durability = DurabilityPolicy.VOLATILE
        # 구독은 매 실행마다 destroy하지 않고 캐시된 슬롯을 재사용한다.
        # 임시 구독을 destroy하면 MultiThreadedExecutor가 이미 큐에 넣은
        # take 핸들러가 InvalidHandle로 실패해 노드가 종료될 수 있다.
        pose_slot = self.get_message_slot(
            PoseWithCovarianceStamped, "/amcl_pose", pose_qos, handler=_pose_cb
        )

        self.send_user_message(
            "🔍 현재 위치를 스스로 탐색합니다. 잠시 로봇이 회전하고 조금씩 이동합니다."
        )

        try:
            for attempt in range(1, max_attempts + 1):
                if self._cancelled.is_set():
                    return {"success": False, "message": "위치 탐색이 취소되었습니다."}
                if time.monotonic() > deadline:
                    return self._converge_failed(
                        "시간 예산을 초과했습니다", xy_std_threshold, yaw_std_threshold
                    )

                # 2-1) 제자리 360도 회전으로 라이다가 주변 특징을 스캔하게 한다
                self._spin_full_turn()
                if self._cancelled.is_set():
                    return {"success": False, "message": "위치 탐색이 취소되었습니다."}

                # 2-2) 회전 직후 최신 amcl_pose 공분산으로 수렴 판정
                with pose_lock:
                    pose_msg = latest_pose.get("msg")
                converged, xy_std, yaw_std = self._check_converged(
                    pose_msg, xy_std_threshold, yaw_std_threshold
                )

                logger.info(
                    "self_localize attempt %d/%d: xy_std=%s, yaw_std=%s",
                    attempt,
                    max_attempts,
                    f"{xy_std:.3f}" if xy_std is not None else "N/A",
                    f"{yaw_std:.3f}" if yaw_std is not None else "N/A",
                )

                if converged and pose_msg is not None:
                    return self._converge_success(pose_msg, xy_std, yaw_std)

                # 2-3) 아직 수렴 안 됨 → 마지막 시도가 아니면 소폭 전진으로 시점 이동
                if attempt < max_attempts:
                    self._nudge_forward(forward_step)

            with pose_lock:
                pose_msg = latest_pose.get("msg")
            _, xy_std, yaw_std = self._check_converged(
                pose_msg, xy_std_threshold, yaw_std_threshold
            )
            return self._converge_failed(
                "회전/이동을 반복했지만 위치가 확정되지 않았습니다",
                xy_std_threshold,
                yaw_std_threshold,
                xy_std=xy_std,
                yaw_std=yaw_std,
            )
        finally:
            pose_slot.remove_handler(_pose_cb)

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _reinitialize_particles(self) -> tuple[bool, str]:
        """AMCL의 글로벌 로컬라이제이션 서비스를 호출해 파티클을 전역 재분산한다.

        서비스가 없으면 AMCL 로컬라이제이션 모드가 아님(예: SLAM 모드)으로 간주한다.
        """
        ok, _ = self.call_service(Empty, _REINIT_SERVICE, timeout_sec=5.0)
        if not ok:
            return (
                False,
                "AMCL 로컬라이제이션 모드가 아닙니다. "
                f"'{_REINIT_SERVICE}' 서비스를 찾을 수 없습니다(SLAM 모드로 구동 중이거나 "
                "AMCL/map_server가 실행되지 않았을 수 있습니다).",
            )
        return True, "파티클을 맵 전체에 재분산했습니다."

    def _spin_full_turn(self) -> None:
        """제자리 360도 회전을 수행하고 완료까지 대기한다(실패해도 계속 진행)."""
        angle_rad = 2.0 * math.pi
        allowance = _spin_time_allowance_sec(angle_rad)
        accepted, message, goal_handle = _send_spin_goal(
            self.node, angle_rad, time_allowance_sec=allowance
        )
        if not accepted:
            logger.warning("Failed to send self_localize rotation goal: %s", message)
            return
        success, result_message = _wait_for_goal_result(
            goal_handle, timeout_sec=allowance + 15.0, label="위치탐색 회전"
        )
        if not success:
            logger.warning("self_localize rotation did not complete: %s", result_message)

    def _nudge_forward(self, forward_step: float) -> None:
        """수렴 유도를 위해 현재 헤딩 기준으로 소폭 전진한다.

        map 프레임 포즈가 (아직 부정확하더라도) 로봇 헤딩과는 일관되므로, 상대 전진
        목표를 map 프레임으로 보내면 물리적으로 앞으로 이동한다. 전역 포즈를 모르는
        상태여도 Nav2 로컬 코스트맵(odom+라이다)이 장애물 회피를 담당한다.
        """
        if forward_step <= 0.0:
            return

        robot_limits = getattr(self.node, "_robot_limits_dict", None)
        limit_error = check_relative_move(forward_step, 0.0, robot_limits)
        if limit_error:
            logger.info("Skipping self_localize forward move (limit): %s", limit_error)
            return

        pose = self.get_map_pose()
        if not pose:
            logger.info("Skipping self_localize forward move: current pose unknown")
            return

        cur_x, cur_y, yaw, frame = pose["x"], pose["y"], pose["yaw"], pose["frame"]
        target_x = cur_x + forward_step * math.cos(yaw)
        target_y = cur_y + forward_step * math.sin(yaw)

        accepted, message, goal_handle = _send_navigation_goal(
            self.node, target_x, target_y, frame, yaw=yaw
        )
        if not accepted:
            logger.info("Failed to send self_localize forward goal (ignored): %s", message)
            return
        success, result_message = _wait_for_goal_result(
            goal_handle, timeout_sec=60.0, label="위치탐색 전진"
        )
        if not success:
            logger.info("self_localize forward move did not complete (ignored): %s", result_message)

    def _check_converged(
        self,
        pose_msg: PoseWithCovarianceStamped | None,
        xy_std_threshold: float,
        yaw_std_threshold: float,
    ) -> tuple[bool, float | None, float | None]:
        """amcl_pose 공분산으로 수렴 여부와 위치/방향 표준편차를 계산한다."""
        if pose_msg is None:
            return False, None, None
        cov = pose_msg.pose.covariance
        try:
            x_var = float(cov[_COV_XX])
            y_var = float(cov[_COV_YY])
            yaw_var = float(cov[_COV_YAWYAW])
        except (IndexError, TypeError, ValueError):
            return False, None, None

        # 음수(수치오차)나 비정상 값 방어
        xy_std = math.sqrt(max(x_var, 0.0) + max(y_var, 0.0))
        yaw_std = math.sqrt(max(yaw_var, 0.0))
        converged = xy_std <= xy_std_threshold and yaw_std <= yaw_std_threshold
        return converged, xy_std, yaw_std

    def _converge_success(
        self,
        pose_msg: PoseWithCovarianceStamped,
        xy_std: float | None,
        yaw_std: float | None,
    ) -> dict[str, Any]:
        p = pose_msg.pose.pose
        x = round(float(p.position.x), 3)
        y = round(float(p.position.y), 3)
        yaw = round(math.degrees(yaw_from_quaternion(p.orientation)), 1)
        self.send_user_message(
            f"✅ 현재 위치를 찾았습니다! 위치=({x:.2f}, {y:.2f})m, 방향={yaw:.1f}° "
            f"(위치 오차 ±{xy_std:.2f}m)"
        )
        return {
            "success": True,
            "message": (
                f"글로벌 로컬라이제이션 성공: (x={x}, y={y}, yaw={yaw}°), "
                f"xy_std={round(xy_std, 3) if xy_std is not None else None}, "
                f"yaw_std={round(yaw_std, 3) if yaw_std is not None else None}"
            ),
            "pose": {"x": x, "y": y, "yaw_deg": yaw},
            "xy_std": round(xy_std, 3) if xy_std is not None else None,
            "yaw_std": round(yaw_std, 3) if yaw_std is not None else None,
        }

    def _converge_failed(
        self,
        reason: str,
        xy_std_threshold: float,
        yaw_std_threshold: float,
        xy_std: float | None = None,
        yaw_std: float | None = None,
    ) -> dict[str, Any]:
        self.send_user_message(
            "❌ 현재 위치를 확정하지 못했습니다. 대칭적이거나 특징이 적은 공간일 수 있습니다. "
            "다른 위치로 옮겨 다시 시도하거나 초기 위치를 직접 지정해 주세요."
        )
        return {
            "success": False,
            "message": (
                f"글로벌 로컬라이제이션 실패: {reason}. "
                f"(임계값 xy_std<={xy_std_threshold}m, yaw_std<={yaw_std_threshold}rad; "
                f"측정 xy_std={round(xy_std, 3) if xy_std is not None else 'N/A'}, "
                f"yaw_std={round(yaw_std, 3) if yaw_std is not None else 'N/A'})"
            ),
            "xy_std": round(xy_std, 3) if xy_std is not None else None,
            "yaw_std": round(yaw_std, 3) if yaw_std is not None else None,
        }
