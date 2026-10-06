"""표면 유형별 결정론적 고정 패턴 파지 스킬.

그리퍼 카메라(D405) 근접 비주얼 서보(``ServoGripperToObjectSkill``)는 물체에
가까워질수록 대상이 화면에서 밀리고 depth 가 소실돼 수렴하지 못하는 취약 구간이
있다. VLA(Vision-Language-Action)를 사용하지 못하는 환경에서는, 물체 **근처까지**
(헤드 카메라 3D 관측 + 베이스 정렬 + 팔 접근)는 이미 잘 수행되므로, 마지막 파지를
"표면 유형별 고정 패턴"으로 결정론적으로 수행하는 것이 더 견고하다.

이 스킬은:

1. 물체의 base_link 3D 좌표를 헤드 카메라 관측으로 얻는다(안정적, 원거리).
2. 표면 유형(floor / elevated)을 판단하거나 힌트를 받는다.
3. ``_ik_position`` 으로 파지점/접근점의 (joint_lift, wrist_extension, yaw)를 계산한다.
4. 결정론적 시퀀스(오픈 → 접근 hover → 파지 지점 도달 → close 검증 → lift → carry)를 수행한다.

근접 비주얼 서보를 전혀 쓰지 않으므로 D405 근접 관측 문제에서 자유롭고, 표면
유형이 결정한 lift/접근 높이 프로파일을 재현성 있게 반복한다.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
    tolerate_contact,
)
from robo_claw_agent.manipulation_runtime.stretch_kinematics import _ik_position
from robo_claw_agent.skill_manager import BaseSkill

from ..core import _get_runtime
from .grasp_verification import _close_gripper_with_grasp_verification, _lift_after_grasp
from .head_assist import HeadAssist
from .head_vision import _observe_head_target
from .joint_state import _log_achieved_gripper_aperture
from .params import (
    _bool_param,
    _clamp,
    _clamp_int,
    _float_param,
    _is_cup_target,
    _is_deadline_exceeded,
    _resolve_close_preset,
    _resolve_deadline,
)
from .recovery import build_sequence_failure, recover_arm_to_stow
from .surface_classify import ClassifyObjectSurfaceSkill

logger = logging.getLogger(__name__)

# 표면 유형별 기본 접근/리프트 프로파일(m).
# Stretch3 팔 그립 중심 최소 도달 z 는 ~0.11m(FK 기준 joint_lift+0.11)이다.
# 바닥 컵 중심(z≈0.08~0.12)은 경계라, 바닥 그립 오프셋은 음수보다 0에 가깝게 잡아
# 도달 가능 범위를 보장한다.
# floor: 바닥에서 더 높이 들어올려 운반에 필요한 여유를 확보.
# elevated: 이미 표면 위에 있으므로 리프트는 작게.
_FLOOR_GRASP_Z_OFFSET_M = 0.0
_ELEVATED_GRASP_Z_OFFSET_M = 0.0
_FLOOR_APPROACH_HEIGHT_M = 0.10
_ELEVATED_APPROACH_HEIGHT_M = 0.06
_FLOOR_POST_GRASP_LIFT_M = 0.18
_ELEVATED_POST_GRASP_LIFT_M = 0.12


def _ik_reach(runtime: Any, xyz: tuple[float, float, float]) -> dict[str, float] | None:
    """base_link 3D 좌표를 팔 joint 목표(joint_lift/wrist_extension/yaw)로 변환.

    도달 불가 또는 수치 IK 수렴 실패 시 None.
    """
    sol = _ik_position(xyz)
    if sol is None:
        return None
    lift, ext, yaw = sol
    return {
        "joint_lift": _clamp(float(lift), 0.0, 1.1),
        "wrist_extension": _clamp(float(ext), 0.0, 0.52),
        "joint_wrist_yaw": float(yaw),
    }


class FixedPatternGraspSkill(BaseSkill):
    """표면 유형에 따라 결정론적 고정 패턴으로 파지."""

    name = "fixed_pattern_grasp"
    is_internal = True
    requires_manipulation_backend = "stretch"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string", "default": "cup"},
            "surface_type": {"type": "string", "enum": ["floor", "elevated"]},
            "max_grasp_retries": {"type": "integer", "default": 1},
        },
        "required": ["target_object"],
        "additionalProperties": True,
    }
    description = (
        "Stretch3에서 물체 근처까지 접근한 뒤 표면 유형(floor/elevated)에 따라 "
        "미리 정의된 고정 패턴으로 파지합니다. 헤드 카메라로 물체 3D 좌표를 얻고 "
        "표면을 판단한 뒤, IK로 파지 지점의 관절 목표를 계산해 결정론적 시퀀스 "
        "(오픈 → 접근 hover → 파지 도달 → close 검증 → lift → carry)를 수행합니다. "
        "object_base_xyz 또는 surface_hint를 미리 알면 재관측을 건너뜁니다. "
        "근접 그리퍼 비주얼 서보를 사용하지 않습니다."
    )
    allow_with_others = False

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_object = str(params.get("target_object") or "cup").strip()
        open_preset = str(params.get("open_preset") or "open").strip()
        skip_carry = _bool_param(params.get("skip_carry"), _is_cup_target(target_object))
        deadline, _ = _resolve_deadline(self, params, 30.0)

        failed_stage = "runtime_init"
        executed_steps: list[str] = []
        steps: list[dict[str, Any]] = []

        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError) as exc:
            logger.warning("Fixed pattern grasp failed (runtime): %s", exc)
            return build_sequence_failure(
                f"고정 패턴 파지 실패: {exc}",
                failed_stage="runtime_init",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        if getattr(runtime.config, "backend", "") != "stretch":
            return build_sequence_failure(
                f"fixed_pattern_grasp는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
                failed_stage="backend_check",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        close_preset = _resolve_close_preset(params, target_object, runtime)

        # 1) 물체 3D 좌표와 표면 유형 결정 (명시 값 우선, 없으면 헤드 관측).
        failed_stage = "resolve_target"
        obj_xyz = self._resolve_object_xyz(params)
        surface, surface_from, classify = self._resolve_surface(params, obj_xyz)
        if obj_xyz is None:
            return build_sequence_failure(
                "물체 3D 좌표를 얻지 못해 고정 패턴 파지를 수행할 수 없습니다.",
                failed_stage="resolve_target",
                executed_steps=executed_steps,
                recovery_attempted=False,
                target_object=target_object,
                surface=surface,
                surface_from=surface_from,
                classify=classify,
            )

        # 2) 표면 유형별 파지/접근 높이 프로파일.
        grasp_z_offset_m = (
            _float_param(params, "grasp_z_offset_m", _FLOOR_GRASP_Z_OFFSET_M)
            if surface == "floor"
            else _float_param(params, "grasp_z_offset_m", _ELEVATED_GRASP_Z_OFFSET_M)
        )
        approach_height_m = (
            _float_param(params, "approach_height_m", _FLOOR_APPROACH_HEIGHT_M)
            if surface == "floor"
            else _float_param(params, "approach_height_m", _ELEVATED_APPROACH_HEIGHT_M)
        )
        post_grasp_lift_m = (
            _float_param(params, "post_grasp_lift_m", _FLOOR_POST_GRASP_LIFT_M)
            if surface == "floor"
            else _float_param(params, "post_grasp_lift_m", _ELEVATED_POST_GRASP_LIFT_M)
        )

        grasp_xyz = (obj_xyz[0], obj_xyz[1], obj_xyz[2] + grasp_z_offset_m)
        approach_xyz = (grasp_xyz[0], grasp_xyz[1], grasp_xyz[2] + approach_height_m)

        failed_stage = "ik_reach"
        grasp_joints = _ik_reach(runtime, grasp_xyz)
        if grasp_joints is None:
            return build_sequence_failure(
                (
                    f"파지 지점({grasp_xyz[0]:.3f},{grasp_xyz[1]:.3f},{grasp_xyz[2]:.3f})이 "
                    "팔 도달 범위 밖이거나 IK 가 수렴하지 않아 고정 패턴 파지를 하지 않습니다."
                ),
                failed_stage="ik_reach",
                executed_steps=executed_steps,
                recovery_attempted=False,
                target_object=target_object,
                surface=surface,
                object_base_xyz=obj_xyz,
            )
        approach_joints = _ik_reach(runtime, approach_xyz) or grasp_joints

        # 3) 결정론적 파지 시퀀스.
        try:
            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            failed_stage = "open"
            runtime.execute_gripper_preset(open_preset)
            _log_achieved_gripper_aperture(self, logger, "fixed_pattern_grasp open")
            executed_steps.append(failed_stage)
            steps.append({"stage": "open", "open_preset": open_preset})

            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            # 접근 hover: 물체 위로 접근하다 contact 로 조기 중단되어도 다음 파지
            # 지점 이동이 현재 자세에서 진행되므로 수용한다.
            failed_stage = "approach"
            with tolerate_contact("fixed_pattern_grasp approach"):
                runtime.move_to_joint_target(approach_joints, group_name="arm")
            executed_steps.append(failed_stage)
            steps.append({"stage": "approach", "joints": approach_joints})

            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            failed_stage = "reach_grasp"
            runtime.move_to_joint_target(grasp_joints, group_name="arm")
            executed_steps.append(failed_stage)
            steps.append({"stage": "reach_grasp", "joints": grasp_joints})

            retry_on_empty_grasp = _bool_param(params.get("retry_on_empty_grasp"), True)
            max_grasp_retries = _clamp_int(params.get("max_grasp_retries", 1), 0, 5, 1)

            def _reacquire() -> dict[str, Any]:
                runtime.execute_gripper_preset(open_preset)
                with tolerate_contact("fixed_pattern_grasp reacquire_approach"):
                    runtime.move_to_joint_target(approach_joints, group_name="arm")
                runtime.move_to_joint_target(grasp_joints, group_name="arm")
                return {"success": True}

            failed_stage = "grasp_verification"
            grasp_verified, grasp_retry_count = _close_gripper_with_grasp_verification(
                self,
                runtime,
                close_preset=close_preset,
                open_preset=open_preset,
                gripper_group=None,
                reacquire=_reacquire,
                retry_on_empty_grasp=retry_on_empty_grasp,
                max_grasp_retries=max_grasp_retries,
            )
            executed_steps.append(failed_stage)
            steps.append(
                {"stage": "close", "close_preset": close_preset, "grasp_verified": grasp_verified}
            )

            # fail-closed: grasp_verified가 True가 아니면 절대 lift/carry를 실행하지 않는다.
            if grasp_verified is not True:
                rec_ok, rec_msg = recover_arm_to_stow(runtime)
                message = (
                    "파지 검증 불가: 실측 gripper aperture를 확인하지 못해 lift/carry를 수행하지 않았습니다."
                    if grasp_verified is None
                    else f"파지 검증 실패: 재시도 {grasp_retry_count}회 후에도 물체를 놓쳤습니다 ({target_object})"
                )
                return build_sequence_failure(
                    message,
                    failed_stage="grasp_verification",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                    recovery_success=rec_ok,
                    recovery_message=rec_msg,
                    target_object=target_object,
                    surface=surface,
                    grasp_verified=grasp_verified,
                    grasp_retry_count=grasp_retry_count,
                    steps=steps,
                )

            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            failed_stage = "lift"
            lifted_step = _lift_after_grasp(
                self,
                runtime,
                target_object,
                lift_pose=str(params.get("lift_pose") or "right_side_pick_lift"),
                lift_distance_m=post_grasp_lift_m,
            )
            executed_steps.append(failed_stage)
            steps.append({"stage": "lift", "detail": lifted_step})

            if not skip_carry:
                if _is_deadline_exceeded(deadline):
                    raise ManipulationError("동작 제한 시간(deadline) 초과")
                failed_stage = "carry"
                runtime.move_to_named_pose(str(params.get("carry_pose") or "carry"))
                executed_steps.append(failed_stage)
                steps.append({"stage": "carry"})
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as exc:
            logger.warning("Fixed pattern grasp failed (%s): %s", target_object, exc)
            rec_ok, rec_msg = recover_arm_to_stow(runtime)
            return build_sequence_failure(
                str(exc),
                failed_stage=failed_stage,
                executed_steps=executed_steps,
                recovery_attempted=True,
                recovery_success=rec_ok,
                recovery_message=rec_msg,
                target_object=target_object,
                surface=surface,
                steps=steps,
            )

        return {
            "success": True,
            "message": f"고정 패턴 파지 완료({surface}): {target_object}",
            "target_object": target_object,
            "surface": surface,
            "surface_from": surface_from,
            "object_base_xyz": {
                "x": round(obj_xyz[0], 4),
                "y": round(obj_xyz[1], 4),
                "z": round(obj_xyz[2], 4),
            },
            "grasp_xyz": {
                "x": round(grasp_xyz[0], 4),
                "y": round(grasp_xyz[1], 4),
                "z": round(grasp_xyz[2], 4),
            },
            "approach_height_m": round(approach_height_m, 3),
            "grasp_z_offset_m": round(grasp_z_offset_m, 3),
            "post_grasp_lift_m": round(post_grasp_lift_m, 3),
            "grasp_verified": grasp_verified,
            "grasp_retry_count": grasp_retry_count,
            "open_preset": open_preset,
            "close_preset": close_preset,
            "steps": steps,
        }

    # ── 헬퍼 ────────────────────────────────────────────────────────────────

    def _resolve_object_xyz(
        self, params: dict[str, Any]
    ) -> tuple[float, float, float] | None:
        """object_base_xyz 파라미터 또는 헤드 카메라 관측으로 물체 3D 좌표를 얻는다."""
        raw = params.get("object_base_xyz")
        if isinstance(raw, Mapping):
            try:
                return (float(raw["x"]), float(raw["y"]), float(raw["z"]))
            except (KeyError, TypeError, ValueError):
                pass

        observation = self._observe_object(params)
        if observation is None:
            return None
        raw = observation.get("object_base_xyz")
        if not isinstance(raw, Mapping):
            return None
        try:
            return (float(raw["x"]), float(raw["y"]), float(raw["z"]))
        except (KeyError, TypeError, ValueError):
            return None

    def _observe_object(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """헤드 카메라로 물체를 관측한다 (ArUco 보정 적용, best-effort)."""
        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError):
            runtime = None

        use_head_assist = (
            _bool_param(params.get("use_head_assist"), True)
            and runtime is not None
            and runtime.config.backend == "stretch"
        )
        if use_head_assist:
            head_assist = HeadAssist(self, params, enabled=True)
            try:
                obj_xyz, observation = head_assist.observe()
                if obj_xyz is not None:
                    return observation
            except Exception as exc:  # noqa: BLE001 - 보조이므로 흡수
                logger.warning("Head assist observe raised, falling back: %s", exc)

        if self.node is None:
            return None
        try:
            return _observe_head_target(self, params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Head observation raised: %s", exc)
            return None

    def _resolve_surface(
        self, params: dict[str, Any], obj_xyz: tuple[float, float, float] | None
    ) -> tuple[str | None, str, dict[str, Any] | None]:
        """표면 유형을 결정한다: 힌트 → (관측 가능하면) 분류 스킬 → (부재 시) z 임계."""
        classify: dict[str, Any] | None = None
        if self.node is not None:
            classifier = ClassifyObjectSurfaceSkill()
            classifier.set_node(self.node)
            classify_params = dict(params)
            classify_params["target_object"] = params.get("target_object", "cup")
            if obj_xyz is not None:
                classify_params["object_base_xyz"] = {
                    "x": obj_xyz[0],
                    "y": obj_xyz[1],
                    "z": obj_xyz[2],
                }
            classify = classifier.execute(classify_params)
            surface = classify.get("surface")
            if surface in {"floor", "elevated"}:
                return surface, classify.get("surface_from", "observation"), classify

        # 분류 스킬이 실패했거나 노드가 없으면 z 임계값으로 직접 판단한다.
        if obj_xyz is None:
            return None, "unresolved", classify
        from .surface_classify import _DEFAULT_FLOOR_Z_THRESHOLD_M

        threshold = _float_param(params, "floor_z_threshold_m", _DEFAULT_FLOOR_Z_THRESHOLD_M)
        surface = "floor" if obj_xyz[2] < threshold else "elevated"
        return surface, "z_threshold", classify
