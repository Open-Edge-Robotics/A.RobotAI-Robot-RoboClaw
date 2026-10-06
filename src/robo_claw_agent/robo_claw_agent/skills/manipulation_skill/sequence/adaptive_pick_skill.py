import logging
import math
from typing import Any

from robo_claw_agent.manipulation_runtime import (
    ManipulationConfigError,
    ManipulationError,
    ManipulationRuntimeUnavailableError,
)
from robo_claw_agent.manipulation_runtime.stretch_kinematics import _ik_position
from robo_claw_agent.skill_manager import BaseSkill
from robo_claw_agent.skills.navigation_skill.move_relative import MoveRelativeSkill

from ..core import _get_runtime
from .align_skill import AlignRightArmToFrontSkill
from .fixed_pattern_grasp import FixedPatternGraspSkill
from .gripper_target_skills import ObserveGripperTargetSkill
from .head_assist import HeadAssist, lateral_align
from .params import (
    _bool_param,
    _clamp_int,
    _is_deadline_exceeded,
    _remaining_timeout,
    _resolve_deadline,
)
from .recovery import (
    build_sequence_failure,
    cleanup_attempt_state,
    recover_head_to_pose,
)
from .right_side_pick_skills import PrepareRightSidePickSkill
from .search_skill import SearchObjectSkill
from .vla_pick_skills import VLABasedPickGripperObjectSkill

logger = logging.getLogger(__name__)


def _approach_vector_to_reach(
    obj_base_xyz: tuple[float, float, float],
    *,
    target_distance_m: float = 0.42,
) -> dict[str, float] | None:
    """현재 base_link 좌표에서 팔이 닿을 안전 거리까지 이동할 상대 벡터."""
    x, y, _ = obj_base_xyz
    distance = math.hypot(x, y)
    move_distance = distance - target_distance_m
    if distance <= 1e-6 or move_distance <= 0.05:
        return None
    return {
        "forward": move_distance * x / distance,
        "lateral": move_distance * y / distance,
    }


