"""설정 기반 접근-파지/배치-후퇴 시퀀스 스킬."""

from __future__ import annotations

import logging
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
    normalize_vector,
    offset_pose,
    pose_goal_from_params,
    tolerate_contact,
)
from robo_claw_agent.skill_manager import BaseSkill

from ..core import _get_runtime, _move_sequence_result, _resolve_target_pose, _transform_pose_goal
from .grasp_verification import _close_gripper_with_grasp_verification
from .gripper_target_skills import ObserveGripperTargetSkill, ServoGripperToObjectSkill
from .joint_state import _log_achieved_gripper_aperture
from .params import _bool_param, _ensure_gripper_open, _resolve_close_preset
from .rotation import _align_base_if_unreachable

logger = logging.getLogger(__name__)

_GRIPPER_CAMERA_ALIASES = {"gripper", "wrist", "hand", "end_effector", "eoa"}


def _recover_arm_to_stow(runtime: Any, arm_group: str | None) -> tuple[bool, str]:
    """실패 후 gripper 상태는 유지하고 arm만 stow로 복귀한다."""
    if runtime is None:
        return False, "manipulation runtime이 없어 arm stow를 시도할 수 없습니다."
    try:
        runtime.move_to_named_pose("stow", group_name=arm_group)
        return True, "arm stow 복구 완료"
    except Exception as exc:  # noqa: BLE001
        return False, f"arm stow 복구 실패: {exc}"


