"""그리퍼 카메라 폐루프 보정을 사용하는 VLA 스타일 집기 스킬."""

from __future__ import annotations

import logging
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
    tolerate_contact,
)
from robo_claw_agent.skill_manager import BaseSkill

from ..core import _get_runtime
from .align_skill import AlignRightArmToFrontSkill
from .grasp_verification import _close_gripper_with_grasp_verification, _lift_after_grasp
from .gripper_target_skills import ServoGripperToObjectSkill
from .params import (
    _bool_param,
    _clamp_int,
    _ensure_gripper_open,
    _float_param,
    _is_cup_target,
    _is_deadline_exceeded,
    _remaining_timeout,
    _resolve_close_preset,
    _resolve_deadline,
)
from .recovery import build_sequence_failure, recover_arm_to_stow
from .search_skill import SearchObjectSkill

logger = logging.getLogger(__name__)


class VLABasedPickFrontObjectSkill(BaseSkill):
    """정면 물체를 90도 정렬 후 그리퍼 RGB-D 폐루프로 보정해 집기 시도."""

    name = "vla_pick_front_object"
    requires_manipulation_backend = "stretch"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string", "default": "cup"},
            "max_grasp_retries": {"type": "integer", "default": 1},
        },
        "additionalProperties": True,
    }
    description = (
        "Stretch3 정면의 target_object를 집기 위한 비전 피드백 기반 스킬입니다. "
        "stow, 오른쪽 팔 작업축 정렬, ready pose 이동 후 그리퍼 카메라 RGB-D로 "
        "대상 위치를 관측/보정하고 안전 조건이 맞을 때만 그리퍼를 닫습니다. "
        "use_active_search=True면 고정 각도 대신 search_object(헤드 pan/tilt 스윗 + 몸통 회전 스윗)로 "
        "실제 물체 방향을 찾아 정렬합니다."
    )
    allow_with_others = False

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_object = str(params.get("target_object") or "cup").strip()
        angle_deg = _float_param(params, "align_angle_deg", _float_param(params, "angle_deg", 90.0))
        do_stow = _bool_param(params.get("do_stow"), True)
        use_active_search = _bool_param(params.get("use_active_search"), False)
        ready_pose = str(params.get("ready_pose") or "right_side_pick_ready")
        lift_pose = str(params.get("lift_pose") or "right_side_pick_lift")
        carry_pose = str(params.get("carry_pose") or "carry")
        open_preset = str(params.get("open_preset") or "open")
        skip_carry = _bool_param(params.get("skip_carry"), _is_cup_target(target_object))
        require_ready = _bool_param(params.get("require_ready"), True)
        deadline, _ = _resolve_deadline(self, params, 60.0)

        failed_stage = "runtime_init"
        executed_steps: list[str] = []
        steps: list[dict[str, Any]] = []
        cumulative_angle = 0.0

        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError) as exc:
            logger.warning("VLA pick failed (runtime): %s", exc)
            return build_sequence_failure(
                f"VLA 집기 실패: {exc}",
                failed_stage="runtime_init",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        if getattr(runtime.config, "backend", "") != "stretch":
            return build_sequence_failure(
                f"vla_pick_front_object는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
                failed_stage="backend_check",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        close_preset = _resolve_close_preset(params, target_object, runtime)

        try:
            if do_stow:
                if _is_deadline_exceeded(deadline):
                    raise ManipulationError("동작 제한 시간(deadline) 초과")
                failed_stage = "stow"
                from robo_claw_agent.skills.manipulation_skill import StowForNavigationSkill

                stow_skill = StowForNavigationSkill()
                stow_skill.set_node(self.node)
                stow_result = stow_skill.execute({"timeout_sec": _remaining_timeout(deadline)})
                steps.append({"skill": "stow_for_navigation", "result": stow_result})
                if not stow_result.get("success", False):
                    rec_ok, rec_msg = recover_arm_to_stow(runtime)
                    return build_sequence_failure(
                        "VLA 집기 전 stow 실패",
                        failed_stage="stow",
                        executed_steps=executed_steps,
                        recovery_attempted=True,
                        recovery_success=rec_ok,
                        recovery_message=rec_msg,
                        steps=steps,
                    )
                executed_steps.append("stow")

            if use_active_search:
                if _is_deadline_exceeded(deadline):
                    raise ManipulationError("동작 제한 시간(deadline) 초과")
                failed_stage = "search"
                search = SearchObjectSkill()
                search.set_node(self.node)
                search_params = dict(params)
                search_params["target_object"] = target_object
                search_params["timeout_sec"] = _remaining_timeout(deadline)
                search_result = search.execute(search_params)
                steps.append({"skill": "search_object", "result": search_result})
                if not search_result.get("success", False):
                    rec_ok, rec_msg = recover_arm_to_stow(runtime)
                    return build_sequence_failure(
                        f"VLA 집기 전 정면 물체 탐색 실패: {search_result.get('message', '')}",
                        failed_stage="search",
                        executed_steps=executed_steps,
                        recovery_attempted=True,
                        recovery_success=rec_ok,
                        recovery_message=rec_msg,
                        steps=steps,
                    )
                executed_steps.append("search")
                found_angle = search_result.get("align_angle_deg")
                if found_angle is not None:
                    angle_deg = found_angle
                cumulative_angle += float(
                    search_result.get("body_rotation_applied_deg", 0.0) or 0.0
                )

            if abs(angle_deg) > 1e-6:
                if _is_deadline_exceeded(deadline):
                    raise ManipulationError("동작 제한 시간(deadline) 초과")
                failed_stage = "align"
                align_skill = AlignRightArmToFrontSkill()
                align_skill.set_node(self.node)
                align_result = align_skill.execute(
                    {"angle_deg": angle_deg, "timeout_sec": _remaining_timeout(deadline)}
                )
                steps.append({"skill": "align_right_arm_to_front", "result": align_result})
                if not align_result.get("success", False):
                    rec_ok, rec_msg = recover_arm_to_stow(runtime)
                    return build_sequence_failure(
                        "VLA 집기 전 오른쪽 팔 작업축 정렬 실패",
                        failed_stage="align",
                        executed_steps=executed_steps,
                        recovery_attempted=True,
                        recovery_success=rec_ok,
                        recovery_message=rec_msg,
                        steps=steps,
                    )
                executed_steps.append("align")
                cumulative_angle += angle_deg

            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            failed_stage = "open"
            runtime.execute_gripper_preset(open_preset)
            executed_steps.append("open")

            failed_stage = "ready_pose"
            with tolerate_contact("vla_pick_front ready_pose"):
                runtime.move_to_named_pose(ready_pose)
            executed_steps.append("ready_pose")

            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            failed_stage = "servo"
            servo_skill = ServoGripperToObjectSkill()
            servo_skill.set_node(self.node)
            servo_params = dict(params)
            servo_params["target_object"] = target_object
            servo_params["camera"] = "gripper"
            servo_params["timeout_sec"] = _remaining_timeout(deadline)
            servo_result = servo_skill.execute(servo_params)
            steps.append({"skill": "servo_gripper_to_object", "result": servo_result})
            if not servo_result.get("success", False):
                rec_ok, rec_msg = recover_arm_to_stow(runtime)
                return build_sequence_failure(
                    f"그리퍼 비전 보정 실패: {servo_result.get('message', '')}",
                    failed_stage="servo",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                    recovery_success=rec_ok,
                    recovery_message=rec_msg,
                    steps=steps,
                )
            if require_ready and not servo_result.get("ready_to_grasp", False):
                rec_ok, rec_msg = recover_arm_to_stow(runtime)
                return build_sequence_failure(
                    "집기 준비 조건이 충족되지 않아 gripper close를 실행하지 않습니다.",
                    failed_stage="servo_precondition",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                    recovery_success=rec_ok,
                    recovery_message=rec_msg,
                    steps=steps,
                )
            executed_steps.append("servo")

            # close 직전 항상 그리퍼를 최대로 연다.
            _ensure_gripper_open(runtime, open_preset)

            retry_on_empty_grasp = _bool_param(params.get("retry_on_empty_grasp"), True)
            max_grasp_retries = _clamp_int(params.get("max_grasp_retries", 1), 0, 5, 1)

            def _reacquire() -> dict[str, Any]:
                _ensure_gripper_open(runtime, open_preset)
                reacquire_servo = ServoGripperToObjectSkill()
                reacquire_servo.set_node(self.node)
                reacquire_params = dict(servo_params)
                reacquire_params["timeout_sec"] = _remaining_timeout(deadline)
                reacquire_result = reacquire_servo.execute(reacquire_params)
                steps.append(
                    {"skill": "servo_gripper_to_object (재시도)", "result": reacquire_result}
                )
                return reacquire_result

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
            executed_steps.append("grasp_verification")

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
                lift_pose=lift_pose,
                lift_distance_m=_float_param(params, "post_grasp_lift_m", 0.18),
            )
            executed_steps.append("lift")

            if not skip_carry:
                if _is_deadline_exceeded(deadline):
                    raise ManipulationError("동작 제한 시간(deadline) 초과")
                failed_stage = "carry"
                runtime.move_to_named_pose(carry_pose)
                executed_steps.append("carry")
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as exc:
            logger.warning("VLA front object pick failed (%s): %s", target_object, exc)
            rec_ok, rec_msg = recover_arm_to_stow(runtime)
            return build_sequence_failure(
                str(exc),
                failed_stage=failed_stage,
                executed_steps=executed_steps,
                recovery_attempted=True,
                recovery_success=rec_ok,
                recovery_message=rec_msg,
                steps=steps,
            )

        sequence_log = [
            "stow_for_navigation" if do_stow else "skip_stow",
            "search_object" if use_active_search else "skip_search",
            f"align:{angle_deg}",
            f"open:{open_preset}",
            ready_pose,
            "servo_gripper_to_object",
            f"close:{close_preset}",
            lifted_step,
        ]
        if not skip_carry:
            sequence_log.append(carry_pose)

        return {
            "success": True,
            "message": f"그리퍼 카메라 기반 VLA 집기 완료: {target_object}",
            "target_object": target_object,
            "steps": steps,
            "grasp_verified": grasp_verified,
            "grasp_retry_count": grasp_retry_count,
            "open_preset": open_preset,
            "close_preset": close_preset,
            "used_active_search": use_active_search,
            "base_rotation_applied_deg": cumulative_angle,
            "sequence": sequence_log,
        }


class VLABasedPickGripperObjectSkill(BaseSkill):
    """이미 그리퍼 카메라/오른쪽 작업 구역에 보이는 물체를 회전 없이 VLA 집기."""

    name = "vla_pick_gripper_object"
    requires_manipulation_backend = "stretch"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string", "default": "cup"},
            "max_grasp_retries": {"type": "integer", "default": 1},
        },
        "additionalProperties": True,
    }
    description = (
        "Stretch3 집기 기본자세 또는 오른쪽 팔 작업 구역에서 그리퍼 카메라에 보이는 "
        "target_object를 회전 없이 관측/미세보정 후 집습니다. 정면 물체 정렬이 필요한 경우는 "
        "vla_pick_front_object를 사용하세요."
    )
    allow_with_others = False

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_object = str(params.get("target_object") or "cup").strip()
        move_ready = _bool_param(params.get("move_ready"), False)
        ready_pose = str(params.get("ready_pose") or "right_side_pick_ready")
        lift_pose = str(params.get("lift_pose") or "right_side_pick_lift")
        carry_pose = str(params.get("carry_pose") or "carry")
        open_preset = str(params.get("open_preset") or "open")
        open_first = _bool_param(params.get("open_first"), False)
        skip_carry = _bool_param(params.get("skip_carry"), _is_cup_target(target_object))
        require_ready = _bool_param(params.get("require_ready"), True)
        deadline, _ = _resolve_deadline(self, params, 60.0)

        failed_stage = "runtime_init"
        executed_steps: list[str] = []
        steps: list[dict[str, Any]] = []

        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError) as exc:
            logger.warning("VLA gripper pick failed (runtime): %s", exc)
            return build_sequence_failure(
                f"VLA 그리퍼 집기 실패: {exc}",
                failed_stage="runtime_init",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        if getattr(runtime.config, "backend", "") != "stretch":
            return build_sequence_failure(
                f"vla_pick_gripper_object는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
                failed_stage="backend_check",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        close_preset = _resolve_close_preset(params, target_object, runtime)

        try:
            if open_first:
                if _is_deadline_exceeded(deadline):
                    raise ManipulationError("동작 제한 시간(deadline) 초과")
                failed_stage = "open_first"
                runtime.execute_gripper_preset(open_preset)
                executed_steps.append("open_first")

            if move_ready:
                if _is_deadline_exceeded(deadline):
                    raise ManipulationError("동작 제한 시간(deadline) 초과")
                failed_stage = "ready_pose"
                with tolerate_contact("vla_pick_gripper ready_pose"):
                    runtime.move_to_named_pose(ready_pose)
                executed_steps.append("ready_pose")

            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            failed_stage = "servo"
            servo_skill = ServoGripperToObjectSkill()
            servo_skill.set_node(self.node)
            servo_params = dict(params)
            servo_params["target_object"] = target_object
            servo_params["camera"] = "gripper"
            servo_params["timeout_sec"] = _remaining_timeout(deadline)
            servo_result = servo_skill.execute(servo_params)
            steps.append({"skill": "servo_gripper_to_object", "result": servo_result})
            if not servo_result.get("success", False):
                rec_ok, rec_msg = recover_arm_to_stow(runtime)
                return build_sequence_failure(
                    f"그리퍼 비전 보정 실패: {servo_result.get('message', '')}",
                    failed_stage="servo",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                    recovery_success=rec_ok,
                    recovery_message=rec_msg,
                    steps=steps,
                )
            if require_ready and not servo_result.get("ready_to_grasp", False):
                rec_ok, rec_msg = recover_arm_to_stow(runtime)
                return build_sequence_failure(
                    "집기 준비 조건이 충족되지 않아 gripper close를 실행하지 않습니다.",
                    failed_stage="servo_precondition",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                    recovery_success=rec_ok,
                    recovery_message=rec_msg,
                    steps=steps,
                )
            executed_steps.append("servo")

            # close 직전 항상 그리퍼를 최대로 연다.
            _ensure_gripper_open(runtime, open_preset)

            retry_on_empty_grasp = _bool_param(params.get("retry_on_empty_grasp"), True)
            max_grasp_retries = _clamp_int(params.get("max_grasp_retries", 1), 0, 5, 1)

            def _reacquire() -> dict[str, Any]:
                _ensure_gripper_open(runtime, open_preset)
                reacquire_servo = ServoGripperToObjectSkill()
                reacquire_servo.set_node(self.node)
                reacquire_params = dict(servo_params)
                reacquire_params["timeout_sec"] = _remaining_timeout(deadline)
                reacquire_result = reacquire_servo.execute(reacquire_params)
                steps.append(
                    {"skill": "servo_gripper_to_object (재시도)", "result": reacquire_result}
                )
                return reacquire_result

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
            executed_steps.append("grasp_verification")

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
                lift_pose=lift_pose,
                lift_distance_m=_float_param(params, "post_grasp_lift_m", 0.18),
            )
            executed_steps.append("lift")

            if not skip_carry:
                if _is_deadline_exceeded(deadline):
                    raise ManipulationError("동작 제한 시간(deadline) 초과")
                failed_stage = "carry"
                runtime.move_to_named_pose(carry_pose)
                executed_steps.append("carry")
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as exc:
            logger.warning("Gripper camera VLA pick failed (%s): %s", target_object, exc)
            rec_ok, rec_msg = recover_arm_to_stow(runtime)
            return build_sequence_failure(
                str(exc),
                failed_stage=failed_stage,
                executed_steps=executed_steps,
                recovery_attempted=True,
                recovery_success=rec_ok,
                recovery_message=rec_msg,
                steps=steps,
            )

        sequence_log = [
            f"open:{open_preset}" if open_first else "skip_open",
            ready_pose if move_ready else "keep_current_pick_pose",
            "servo_gripper_to_object",
            f"close:{close_preset}",
            lifted_step,
        ]
        if not skip_carry:
            sequence_log.append(carry_pose)

        return {
            "success": True,
            "message": f"그리퍼 카메라 기반 VLA 집기 완료: {target_object}",
            "target_object": target_object,
            "steps": steps,
            "grasp_verified": grasp_verified,
            "grasp_retry_count": grasp_retry_count,
            "open_preset": open_preset,
            "close_preset": close_preset,
            "sequence": sequence_log,
        }