class AdaptivePickObjectSkill(BaseSkill):
    """target_object 파지 요청을 받아 탐색부터 이동자세 복귀까지 전 과정을 수행."""

    name = "adaptive_pick_object"
    requires_manipulation_backend = "stretch"
    description = (
        "Stretch3에서 target_object를 집기 위한 최상위 스킬입니다. 다음 순서로 "
        "동작합니다: (1) search_object로 헤드 pan/tilt 스윕과 몸통 회전 스윕을 "
        "결합해 물체 방향을 능동 탐색, (2) 계산된 각도로 몸통을 물체 쪽(오른쪽 팔 "
        "작업축)으로 정렬, (3) prepare_right_side_pick으로 집기 준비자세 이동, "
        "(4) 헤드 카메라 3D 관측으로 물체 위치/좌우 정렬, (5) 기본적으로 "
        "fixed_pattern_grasp(표면 유형별 결정론적 고정 패턴)로 파지, "
        "(6) 파지 후 들어올려 이동 자세로 전환, (7) 헤드를 주행 안전 자세로 "
        "복귀, (8) 결과 보고. use_fixed_pattern_grasp=False를 주면 기존 그리퍼 "
        "카메라 서보 + VLA 경로로 동작합니다. skip_search=True로 탐색 단계를 "
        "건너뛸 수 있습니다. "
        "max_attempts(기본 1, 재시도 없음)를 2 이상으로 주면, 정렬/준비/그리퍼 확인/"
        "파지 중 어느 단계에서 실패하든 처음(탐색)부터 다시 시도합니다 — 물체 위치 "
        "추정이 1회성 스냅샷이라 실패 원인이 오추정일 수 있는 경우에 유용합니다. "
        "기본값은 기존과 동일하게 재시도 없이 즉시 실패를 반환합니다."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target_object": {"type": "string", "default": "cup"},
            "max_attempts": {"type": "integer", "default": 1, "minimum": 1},
            "skip_search": {"type": "boolean", "default": False},
            "use_head_assist": {"type": "boolean", "default": True},
            "use_fixed_pattern_grasp": {"type": "boolean", "default": True},
            "restore_head_after": {"type": "boolean", "default": True},
            "return_to_start_on_fail": {"type": "boolean", "default": False},
            "approach_target_distance_m": {"type": "number", "default": 0.42, "minimum": 0.25},
        },
        "required": ["target_object"],
        "additionalProperties": True,
    }
    side_effects = ("base_motion", "arm_motion", "gripper_motion")
    allow_with_others = False

    def validate_params(self, params: dict[str, Any]) -> bool:
        return bool(str(params.get("target_object") or "").strip())

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        target_object = str(params.get("target_object") or "cup").strip()
        skip_search = _bool_param(params.get("skip_search"), False)
        restore_head_after = _bool_param(params.get("restore_head_after"), True)
        deadline, total_timeout = _resolve_deadline(self, params, 90.0)

        executed_steps: list[str] = []
        steps: list[dict[str, Any]] = []

        try:
            runtime = _get_runtime(self.node)
        except (ManipulationConfigError, ManipulationRuntimeUnavailableError) as exc:
            logger.warning("Adaptive pick failed (runtime): %s", exc)
            return build_sequence_failure(
                f"적응형 집기 실패: {exc}",
                failed_stage="runtime_init",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        if getattr(runtime.config, "backend", "") != "stretch":
            return build_sequence_failure(
                f"adaptive_pick_object는 Stretch 백엔드 전용 스킬입니다 (현재 backend: {runtime.config.backend})",
                failed_stage="backend_check",
                executed_steps=executed_steps,
                recovery_attempted=False,
            )

        # 헤드 카메라 보조는 Stretch3 전용(head_pan_tilt 및 팔 작업축 분해가
        # Stretch 운동학에 묶여 있다). 런타임을 못 얻으면 보조 없이 진행한다.
        use_head_assist = _bool_param(params.get("use_head_assist"), True)
        if use_head_assist:
            try:
                use_head_assist = runtime.config.backend == "stretch"
            except Exception:
                use_head_assist = False
        # 기본 경로(옵션 B): 근접 그리퍼 비주얼 서보 대신 헤드 3D 관측 기반 결정론적
        # 고정 패턴 파지. False로 두면 기존 그리퍼 서보 + VLA 경로로 동작한다.
        use_fixed_pattern_grasp = _bool_param(params.get("use_fixed_pattern_grasp"), True)
        return_to_start_on_fail = _bool_param(params.get("return_to_start_on_fail"), False)
        max_attempts = _clamp_int(params.get("max_attempts", 1), 1, 5, 1)

        steps: list[dict[str, Any]] = []

        def _finish(result: dict[str, Any]) -> dict[str, Any]:
            if restore_head_after:
                head_ok, head_msg = recover_head_to_pose(
                    self.node, "travel_head", timeout_sec=_remaining_timeout(deadline)
                )
                steps.append(
                    {
                        "skill": "head_pan_tilt",
                        "pose_name": "travel_head",
                        "success": head_ok,
                        "message": head_msg,
                    }
                )
                if not head_ok:
                    result["head_restoration_success"] = False
                    result["head_restoration_message"] = head_msg
            result["target_object"] = target_object
            result["steps"] = steps
            return result

        def _run_attempt(attempt: int) -> dict[str, Any]:
            """탐색→정렬→준비→확인→집기 1회 시도. 실패 시 success=False 결과를 반환."""
            cumulative_angle = 0.0
            executed_steps: list[str] = []

            def _restore_heading_if_needed() -> None:
                if return_to_start_on_fail and abs(cumulative_angle) > 1e-6:
                    align = AlignRightArmToFrontSkill()
                    align.set_node(self.node)
                    restore_result = align.execute(
                        {
                            "angle_deg": -cumulative_angle,
                            "timeout_sec": _remaining_timeout(deadline),
                        }
                    )
                    steps.append(
                        {
                            "skill": "restore_heading",
                            "attempt": attempt,
                            "angle_deg": -cumulative_angle,
                            "result": restore_result,
                        }
                    )

            if _is_deadline_exceeded(deadline):
                return build_sequence_failure(
                    "동작 제한 시간(deadline) 초과",
                    failed_stage="timeout",
                    executed_steps=executed_steps,
                    recovery_attempted=False,
                )

            # 1) 물체 방향 능동 탐색 (헤드 pan/tilt 스윕 + 필요 시 몸통 회전 스윕).
            align_angle_deg: float | None = None
            if not skip_search:
                search = SearchObjectSkill()
                search.set_node(self.node)
                search_params = dict(params)
                search_params["target_object"] = target_object
                search_params["timeout_sec"] = _remaining_timeout(deadline)
                search_result = search.execute(search_params)
                steps.append(
                    {"skill": "search_object", "attempt": attempt, "result": search_result}
                )
                if not search_result.get("success", False):
                    return build_sequence_failure(
                        f"'{target_object}' 탐색 실패: {search_result.get('message', '')}",
                        failed_stage="search",
                        executed_steps=executed_steps,
                        recovery_attempted=False,
                    )
                executed_steps.append("search")
                align_angle_deg = search_result.get("align_angle_deg")
                cumulative_angle = float(search_result.get("body_rotation_applied_deg", 0.0) or 0.0)

            # 2) 계산된 각도로 몸통을 물체 쪽(오른쪽 팔 작업축)으로 정렬.
            if align_angle_deg is not None and abs(align_angle_deg) > 1e-6:
                if _is_deadline_exceeded(deadline):
                    return build_sequence_failure(
                        "동작 제한 시간(deadline) 초과",
                        failed_stage="timeout",
                        executed_steps=executed_steps,
                        recovery_attempted=False,
                    )
                align = AlignRightArmToFrontSkill()
                align.set_node(self.node)
                align_result = align.execute(
                    {
                        "angle_deg": align_angle_deg,
                        "timeout_sec": _remaining_timeout(deadline),
                    }
                )
                steps.append(
                    {
                        "skill": "align_right_arm_to_front",
                        "attempt": attempt,
                        "angle_deg": align_angle_deg,
                        "result": align_result,
                    }
                )
                if not align_result.get("success", False):
                    return build_sequence_failure(
                        "탐색된 물체 방향으로 몸통 정렬 실패",
                        failed_stage="align",
                        executed_steps=executed_steps,
                        recovery_attempted=False,
                    )
                executed_steps.append("align")
                cumulative_angle += align_angle_deg

            if _is_deadline_exceeded(deadline):
                return build_sequence_failure(
                    "동작 제한 시간(deadline) 초과",
                    failed_stage="timeout",
                    executed_steps=executed_steps,
                    recovery_attempted=False,
                )

            # 3) 집기 준비자세(그리퍼 오픈 + ready pose)로 이동.
            prepare = PrepareRightSidePickSkill()
            prepare.set_node(self.node)
            prepare_params = dict(params)
            prepare_params["timeout_sec"] = _remaining_timeout(deadline)
            prepare_result = prepare.execute(prepare_params)
            steps.append(
                {"skill": "prepare_right_side_pick", "attempt": attempt, "result": prepare_result}
            )
            if not prepare_result.get("success", False):
                _restore_heading_if_needed()
                return build_sequence_failure(
                    "집기 기본자세 준비 실패",
                    failed_stage="prepare",
                    executed_steps=executed_steps,
                    recovery_attempted=prepare_result.get("recovery_attempted", True),
                    recovery_success=prepare_result.get("recovery_success", True),
                    recovery_message=prepare_result.get("recovery_message", ""),
                )
            executed_steps.append("prepare")

            # 3.5) 헤드를 파지 구역 감시 자세로 옮기고 ArUco 잔차를 래치한다.
            head_assist = HeadAssist(self, params, enabled=use_head_assist)
            obj_base_xyz: tuple[float, float, float] | None = None
            if use_head_assist:
                pose_info = head_assist.ensure_pose()
                steps.append(
                    {
                        "skill": "head_pan_tilt",
                        "attempt": attempt,
                        "purpose": "head_assist",
                        **pose_info,
                    }
                )

                # 3.6~3.7) 헤드 3D 관측으로 좌우 오차를 베이스 미세회전으로 없앤다.
                obj_base_xyz, head_obs = head_assist.observe()
                steps.append(
                    {"skill": "observe_head_target", "attempt": attempt, "result": head_obs}
                )
                if obj_base_xyz is not None:
                    # 베이스를 회전하면 기존 base_link 좌표는 stale 상태가 된다.
                    # 최대 2회까지 재관측해 잔여 방위 오차를 보정한다.
                    for alignment_round in range(2):
                        try:
                            rotation_info = lateral_align(
                                self, _get_runtime(self.node), params, obj_base_xyz
                            )
                        except (
                            ManipulationConfigError,
                            ManipulationRuntimeUnavailableError,
                            ManipulationError,
                            ValueError,
                            TypeError,
                        ) as exc:
                            rotation_info = {"rotated": False, "reason": str(exc)}
                        steps.append(
                            {
                                "skill": "lateral_align",
                                "attempt": attempt,
                                "round": alignment_round + 1,
                                "result": rotation_info,
                            }
                        )
                        if not rotation_info.get("rotated", False):
                            break
                        cumulative_angle += float(rotation_info.get("angle_deg", 0.0) or 0.0)
                        if alignment_round == 1:
                            break
                        refreshed_xyz, refreshed_obs = head_assist.observe()
                        steps.append(
                            {
                                "skill": "observe_head_target",
                                "attempt": attempt,
                                "purpose": "post_lateral_reobserve",
                                "result": refreshed_obs,
                            }
                        )
                        if refreshed_xyz is None:
                            break
                        obj_base_xyz = refreshed_xyz

                    # 회전은 방위만 맞출 뿐 목표까지의 거리를 줄이지 않는다. 물체가
                    # 여전히 IK 밖이면 현재 관측 벡터 방향으로 접근한 뒤 재관측한다.
                    if obj_base_xyz is not None and _ik_position(obj_base_xyz) is None:
                        approach_params = _approach_vector_to_reach(
                            obj_base_xyz,
                            target_distance_m=max(
                                0.25, float(params.get("approach_target_distance_m", 0.42))
                            ),
                        )
                        if approach_params is not None:
                            approach = MoveRelativeSkill()
                            approach.set_node(self.node)
                            approach_result = approach.execute(
                                {
                                    **approach_params,
                                    "timeout_sec": _remaining_timeout(deadline),
                                }
                            )
                            steps.append(
                                {
                                    "skill": "move_relative",
                                    "attempt": attempt,
                                    "purpose": "approach_grasp_target",
                                    "params": approach_params,
                                    "result": approach_result,
                                }
                            )
                            if not approach_result.get("success", False):
                                _restore_heading_if_needed()
                                return build_sequence_failure(
                                    "물체가 팔 도달 범위 밖이고 접근 이동에 실패했습니다: "
                                    f"{approach_result.get('message', '')}",
                                    failed_stage="approach",
                                    executed_steps=executed_steps,
                                    recovery_attempted=True,
                                )
                            refreshed_xyz, refreshed_obs = head_assist.observe()
                            steps.append(
                                {
                                    "skill": "observe_head_target",
                                    "attempt": attempt,
                                    "purpose": "post_approach_reobserve",
                                    "result": refreshed_obs,
                                }
                            )
                            if refreshed_xyz is not None:
                                obj_base_xyz = refreshed_xyz

            # 4)+5)+6) 파지 경로 분기.
            if _is_deadline_exceeded(deadline):
                return build_sequence_failure(
                    "동작 제한 시간(deadline) 초과",
                    failed_stage="timeout",
                    executed_steps=executed_steps,
                    recovery_attempted=True,
                )

            if use_fixed_pattern_grasp:
                pick = FixedPatternGraspSkill()
                pick.set_node(self.node)
                pick_params = dict(params)
                pick_params["target_object"] = target_object
                pick_params["timeout_sec"] = _remaining_timeout(deadline)
                pick_result = pick.execute(pick_params)
                steps.append(
                    {
                        "skill": "fixed_pattern_grasp",
                        "attempt": attempt,
                        "result": pick_result,
                    }
                )
            else:
                # 4) 그리퍼 카메라로 물체 재확인.
                observe = ObserveGripperTargetSkill()
                observe.set_node(self.node)
                observe_params = dict(params)
                observe_params["target_object"] = target_object
                observe_params["timeout_sec"] = _remaining_timeout(deadline)
                observe_result = observe.execute(observe_params)
                steps.append(
                    {
                        "skill": "observe_gripper_target",
                        "attempt": attempt,
                        "result": observe_result,
                    }
                )
                if not observe_result.get("success", False):
                    _restore_heading_if_needed()
                    return build_sequence_failure(
                        (
                            "정렬/준비 후에도 그리퍼 카메라에서 "
                            f"'{target_object}'를 확인하지 못했습니다: {observe_result.get('message', '')}"
                        ),
                        failed_stage="observe_gripper",
                        executed_steps=executed_steps,
                        recovery_attempted=True,
                    )

                # 5)+6) 그리퍼 카메라 기반 팔 미세조정 및 파지, 파지 후 들어올려 이동 자세로 전환.
                pick = VLABasedPickGripperObjectSkill()
                pick.set_node(self.node)
                pick_params = dict(params)
                pick_params["target_object"] = target_object
                pick_params.setdefault("move_ready", False)
                pick_params["timeout_sec"] = _remaining_timeout(deadline)
                if use_head_assist:
                    pick_params["head_assist_ready"] = True
                pick_result = pick.execute(pick_params)
                steps.append(
                    {
                        "skill": "vla_pick_gripper_object",
                        "attempt": attempt,
                        "result": pick_result,
                    }
                )

            if pick_result.get("success", False):
                return {"success": True, "message": f"적응형 집기 완료: {target_object}"}

            _restore_heading_if_needed()
            return build_sequence_failure(
                f"대상은 확인했지만 집기 실패: {pick_result.get('message', '')}",
                failed_stage=pick_result.get("failed_stage", "pick"),
                executed_steps=executed_steps,
                recovery_attempted=pick_result.get("recovery_attempted", True),
                recovery_success=pick_result.get("recovery_success", True),
                recovery_message=pick_result.get("recovery_message", ""),
                pick_result=pick_result,
            )

        attempt_result: dict[str, Any] = {"success": False, "message": "집기 시도 실패"}
        for attempt in range(1, max_attempts + 1):
            attempt_result = _run_attempt(attempt)
            if attempt_result.get("success", False):
                return _finish(attempt_result)
            if attempt < max_attempts:
                # 다음 시도 전 이전 시도의 arm, gripper, head 상태를 깨끗이 정리
                clean_ok, clean_msg = cleanup_attempt_state(
                    self, runtime, open_preset=str(params.get("open_preset") or "open")
                )
                steps.append(
                    {
                        "skill": "retry_pending",
                        "attempt": attempt,
                        "message": attempt_result.get("message", ""),
                        "cleanup_success": clean_ok,
                        "cleanup_message": clean_msg,
                    }
                )

        attempt_result["attempts"] = max_attempts
        return _finish(attempt_result)