class GraspSkill(BaseSkill):
    """설정 기반 접근-파지-후퇴 시퀀스"""

    name = "grasp"
    description = (
        "목표 pose에 접근해 파지합니다. "
        "target_pose 또는 x/y/z 좌표(미터, 기본 프레임 base_link)가 필요하며, "
        "object_name은 메모리의 3D pose metadata가 있을 때만 사용할 수 있습니다. "
        "object_name으로 조회된 pose는 감지 시점이 아니라 실행 시점의 최신 TF로 base_link에 맞춰 자동 변환됩니다. "
        "Stretch3에서 object_name을 주면 기본적으로 그리퍼 카메라로 위치를 재추정/최종 확인한 뒤에만 "
        "닫습니다(require_gripper_confirmation=False로 끌 수 있음). "
        "IK로 도달 불가능하면 Stretch3는 필요한 만큼 베이스를 자동 회전한 뒤 재시도합니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "object_name": {"type": "string", "description": "메모리에 저장된 물체명"},
            "target_pose": {"type": "object", "description": "pose 목표(frame_id, position, orientation)"},
            "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
            "frame_id": {"type": "string", "default": "base_link"},
            "approach_distance_m": {"type": "number"},
            "retreat_distance_m": {"type": "number"},
            "require_gripper_confirmation": {"type": "boolean", "default": True},
            "require_ready": {"type": "boolean", "default": True},
            "max_grasp_retries": {"type": "integer", "default": 1}
        },
        "oneOf": [
            {"required": ["object_name"]},
            {"required": ["target_pose"]},
            {"required": ["x", "y", "z"]}
        ],
        "additionalProperties": True,
    }
    side_effects = ("arm_motion", "gripper_motion")
    allow_with_others = False

    def validate_params(self, params: dict[str, Any]) -> bool:
        if params.get("object_name"):
            return True
        try:
            pose_goal_from_params(params, default_frame="base_link")
        except ManipulationConfigError:
            return False
        return True

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        object_name = str(params.get("object_name") or "").strip()
        arm_group = str(params.get("group_name") or "").strip() or None
        gripper_group = str(params.get("gripper_group") or "").strip() or None
        open_preset = str(params.get("open_preset") or "open").strip()
        require_gripper_confirmation = _bool_param(params.get("require_gripper_confirmation"), True)
        require_ready = _bool_param(params.get("require_ready"), True)

        rotation_info: dict[str, Any] = {"rotated": False, "rotation_deg": None}
        gripper_camera_confirmed: bool | None = None
        confirmation_skipped_reason: str | None = None
        failed_stage = "precondition"
        executed_steps: list[str] = []
        runtime: Any | None = None

        try:
            runtime = _get_runtime(self.node)
            close_preset = _resolve_close_preset(params, object_name, runtime)
            is_stretch = runtime.config.backend == "stretch"
            camera_source = (
                str(params.get("camera") or params.get("camera_source") or "").strip().lower()
            )
            explicit_gripper_camera = camera_source in _GRIPPER_CAMERA_ALIASES or bool(
                params.get("estimate_with_gripper_camera", False)
            )
            # Stretch3 + object_name이면 camera 파라미터를 안 줘도 기본적으로
            # 그리퍼 카메라 확인 경로를 탄다(사용자 요청: 베이스 카메라만으로
            # 판단해 파지/배치가 어긋나는 문제 방지). require_gripper_confirmation=False로 끌 수 있다.
            use_gripper_estimate = explicit_gripper_camera or (
                require_gripper_confirmation and is_stretch and bool(object_name)
            )

            # 회전 정렬(M2)이 먼저다: 물체가 그리퍼 카메라 시야에 들어오려면
            # 필요 시 베이스가 먼저 정렬되어야 하므로, 그리퍼 카메라 재추정보다 앞서 확인한다.
            failed_stage = "target_resolution"
            target_pose = _resolve_target_pose(
                self.node,
                params,
                default_frame=runtime.config.base_frame,
            )
            target_pose, rotation_info = _align_base_if_unreachable(
                self, runtime, params, target_pose, default_frame=runtime.config.base_frame
            )

            if object_name and use_gripper_estimate:
                from robo_claw_agent.skills.perception_skill import (
                    EstimateGripperObjectPoseSkill,
                )

                estimator = EstimateGripperObjectPoseSkill()
                estimator.set_node(self.node)
                estimate_params = {
                    "target_object": object_name,
                    "timeout_sec": params.get("estimate_timeout_sec", 5.0),
                    "min_score": params.get("min_score", 0.1),
                    "register_to_map": True,
                    "camera": "gripper",
                }
                estimate_result = estimator.execute(estimate_params)
                if estimate_result.get("success", False):
                    # 그리퍼 카메라로 더 정밀한 pose를 얻었으면 그것으로 갱신한다.
                    target_pose = _resolve_target_pose(
                        self.node,
                        params,
                        default_frame=runtime.config.base_frame,
                    )
                elif require_ready:
                    recovery_ok, recovery_message = _recover_arm_to_stow(runtime, arm_group)
                    return {
                        "success": False,
                        "message": f"그리퍼 카메라 pose 추정 실패: {estimate_result.get('message', '')}",
                        "estimate": estimate_result,
                        "base_rotation_applied_deg": rotation_info.get("rotation_deg"),
                        "failed_stage": "gripper_confirmation",
                        "partial_execution": True,
                        "recovery_attempted": True,
                        "recovery_success": recovery_ok,
                        "recovery_message": recovery_message,
                        "requires_recovery": not recovery_ok,
                    }
                # require_ready=False면 재추정 실패를 무시하고 기존 target_pose로 진행.

            approach_distance = float(
                params.get(
                    "approach_distance_m",
                    runtime.config.default_approach_distance_m,
                )
            )
            retreat_distance = float(
                params.get(
                    "retreat_distance_m",
                    runtime.config.default_retreat_distance_m,
                )
            )
            approach_vector = normalize_vector(params.get("approach_vector"))
            retreat_vector = normalize_vector(
                params.get("retreat_vector") or params.get("approach_vector")
            )
            pre_grasp_pose = offset_pose(target_pose, approach_distance, approach_vector)
            retreat_pose = offset_pose(target_pose, retreat_distance, retreat_vector)

            failed_stage = "open"
            runtime.execute_gripper_preset(open_preset, group_name=gripper_group)
            executed_steps.append(failed_stage)
            _log_achieved_gripper_aperture(self, logger, "grasp open")
            # 준비자세(pre_grasp): contact 로 조기 중단되어도 이후 target 이동과
            # 그리퍼 비전 보정이 현재 자세에서 진행되므로 수용한다. 진짜 오류
            # (도달 불가·tolerance 위반 등)는 tolerate_contact 가 빠져나가 전파된다.
            failed_stage = "approach"
            with tolerate_contact("grasp pre_grasp"):
                runtime.move_to_pose_target(pre_grasp_pose, group_name=arm_group)
            executed_steps.append(failed_stage)
            failed_stage = "reach_target"
            runtime.move_to_pose_target(target_pose, group_name=arm_group)
            executed_steps.append(failed_stage)

            # 최종 판단은 그리퍼 카메라로: 접근 이동 자체의 오차까지 여기서 잡아낸다.
            if object_name and use_gripper_estimate:
                servo_skill = ServoGripperToObjectSkill()
                servo_skill.set_node(self.node)
                servo_params = dict(params)
                servo_params["target_object"] = object_name
                servo_params["camera"] = "gripper"
                servo_result = servo_skill.execute(servo_params)
                gripper_camera_confirmed = bool(servo_result.get("ready_to_grasp", False))
                if not gripper_camera_confirmed:
                    if require_ready:
                        recovery_ok, recovery_message = _recover_arm_to_stow(runtime, arm_group)
                        return {
                            "success": False,
                            "message": (
                                "그리퍼 카메라로 파지 준비 상태를 확인하지 못해 "
                                f"close를 실행하지 않았습니다: {servo_result.get('message', '')}"
                            ),
                            "gripper_camera_confirmed": False,
                            "servo_result": servo_result,
                            "base_rotation_applied_deg": rotation_info.get("rotation_deg"),
                            "failed_stage": "gripper_servo",
                            "partial_execution": True,
                            "recovery_attempted": True,
                            "recovery_success": recovery_ok,
                            "recovery_message": recovery_message,
                            "requires_recovery": not recovery_ok,
                        }
                    confirmation_skipped_reason = (
                        "ready_to_grasp 미충족이나 require_ready=False로 진행"
                    )
            else:
                gripper_camera_confirmed = False
                if not object_name:
                    confirmation_skipped_reason = "no nameable target"
                elif not require_gripper_confirmation:
                    confirmation_skipped_reason = "require_gripper_confirmation=False"
                else:
                    confirmation_skipped_reason = "gripper camera confirmation not applicable"

            # close 직전 항상 그리퍼를 최대로 연다.
            _ensure_gripper_open(runtime, open_preset, gripper_group)

            retry_on_empty_grasp = _bool_param(params.get("retry_on_empty_grasp"), True)
            max_grasp_retries = int(params.get("max_grasp_retries", 1))

            def _reacquire() -> dict[str, Any]:
                _ensure_gripper_open(runtime, open_preset, gripper_group)
                if object_name and use_gripper_estimate:
                    servo_skill2 = ServoGripperToObjectSkill()
                    servo_skill2.set_node(self.node)
                    servo_params2 = dict(params)
                    servo_params2["target_object"] = object_name
                    servo_params2["camera"] = "gripper"
                    return servo_skill2.execute(servo_params2)
                return {"success": True}

            failed_stage = "grasp_verification"
            grasp_verified, grasp_retry_count = _close_gripper_with_grasp_verification(
                self,
                runtime,
                close_preset=close_preset,
                open_preset=open_preset,
                gripper_group=gripper_group,
                reacquire=_reacquire,
                retry_on_empty_grasp=retry_on_empty_grasp,
                max_grasp_retries=max_grasp_retries,
            )
            failed_stage = "retreat"
            runtime.move_to_pose_target(retreat_pose, group_name=arm_group)
            executed_steps.append(failed_stage)
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
            ValueError,
            TypeError,
        ) as exc:
            logger.warning(f"Grasp failed ({object_name or 'target_pose'}): {exc}")
            recovery_ok, recovery_message = _recover_arm_to_stow(runtime, arm_group)
            return {
                "success": False,
                "message": str(exc),
                "failed_stage": failed_stage,
                "partial_execution": bool(executed_steps),
                "executed_steps": executed_steps,
                "recovery_attempted": True,
                "recovery_success": recovery_ok,
                "recovery_message": recovery_message,
                "requires_recovery": not recovery_ok,
            }

        result = _move_sequence_result(
            action=f"파지 완료: {object_name or 'target_pose'}",
            payload={
                "object_name": object_name,
                "target_pose": {
                    "frame_id": target_pose.frame_id,
                    "position": dict(target_pose.position),
                    "orientation": dict(target_pose.orientation),
                },
                "approach_distance_m": approach_distance,
                "retreat_distance_m": retreat_distance,
                "open_preset": open_preset,
                "close_preset": close_preset,
                "gripper_camera_confirmed": gripper_camera_confirmed,
                "confirmation_skipped_reason": confirmation_skipped_reason,
                "base_rotation_applied_deg": rotation_info.get("rotation_deg"),
                "reachability": {"rotated": rotation_info["rotated"]},
                "grasp_verified": grasp_verified,
                "grasp_retry_count": grasp_retry_count,
            },
        )
        if grasp_verified is not True:
            result["success"] = False
            result["message"] = (
                "파지 검증 불가: 실측 gripper aperture를 확인하지 못해 lift를 수행하지 않았습니다."
                if grasp_verified is None
                else (
                    f"파지 검증 실패: 그리퍼가 명령값까지 닫혀 물체를 놓친 것으로 판단됩니다 "
                    f"(재시도 {grasp_retry_count}회 후에도 실패, {object_name or 'target_pose'})"
                )
            )
        return result


