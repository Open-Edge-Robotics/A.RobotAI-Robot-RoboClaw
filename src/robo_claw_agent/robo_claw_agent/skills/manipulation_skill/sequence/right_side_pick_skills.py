"""Stretch3 오른쪽 팔 작업 구역(캘리브레이션된 named pose)을 이용한 집기 스킬."""

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
from robo_claw_agent.skill_manager import BaseSkill

from ..core import _get_runtime
from .align_skill import AlignRightArmToFrontSkill
from .grasp_verification import _close_gripper_with_grasp_verification, _lift_after_grasp
from .joint_state import _log_achieved_gripper_aperture
from .params import (
    _bool_param,
    _clamp_int,
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


class PickFromRightSideZoneSkill(BaseSkill):
    """Stretch3 오른쪽 팔 작업 구역에 있는 물체를 preset sequence로 집기."""

    name = "pick_from_right_side_zone"
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
        "Stretch3 오른쪽 팔 작업 구역에 이미 놓인 물체를 사전 캘리브레이션된 named pose "
        "시퀀스로 집습니다. 물체가 로봇 정면에 있다면 먼저 `align_right_arm_to_front` "
        "또는 `pick_front_object`를 사용하세요."
    )
    allow_with_others = False

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_object = str(params.get("target_object") or params.get("object_name") or "").strip()
        ready_pose = str(params.get("ready_pose") or "right_side_pick_ready")
        reach_pose = str(params.get("reach_pose") or "right_side_pick_reach")
        lift_pose = str(params.get("lift_pose") or "right_side_pick_lift")
        carry_pose = str(params.get("carry_pose") or "carry")
        open_preset = str(params.get("open_preset") or "open")
        skip_carry = _bool_param(params.get("skip_carry"), _is_cup_target(target_object))
        retry_on_empty_grasp = _bool_param(params.get("retry_on_empty_grasp"), True)
        max_grasp_retries = _clamp_int(params.get("max_grasp_retries", 1), 0, 5, 1)
        deadline, _ = _resolve_deadline(self, params, 30.0)

        failed_stage = "runtime_init"
        executed_steps: list[str] = []

        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError) as exc:
            logger.warning("Right work-zone pick failed (runtime): %s", exc)
            return build_sequence_failure(
                f"오른쪽 팔 작업 구역 집기 실패: {exc}",
                failed_stage="runtime_init",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        if getattr(runtime.config, "backend", "") != "stretch":
            return build_sequence_failure(
                f"pick_from_right_side_zone는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
                failed_stage="backend_check",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        close_preset = _resolve_close_preset(params, target_object, runtime)

        try:
            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            failed_stage = "open"
            runtime.execute_gripper_preset(open_preset)
            executed_steps.append("open")

            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            # 준비자세(ready_pose): contact 로 조기 중단되어도 현재 자세에서
            # 이후 reach/시각 서보가 진행되므로 수용한다.
            failed_stage = "ready_pose"
            with tolerate_contact("pick_from_right_side_zone ready_pose"):
                runtime.move_to_named_pose(ready_pose)
            executed_steps.append("ready_pose")

            if _is_deadline_exceeded(deadline):
                raise ManipulationError("동작 제한 시간(deadline) 초과")

            failed_stage = "reach_pose"
            runtime.move_to_named_pose(reach_pose)
            executed_steps.append("reach_pose")

            def _reacquire() -> dict[str, Any]:
                runtime.move_to_named_pose(reach_pose)
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
            executed_steps.append("grasp_verification")

            # fail-closed: grasp_verified가 True가 아니면 절대 lift/carry를 실행하지 않는다.
            if grasp_verified is not True:
                rec_ok, rec_msg = recover_arm_to_stow(runtime)
                message = (
                    "파지 검증 불가: 실측 gripper aperture를 확인하지 못해 lift/carry를 수행하지 않았습니다."
                    if grasp_verified is None
                    else f"파지 검증 실패: 재시도 {grasp_retry_count}회 후에도 물체를 놓친 것으로 판단됩니다"
                )
                return build_sequence_failure(
                    message,
                    failed_stage="grasp_verification",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                    recovery_success=rec_ok,
                    recovery_message=rec_msg,
                    grasp_verified=grasp_verified,
                    grasp_retry_count=grasp_retry_count,
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
            logger.warning("Right work-zone pick failed: %s", exc)
            rec_ok, rec_msg = recover_arm_to_stow(runtime)
            return build_sequence_failure(
                str(exc),
                failed_stage=failed_stage,
                executed_steps=executed_steps,
                recovery_attempted=True,
                recovery_success=rec_ok,
                recovery_message=rec_msg,
            )

        sequence_log = [
            f"open:{open_preset}",
            ready_pose,
            reach_pose,
            f"close:{close_preset}",
            lifted_step,
        ]
        if not skip_carry:
            sequence_log.append(carry_pose)

        return {
            "success": True,
            "message": "오른쪽 팔 작업 구역 물체 집기 완료",
            "sequence": sequence_log,
            "grasp_verified": grasp_verified,
            "grasp_retry_count": grasp_retry_count,
        }


class PrepareRightSidePickSkill(BaseSkill):
    """Stretch3 오른쪽 팔 작업 구역 집기 기본자세로 이동."""

    name = "prepare_right_side_pick"
    is_internal = True
    requires_manipulation_backend = "stretch"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = (
        "Stretch3 오른쪽 팔 작업 구역의 물체를 집기 전 기본자세로 이동합니다. "
        "그리퍼를 열고 right_side_pick_ready pose로 이동합니다. 정면 물체용 90도 회전은 수행하지 않습니다."
    )
    allow_with_others = False

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        ready_pose = str(params.get("ready_pose") or "right_side_pick_ready")
        open_preset = str(params.get("open_preset") or "open")
        staged = _bool_param(params.get("staged"), True)
        failed_stage = "runtime_init"
        executed_steps: list[str] = []

        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError) as exc:
            logger.warning("Prepare right side pick failed (runtime): %s", exc)
            return build_sequence_failure(
                f"집기 기본자세 준비 실패: {exc}",
                failed_stage="runtime_init",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        if getattr(runtime.config, "backend", "") != "stretch":
            return build_sequence_failure(
                f"prepare_right_side_pick는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
                failed_stage="backend_check",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        try:
            failed_stage = "open"
            runtime.execute_gripper_preset(open_preset)
            _log_achieved_gripper_aperture(self, logger, "prepare_right_side_pick open")
            executed_steps.append("open")

            pose = runtime.config.named_poses.get(ready_pose)
            if staged and isinstance(pose, Mapping):
                first_stage = {
                    name: value for name, value in pose.items() if name != "wrist_extension"
                }
                if first_stage:
                    failed_stage = "first_stage"
                    with tolerate_contact("prepare_right_side_pick first_stage"):
                        runtime.move_to_joint_target(first_stage, group_name="arm")
                    executed_steps.append("first_stage")
                if "wrist_extension" in pose:
                    failed_stage = "wrist_extension"
                    with tolerate_contact("prepare_right_side_pick wrist_extension"):
                        runtime.move_to_joint_target(
                            {"wrist_extension": float(pose["wrist_extension"])},
                            group_name="arm",
                        )
                    executed_steps.append("wrist_extension")
            else:
                failed_stage = "ready_pose"
                with tolerate_contact("prepare_right_side_pick ready_pose"):
                    runtime.move_to_named_pose(ready_pose)
                executed_steps.append("ready_pose")
        except (
            ManipulationConfigError,
            ManipulationRuntimeUnavailableError,
            ManipulationError,
            ValueError,
            TypeError,
            RuntimeError,
        ) as exc:
            logger.warning("Failed to move to pick-ready pose: %s", exc)
            rec_ok, rec_msg = recover_arm_to_stow(runtime)
            return build_sequence_failure(
                str(exc),
                failed_stage=failed_stage,
                executed_steps=executed_steps,
                recovery_attempted=True,
                recovery_success=rec_ok,
                recovery_message=rec_msg,
            )

        return {
            "success": True,
            "message": "Stretch3 오른쪽 팔 작업 구역 집기 기본자세 준비 완료",
            "ready_pose": ready_pose,
            "open_preset": open_preset,
            "staged": staged,
        }


class PickFrontObjectSkill(BaseSkill):
    """정면 물체를 Stretch3 오른쪽 팔 작업축에 맞춘 뒤 preset sequence로 집기."""

    name = "pick_front_object"
    requires_manipulation_backend = "stretch"
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string", "default": "cup"},
            "angle_deg": {"type": "number", "default": 90.0},
        },
        "additionalProperties": True,
    }
    description = (
        "Stretch3 정면에 있는 물체를 집기 위한 합성 스킬입니다. 팔이 오른쪽에 달린 구조를 "
        "반영해 stow_for_navigation 후 베이스를 기본 90도(align_angle_deg) 회전하여 오른쪽 팔 작업축을 "
        "정면 물체 방향에 맞춘 뒤 `pick_from_right_side_zone` 시퀀스를 실행합니다. "
        "use_active_search=True면 고정 각도 대신 search_object(헤드 pan/tilt 스윗 + 몸통 회전 스윗)로 "
        "실제 물체 방향을 찾아 정렬합니다(target_object/object_name 필요)."
    )
    allow_with_others = False

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        angle_deg = float(params.get("align_angle_deg", params.get("angle_deg", 90.0)))
        do_stow = _bool_param(params.get("do_stow"), True)
        restore_heading = _bool_param(params.get("restore_heading"), False)
        use_active_search = _bool_param(params.get("use_active_search"), False)
        target_object = str(params.get("target_object") or params.get("object_name") or "").strip()
        deadline, _ = _resolve_deadline(self, params, 60.0)

        executed_steps: list[str] = []
        steps: list[dict[str, Any]] = []
        cumulative_angle = 0.0

        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError) as exc:
            logger.warning("Pick front object failed (runtime): %s", exc)
            return build_sequence_failure(
                f"정면 물체 집기 실패: {exc}",
                failed_stage="runtime_init",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        if getattr(runtime.config, "backend", "") != "stretch":
            return build_sequence_failure(
                f"pick_front_object는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
                failed_stage="backend_check",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        if do_stow:
            if _is_deadline_exceeded(deadline):
                return build_sequence_failure(
                    "동작 제한 시간(deadline) 초과",
                    failed_stage="timeout",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                )
            from robo_claw_agent.skills.manipulation_skill import StowForNavigationSkill

            stow_skill = StowForNavigationSkill()
            stow_skill.set_node(self.node)
            stow_result = stow_skill.execute({"timeout_sec": _remaining_timeout(deadline)})
            steps.append({"skill": "stow_for_navigation", "result": stow_result})
            if not stow_result.get("success", False):
                rec_ok, rec_msg = recover_arm_to_stow(runtime)
                return build_sequence_failure(
                    "정면 물체 집기 전 stow 실패",
                    failed_stage="stow",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                    recovery_success=rec_ok,
                    recovery_message=rec_msg,
                    steps=steps,
                )
            executed_steps.append("stow")

        if use_active_search:
            if not target_object:
                return build_sequence_failure(
                    "use_active_search=True인 경우 target_object(또는 object_name)가 필요합니다.",
                    failed_stage="param_validation",
                    executed_steps=executed_steps,
                    recovery_attempted=False,
                    steps=steps,
                )
            if _is_deadline_exceeded(deadline):
                return build_sequence_failure(
                    "동작 제한 시간(deadline) 초과",
                    failed_stage="timeout",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                )
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
                    f"정면 물체 탐색 실패: {search_result.get('message', '')}",
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
            cumulative_angle += float(search_result.get("body_rotation_applied_deg", 0.0) or 0.0)

        align_skill = AlignRightArmToFrontSkill()
        align_skill.set_node(self.node)
        if abs(angle_deg) > 1e-6:
            if _is_deadline_exceeded(deadline):
                return build_sequence_failure(
                    "동작 제한 시간(deadline) 초과",
                    failed_stage="timeout",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                )
            align_result = align_skill.execute(
                {"angle_deg": angle_deg, "timeout_sec": _remaining_timeout(deadline)}
            )
            steps.append({"skill": "align_right_arm_to_front", "result": align_result})
            if not align_result.get("success", False):
                rec_ok, rec_msg = recover_arm_to_stow(runtime)
                return build_sequence_failure(
                    "오른쪽 팔 작업축 정렬 실패",
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
            return build_sequence_failure(
                "동작 제한 시간(deadline) 초과",
                failed_stage="timeout",
                executed_steps=executed_steps,
                recovery_attempted=True,
            )

        pick_skill = PickFromRightSideZoneSkill()
        pick_skill.set_node(self.node)
        pick_params = dict(params)
        pick_params["timeout_sec"] = _remaining_timeout(deadline)
        pick_result = pick_skill.execute(pick_params)
        steps.append({"skill": "pick_from_right_side_zone", "result": pick_result})

        restore_result = None
        if restore_heading and abs(cumulative_angle) > 1e-6:
            restore_result = align_skill.execute(
                {"angle_deg": -cumulative_angle, "timeout_sec": _remaining_timeout(deadline)}
            )
            steps.append({"skill": "restore_heading", "result": restore_result})

        if not pick_result.get("success", False):
            return build_sequence_failure(
                f"정면 물체 집기 실패: {pick_result.get('message', '')}",
                failed_stage="pick",
                executed_steps=executed_steps,
                recovery_attempted=pick_result.get("recovery_attempted", True),
                recovery_success=pick_result.get("recovery_success", True),
                recovery_message=pick_result.get("recovery_message", ""),
                steps=steps,
                pick_result=pick_result,
            )
        executed_steps.append("pick")

        if restore_heading and restore_result and not restore_result.get("success", False):
            return build_sequence_failure(
                "물체는 집었지만 원래 방향 복귀 실패",
                failed_stage="restore_heading",
                executed_steps=executed_steps,
                recovery_attempted=False,
                steps=steps,
            )

        return {
            "success": True,
            "message": "정면 물체를 오른쪽 팔 작업축에 맞춰 집었습니다.",
            "steps": steps,
            "align_angle_deg": angle_deg,
            "restore_heading": restore_heading,
            "used_active_search": use_active_search,
        }
