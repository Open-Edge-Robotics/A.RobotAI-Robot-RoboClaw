"""gripper_aperture 실측값으로 파지 성공 여부를 검증/재시도하는 헬퍼."""

from __future__ import annotations

from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .joint_state import _joint_positions
from .params import _clamp, _is_cup_target

# gripper_aperture 실측이 commanded 값과 이 이상 벌어지면 "뭔가에 걸려 완전히 닫히지
# 못함 = 파지 성공"으로 판단한다. 실기/시뮬 확인 전 임시값이며, 실측 후 조정 필요
# (STRETCH3_TEST_GUIDE.md 참고).
_EMPTY_CLOSE_GAP_M = 0.01


def _evaluate_grasp_success(
    commanded_aperture_m: float,
    achieved_aperture_m: float | None,
    *,
    empty_close_gap_m: float = _EMPTY_CLOSE_GAP_M,
) -> bool | None:
    """close preset 명령값과 실제 도달값의 차이(gap)로 파지 성공 여부를 판정한다.

    gap(achieved - commanded)이 임계치보다 크면 그리퍼가 물체에 걸려 완전히
    닫히지 못한 것이므로 파지 성공으로 본다. gap이 임계치 이하(명령값까지 거의
    도달)면 허공을 집은 것으로 실패 판정한다. achieved_aperture_m을 얻지
    못했으면(None) 판정 불가로 None을 반환한다.
    """
    if achieved_aperture_m is None:
        return None
    gap = achieved_aperture_m - commanded_aperture_m
    return gap > empty_close_gap_m


def _commanded_gripper_aperture(runtime: Any, preset_name: str) -> float | None:
    preset = runtime.config.gripper_presets.get(preset_name) or {}
    value = preset.get("gripper_aperture")
    return None if value is None else float(value)


def _read_gripper_aperture(skill: BaseSkill) -> float | None:
    joints = _joint_positions(skill, ["gripper_aperture"])
    return None if joints is None else joints.get("gripper_aperture")


def _lift_after_grasp(
    skill: BaseSkill,
    runtime: Any,
    target_object: str,
    *,
    lift_pose: str,
    lift_distance_m: float | None = None,
) -> str:
    """파지 직후에는 wrist_extension을 당기지 않고 lift만 먼저 올린다."""
    if lift_distance_m is None:
        lift_distance_m = 0.18 if _is_cup_target(target_object) else 0.12
    joints = _joint_positions(skill, ["joint_lift"])
    if joints is None or "joint_lift" not in joints:
        raise RuntimeError("파지 후 lift를 검증할 joint state를 읽을 수 없습니다.")
    target_lift = _clamp(joints["joint_lift"] + lift_distance_m, 0.0, 1.1)
    runtime.move_to_joint_target({"joint_lift": target_lift}, group_name="arm")
    return f"vertical_lift:{target_lift:.3f}"


def _close_gripper_with_grasp_verification(
    skill: BaseSkill,
    runtime: Any,
    *,
    close_preset: str,
    open_preset: str,
    gripper_group: str | None,
    reacquire: Any,
    retry_on_empty_grasp: bool,
    max_grasp_retries: int,
) -> tuple[bool | None, int]:
    """close → gripper_aperture로 파지 검증 → 실패 시 (open→reacquire→close) 재시도.

    reacquire는 인자 없이 호출되는 콜백으로, 재시도 직전 물체를 다시 찾거나
    보정하는 스킬 결과 dict({"success": bool, ...})를 반환해야 한다.

    Returns:
        (grasp_verified, retry_count). grasp_verified는 achieved aperture를
        읽지 못하면 None(미검증), 그 외에는 bool.
    """
    commanded = _commanded_gripper_aperture(runtime, close_preset)

    def _close_and_check() -> bool | None:
        runtime.execute_gripper_preset(close_preset, group_name=gripper_group)
        if commanded is None:
            return None
        achieved = _read_gripper_aperture(skill)
        return _evaluate_grasp_success(commanded, achieved)

    grasp_verified = _close_and_check()
    retry_count = 0
    while grasp_verified is False and retry_on_empty_grasp and retry_count < max_grasp_retries:
        retry_count += 1
        runtime.execute_gripper_preset(open_preset, group_name=gripper_group)
        reacquire_result = reacquire()
        if not reacquire_result.get("success", False):
            break
        grasp_verified = _close_and_check()

    return grasp_verified, retry_count
