"""헤드 카메라 파지 보조 (관측 유도 + 좌우 정렬).

그리퍼 카메라 단독 서보의 두 가지 구조적 한계를 헤드 카메라로 메운다:

1. **근접 시 관측 소실** — D405 는 손목 고정이라 ``wrist_extension`` 이 늘수록 대상이
   화면 밖으로 밀리고, 근접하면 YOLO 가 물체 전체를 못 봐 탐지에 실패한다. 기존
   서보는 이때 ``joint_lift`` 를 눈감고 흔들어 재획득을 시도했다. 헤드 카메라는
   마스트 상단에 있어 팔이 뻗어도 계속 대상을 보므로, 눈감고 흔드는 대신 **실제
   3D 오차 방향으로 유도**할 수 있다.
2. **좌우 오차 보정 불가** — ``joint_lift``/``wrist_extension`` 만으로는 팔 작업축에
   직교한 좌우 오차를 줄일 수 없다. Stretch3 에서 이 자유도는 베이스 회전뿐이다.

**안전**: ``rotation._ROTATE_SAFE_WRIST_EXTENSION_M`` 은 팔이 뻗은 상태의 베이스
회전을 금지한다. 따라서 좌우 정렬의 주 경로는 **팔이 접힌 ready pose 시점**이고,
서보 도중 회전은 옵트인이며 활성 시 안전 길이까지 먼저 접는다.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

from robo_claw_agent.manipulation_runtime.stretch_kinematics import (
    compute_align_rotation_from_bearing_rad,
)
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.limits import check_rotation

from ..head import HeadPanTiltSkill
from .aruco_calib import MarkerResidualCache, make_residual_cache
from .ee_state import _decompose_base_error, _grasp_center_base_xyz
from .head_vision import _object_base_xyz, _observe_head_target
from .joint_state import _joint_positions
from .params import _bool_param, _clamp, _float_param
from .rotation import _ROTATE_SAFE_WRIST_EXTENSION_M

logger = logging.getLogger(__name__)

_DEFAULT_HEAD_ASSIST_POSE = "gripper_side_head"
_DEFAULT_HEAD_SETTLE_SEC = 0.5
_DEFAULT_MAX_HEAD_GUIDED_STEPS = 6
# 이보다 작은 관절 델타는 명령하지 않는다(왕복 진동 방지).
_MIN_JOINT_DELTA_M = 0.005

# 좌우 정렬: 데드밴드 이내는 무시, 상한 초과는 오검출로 보고 거부한다.
_DEFAULT_LATERAL_DEADBAND_DEG = 1.5
_DEFAULT_MAX_LATERAL_CORRECTION_DEG = 20.0
# 헤드 3D 와 그리퍼 3D 가 이 이상 어긋나면 서로 다른 물체를 본 것으로 판단한다.
_DEFAULT_DUAL_VIEW_AGREE_M = 0.15
# depth 소실 시 "이미 충분히 가깝다"고 인정할 헤드 기준 거리.
_DEFAULT_GRASP_CONFIRM_RADIUS_M = 0.05


@dataclass(frozen=True)
class ArmBounds:
    """서보가 사용하는 관절 클램프/스텝 한계 (ServoGripperToObjectSkill 에서 주입)."""

    min_extension_m: float
    max_extension_m: float
    min_lift_m: float
    max_lift_m: float
    extension_step_m: float
    max_lift_step_m: float


class HeadAssist:
    """헤드 카메라 보조 상태(헤드 배치 여부, ArUco 잔차 래치)를 들고 있는 헬퍼."""

    def __init__(self, skill: BaseSkill, params: dict[str, Any], *, enabled: bool) -> None:
        self.skill = skill
        self.params = params
        self.enabled = enabled
        self.pose_name = str(params.get("head_assist_pose") or "").strip() or (
            skill.get_string_param(params, "head_assist_pose", _DEFAULT_HEAD_ASSIST_POSE)
        )
        self.settle_sec = _float_param(params, "head_move_settle_sec", _DEFAULT_HEAD_SETTLE_SEC)
        self.max_guided_steps = int(
            params.get("max_head_guided_steps", _DEFAULT_MAX_HEAD_GUIDED_STEPS)
        )
        self.guided_steps = 0
        self.head_ready = _bool_param(params.get("head_assist_ready"), False)
        self.residual_cache: MarkerResidualCache | None = None
        self.steps: list[dict[str, Any]] = []

    # ── 헤드 배치 / 잔차 래치 ────────────────────────────────────────────────

    def ensure_pose(self) -> dict[str, Any]:
        """헤드를 파지 감시 자세로 옮기고 ArUco 잔차를 1회 래치한다.

        헤드 이동 실패는 치명적이지 않다(``search_skill`` 의 best-effort 패턴과 동일) —
        헤드가 이미 대상을 보고 있을 수도 있으므로 관측 자체는 계속 시도한다.
        **서보 루프 도중에는 호출하지 않는다**: 헤드를 움직이면 TF 지연과 모션
        블러로 관측 품질이 무너진다.
        """
        info: dict[str, Any] = {"moved": False, "pose_name": self.pose_name}
        if not self.enabled:
            return info

        if not self.head_ready:
            head = HeadPanTiltSkill()
            head.set_node(self.skill.node)
            result = head.execute({"pose_name": self.pose_name})
            info["moved"] = bool(result.get("success", False))
            info["result"] = result
            if result.get("success", False):
                time.sleep(max(0.0, self.settle_sec))
            else:
                logger.warning(
                    "Head assist pose move failed, continuing best-effort: %s",
                    result.get("message", ""),
                )
            self.head_ready = True

        if self.residual_cache is None:
            self.residual_cache = make_residual_cache(self.skill, self.params)
        else:
            self.residual_cache.refresh(self.skill, self.params)
        info["aruco_residual"] = (
            None if self.residual_cache is None else self.residual_cache.residual
        )
        info["aruco_diagnostics"] = (
            {} if self.residual_cache is None else self.residual_cache.diagnostics
        )
        self.steps.append({"stage": "head_assist_pose", **info})
        return info

    # ── 관측 ────────────────────────────────────────────────────────────────

    def observe(self) -> tuple[tuple[float, float, float] | None, dict[str, Any]]:
        """헤드 카메라로 물체의 base_link 3D 좌표를 관측한다(ArUco 잔차 보정 적용).

        헤드 보조는 어디까지나 보조다. 카메라/TF/토픽 어느 단계에서 무슨 예외가 나든
        파지 자체를 죽여서는 안 되므로, 예외는 "관측 실패"로 흡수하고 호출부가 기존
        폴백 경로를 타게 한다.
        """
        if not self.enabled:
            return None, {"success": False, "message": "헤드 보조가 비활성화되어 있습니다."}

        try:
            observation = _observe_head_target(self.skill, self.params)
        except Exception as exc:  # noqa: BLE001 - 보조 기능이므로 모든 예외를 흡수
            logger.warning("Head assist observation raised, falling back: %s", exc)
            return None, {"success": False, "message": f"헤드 관측 예외: {exc}"}
        raw_xyz = _object_base_xyz(observation)
        if raw_xyz is None:
            return None, observation

        if self.residual_cache is not None:
            corrected = self.residual_cache.apply(raw_xyz)
            if corrected != raw_xyz:
                observation["aruco_corrected_base_xyz"] = {
                    "x": round(corrected[0], 4),
                    "y": round(corrected[1], 4),
                    "z": round(corrected[2], 4),
                }
            return corrected, observation
        return raw_xyz, observation

    def object_error(
        self,
    ) -> tuple[tuple[float, float, float] | None, tuple[float, float, float] | None, dict[str, Any]]:
        """(물체 base 좌표, 오차 벡터 obj-ee, 관측 결과) 를 반환한다."""
        obj_xyz, observation = self.observe()
        if obj_xyz is None:
            return None, None, observation
        try:
            ee_xyz = _grasp_center_base_xyz(self.skill)
        except Exception as exc:  # noqa: BLE001 - 보조 기능이므로 모든 예외를 흡수
            logger.warning("grasp center lookup raised, falling back: %s", exc)
            ee_xyz = None
        if ee_xyz is None:
            observation = dict(observation)
            observation["success"] = False
            observation["message"] = "grasp center 위치를 TF/FK 어느 쪽으로도 얻지 못했습니다."
            return obj_xyz, None, observation
        err = (
            obj_xyz[0] - ee_xyz[0],
            obj_xyz[1] - ee_xyz[1],
            obj_xyz[2] - ee_xyz[2],
        )
        observation["grasp_center_base_xyz"] = {
            "x": round(ee_xyz[0], 4),
            "y": round(ee_xyz[1], 4),
            "z": round(ee_xyz[2], 4),
        }
        observation["error_base_m"] = {
            "x": round(err[0], 4),
            "y": round(err[1], 4),
            "z": round(err[2], 4),
        }
        return obj_xyz, err, observation

    # ── 유도 (그리퍼 관측 실패 / depth 소실 시) ────────────────────────────

    def assist_step(
        self, runtime: Any, bounds: ArmBounds, *, move: bool = True
    ) -> dict[str, Any]:
        """헤드 관측 1회로 파지 도달 여부를 판정하고, 미도달이면 한 스텝 보정한다.

        기존의 "눈감고 ``joint_lift`` 를 흔드는" 재획득을 대체한다. 헤드 관측은
        비싸므로(검출 폴링 + depth + TF) 판정과 보정이 **같은 관측 1회**를 쓰도록
        합쳤다.

        Args:
            move: False 면 판정만 하고 관절 명령을 내리지 않는다(depth 소실 확인용).

        Returns:
            ``outcome`` 키가 다음 중 하나인 dict.

            - ``"ready"``: 물체가 파지 반경 안 → gripper close 로 진행해도 좋다
            - ``"guided"``: 오차 방향으로 관절을 보정했다 → 다음 스텝에서 재관측
            - ``"blocked"``: 남은 오차가 좌우 성분뿐이라 팔 관절로는 못 줄인다
            - ``"failed"``: 관측 실패 / joint 읽기 실패 / 유도 예산 소진
        """
        radius = _float_param(
            self.params, "grasp_confirm_radius_m", _DEFAULT_GRASP_CONFIRM_RADIUS_M
        )
        obj_xyz, err, observation = self.object_error()
        if err is None or obj_xyz is None:
            return {
                "outcome": "failed",
                "message": observation.get("message", "헤드 관측 실패"),
                "observation": observation,
            }

        distance = math.sqrt(sum(component * component for component in err))
        observation["grasp_distance_m"] = round(distance, 4)
        observation["grasp_confirm_radius_m"] = radius
        base_result: dict[str, Any] = {
            "object_base_xyz": obj_xyz,
            "error_base_m": err,
            "distance_m": distance,
            "observation": observation,
        }

        if distance <= radius:
            return {
                "outcome": "ready",
                "message": (
                    f"헤드 카메라 기준 물체가 파지 반경 이내입니다 "
                    f"({distance:.3f}m <= {radius:.3f}m)."
                ),
                **base_result,
            }

        # along: 팔이 뻗어나가는 방향 성분(+면 물체가 더 멀다 → 더 뻗어야 함).
        # vertical: z 오차. joint_lift 는 z 와 1:1 로 대응한다(FK 로 확인).
        along, lateral, vertical = _decompose_base_error(err)
        base_result["error_decomposed_m"] = {
            "along": round(along, 4),
            "lateral": round(lateral, 4),
            "vertical": round(vertical, 4),
        }

        if not move:
            return {
                "outcome": "blocked",
                "message": (
                    f"헤드 카메라 기준 물체가 아직 {distance:.3f}m 떨어져 있습니다 "
                    f"(파지 반경 {radius:.3f}m)."
                ),
                **base_result,
            }

        if self.guided_steps >= max(0, self.max_guided_steps):
            return {
                "outcome": "failed",
                "message": f"헤드 유도 예산({self.max_guided_steps}회)을 모두 사용했습니다.",
                **base_result,
            }

        joints = _joint_positions(self.skill, ["wrist_extension", "joint_lift"])
        if joints is None or "wrist_extension" not in joints or "joint_lift" not in joints:
            return {
                "outcome": "failed",
                "message": "joint_states에서 wrist_extension/joint_lift를 읽지 못했습니다.",
                **base_result,
            }

        target: dict[str, float] = {}
        lift_delta = _clamp(vertical, -bounds.max_lift_step_m, bounds.max_lift_step_m)
        if abs(lift_delta) >= _MIN_JOINT_DELTA_M:
            target["joint_lift"] = _clamp(
                joints["joint_lift"] + lift_delta, bounds.min_lift_m, bounds.max_lift_m
            )

        ext_delta = _clamp(along, -bounds.extension_step_m, bounds.extension_step_m)
        if abs(ext_delta) >= _MIN_JOINT_DELTA_M:
            target["wrist_extension"] = _clamp(
                joints["wrist_extension"] + ext_delta,
                bounds.min_extension_m,
                bounds.max_extension_m,
            )

        if not target:
            # 파지 반경 밖인데 팔 관절로 줄일 성분이 없다 = 남은 오차가 좌우뿐.
            # Stretch3 에서 이 자유도는 베이스 회전이므로 호출부가 판단해야 한다.
            return {
                "outcome": "blocked",
                "message": (
                    f"남은 오차가 좌우 성분(lateral={lateral:.3f}m)뿐이라 "
                    "팔 관절로는 보정할 수 없습니다."
                ),
                **base_result,
            }

        logger.info(
            "Head-guided correction: err(along=%.3f lateral=%.3f vertical=%.3f) target=%s",
            along,
            lateral,
            vertical,
            target,
        )
        runtime.move_to_joint_target(target, group_name="arm")
        self.guided_steps += 1
        return {
            "outcome": "guided",
            "message": "헤드 카메라 관측 기준으로 팔을 보정했습니다.",
            "moved": target,
            **base_result,
        }

    def disagrees_with(
        self, gripper_target_pose: Any, obj_xyz: tuple[float, float, float]
    ) -> bool:
        """헤드 관측이 그리퍼 관측과 너무 다르면(=다른 물체) True."""
        from collections.abc import Mapping

        if not isinstance(gripper_target_pose, Mapping):
            return False
        from .head_vision import _map_point_to_base

        gripper_xyz = _map_point_to_base(self.skill, gripper_target_pose)
        if gripper_xyz is None:
            return False
        agree_m = _float_param(self.params, "dual_view_agree_m", _DEFAULT_DUAL_VIEW_AGREE_M)
        spread = math.dist(gripper_xyz, obj_xyz)
        if spread > agree_m:
            logger.warning(
                "Head/gripper observations disagree (%.3fm > %.3fm) — 헤드 관측을 폐기합니다.",
                spread,
                agree_m,
            )
            return True
        return False


# ── 좌우 정렬 (베이스 미세회전) ────────────────────────────────────────────


def lateral_align(
    skill: BaseSkill,
    runtime: Any,
    params: dict[str, Any],
    obj_base_xyz: tuple[float, float, float],
    *,
    allow_retract: bool = False,
) -> dict[str, Any]:
    """물체를 팔 작업축 위로 올리는 데 필요한 베이스 회전을 수행한다.

    ``compute_align_rotation_from_bearing_rad`` 로 필요한 yaw 를 직접 계산한다
    (``rotation`` 모듈이 이미 쓰는 검증된 순수 함수).

    Args:
        allow_retract: 팔이 안전 길이보다 뻗어 있을 때 먼저 접는 것을 허용할지.
            서보 시작 전(팔이 접힌 상태)에는 필요 없고, 서보 도중 회전을 옵트인으로
            허용할 때만 True 로 준다.

    Returns:
        ``rotated`` (실제 회전 여부), ``angle_deg``, ``reason`` 을 담은 dict.
        회전하지 않아도 실패가 아니다(데드밴드 이내가 정상 상태다).
    """
    deadband_rad = math.radians(
        _float_param(params, "lateral_deadband_deg", _DEFAULT_LATERAL_DEADBAND_DEG)
    )
    max_correction_rad = math.radians(
        _float_param(
            params, "max_lateral_correction_deg", _DEFAULT_MAX_LATERAL_CORRECTION_DEG
        )
    )

    bearing_rad = math.atan2(obj_base_xyz[1], obj_base_xyz[0])
    yaw_rad = compute_align_rotation_from_bearing_rad(bearing_rad)
    angle_deg = math.degrees(yaw_rad)
    info: dict[str, Any] = {
        "rotated": False,
        "angle_deg": round(angle_deg, 2),
        "object_base_xyz": obj_base_xyz,
    }

    if abs(yaw_rad) < deadband_rad:
        info["reason"] = "데드밴드 이내 — 회전 불필요"
        return info

    if abs(yaw_rad) > max_correction_rad:
        info["reason"] = (
            f"필요 회전각({angle_deg:.1f}°)이 상한"
            f"({math.degrees(max_correction_rad):.1f}°)을 초과 — 오검출 의심으로 회전하지 않습니다."
        )
        logger.warning("Lateral align rejected: %s", info["reason"])
        return info

    limit_error = check_rotation(angle_deg, getattr(skill.node, "_robot_limits_dict", None))
    if limit_error:
        info["reason"] = f"회전 한계 위반: {limit_error}"
        logger.warning("Lateral align rejected: %s", info["reason"])
        return info

    joints = _joint_positions(skill, ["wrist_extension"])
    current_ext = None if joints is None else joints.get("wrist_extension")
    if current_ext is None:
        info["reason"] = "wrist_extension을 읽지 못해 회전 안전성을 확인할 수 없습니다."
        logger.warning("Lateral align rejected: %s", info["reason"])
        return info

    if current_ext > _ROTATE_SAFE_WRIST_EXTENSION_M:
        if not allow_retract:
            info["reason"] = (
                f"팔이 뻗어 있어({current_ext:.3f}m > {_ROTATE_SAFE_WRIST_EXTENSION_M:.2f}m) "
                "회전하지 않습니다."
            )
            logger.warning("Lateral align rejected: %s", info["reason"])
            return info
        # 뻗은 팔을 휘두르지 않도록 안전 길이까지 먼저 접는다.
        runtime.move_to_joint_target(
            {"wrist_extension": _ROTATE_SAFE_WRIST_EXTENSION_M}, group_name="arm"
        )
        info["retracted_to_m"] = _ROTATE_SAFE_WRIST_EXTENSION_M
        info["restore_extension_m"] = current_ext

    runtime.move_to_joint_target({"rotate_mobile_base": yaw_rad}, group_name="base")
    info["rotated"] = True
    info["reason"] = "베이스 미세회전으로 좌우 오차를 보정했습니다."
    logger.info("Lateral align: rotated base by %.2f deg", angle_deg)

    if "restore_extension_m" in info:
        runtime.move_to_joint_target(
            {"wrist_extension": info["restore_extension_m"]}, group_name="arm"
        )
    return info