class PlaceSkill(BaseSkill):
    """설정 기반 접근-배치-후퇴 시퀀스"""

    name = "place"
    description = (
        "목표 pose에 접근해 물체를 내려놓습니다. "
        "target_pose 또는 x/y/z 좌표(미터, 기본 프레임 base_link)가 필요합니다. "
        "Stretch3에서 target_object(내려놓을 위치/표면 이름)를 주면 open 전에 그리퍼 카메라로 "
        "정렬을 확인합니다(require_gripper_confirmation=False로 끌 수 있음). "
        "target_object 없이 좌표만 주면 시각 확인 없이 진행됩니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string", "description": "내려놓을 표면/위치 이름"},
            "target_pose": {"type": "object", "description": "pose 목표(frame_id, position, orientation)"},
            "x": {"type": "number"}, "y": {"type": "number"}, "z": {"type": "number"},
            "frame_id": {"type": "string", "default": "base_link"},
            "approach_distance_m": {"type": "number"},
            "retreat_distance_m": {"type": "number"},
            "require_gripper_confirmation": {"type": "boolean", "default": True},
            "require_ready": {"type": "boolean", "default": True}
        },
        "oneOf": [
            {"required": ["target_pose"]},
            {"required": ["x", "y", "z"]}
        ],
        "additionalProperties": True,
    }
    side_effects = ("arm_motion", "gripper_motion")
    allow_with_others = False

    def validate_params(self, params: dict[str, Any]) -> bool:
        try:
            pose_goal_from_params(params, default_frame="base_link")
        except ManipulationConfigError:
            return False
        return True

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        arm_group = str(params.get("group_name") or "").strip() or None
        gripper_group = str(params.get("gripper_group") or "").strip() or None
        open_preset = str(params.get("open_preset") or "open").strip()
        target_object = str(params.get("target_object") or "").strip()
        require_gripper_confirmation = _bool_param(params.get("require_gripper_confirmation"), True)
        require_ready = _bool_param(params.get("require_ready"), True)

        gripper_camera_confirmed: bool | None = None
        confirmation_skipped_reason: str | None = None
        failed_stage = "precondition"
        executed_steps: list[str] = []
        runtime: Any | None = None

        try:
            runtime = _get_runtime(self.node)
            is_stretch = runtime.config.backend == "stretch"
            target_pose = pose_goal_from_params(
                params,
                default_frame=runtime.config.base_frame,
            )
            target_pose = _transform_pose_goal(self.node, target_pose, runtime.config.base_frame)
            approach_distance = float(
                params.get(
                    "approach_distance_m",
                    runtime.config.default_approach_distance_m,
                )
            )
            retreat_distance = float(
                params.get(
                    "retreat_distance_m",
                    runtime.config.default_retreat_distance_m,
                )
            )
            approach_vector = normalize_vector(params.get("approach_vector"))
            retreat_vector = normalize_vector(
                params.get("retreat_vector") or params.get("approach_vector")
            )
            pre_place_pose = offset_pose(target_pose, approach_distance, approach_vector)
            retreat_pose = offset_pose(target_pose, retreat_distance, retreat_vector)

            failed_stage = "approach"
            runtime.move_to_pose_target(pre_place_pose, group_name=arm_group)
            executed_steps.append(failed_stage)
            failed_stage = "reach_target"
            runtime.move_to_pose_target(target_pose, group_name=arm_group)
            executed_steps.append(failed_stage)

            use_gripper_confirm = (
                is_stretch and require_gripper_confirmation and bool(target_object)
            )
            if use_gripper_confirm:
                observe_skill = ObserveGripperTargetSkill()
                observe_skill.set_node(self.node)
                observe_params = dict(params)
                observe_params["target_object"] = target_object
                observe_params["camera"] = "gripper"
                observe_result = observe_skill.execute(observe_params)
                gripper_camera_confirmed = bool(observe_result.get("ready_to_grasp", False))
                if not gripper_camera_confirmed:
                    if require_ready:
                        recovery_ok, recovery_message = _recover_arm_to_stow(runtime, arm_group)
                        return {
                            "success": False,
                            "message": (
                                "그리퍼 카메라로 배치 위치 정렬을 확인하지 못해 "
                                f"open을 실행하지 않았습니다: {observe_result.get('message', '')}"
                            ),
                            "gripper_camera_confirmed": False,
                            "observe_result": observe_result,
                            "failed_stage": "placement_confirmation",
                            "partial_execution": True,
                            "recovery_attempted": True,
                            "recovery_success": recovery_ok,
                            "recovery_message": recovery_message,
                            "requires_recovery": not recovery_ok,
                        }
                    confirmation_skipped_reason = "정렬 미충족이나 require_ready=False로 진행"
            else:
                gripper_camera_confirmed = False
                if not target_object:
                    confirmation_skipped_reason = "no target_object given"
                elif not require_gripper_confirmation:
                    confirmation_skipped_reason = "require_gripper_confirmation=False"
                else:
                    confirmation_skipped_reason = "gripper camera confirmation not applicable"

            failed_stage = "release"
            runtime.execute_gripper_preset(open_preset, group_name=gripper_group)
            executed_steps.append(failed_stage)
            failed_stage = "retreat"
            runtime.move_to_pose_target(retreat_pose, group_name=arm_group)
            executed_steps.append(failed_stage)
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
            ValueError,
            TypeError,
        ) as exc:
            logger.warning(f"Place failed: {exc}")
            recovery_ok, recovery_message = _recover_arm_to_stow(runtime, arm_group)
            return {
                "success": False,
                "message": str(exc),
                "failed_stage": failed_stage,
                "partial_execution": bool(executed_steps),
                "executed_steps": executed_steps,
                "recovery_attempted": True,
                "recovery_success": recovery_ok,
                "recovery_message": recovery_message,
                "requires_recovery": not recovery_ok,
            }

        return _move_sequence_result(
            action="내려놓기 완료",
            payload={
                "target_pose": {
                    "frame_id": target_pose.frame_id,
                    "position": dict(target_pose.position),
                    "orientation": dict(target_pose.orientation),
                },
                "approach_distance_m": approach_distance,
                "retreat_distance_m": retreat_distance,
                "open_preset": open_preset,
                "gripper_camera_confirmed": gripper_camera_confirmed,
                "confirmation_skipped_reason": confirmation_skipped_reason,
            },
        )
