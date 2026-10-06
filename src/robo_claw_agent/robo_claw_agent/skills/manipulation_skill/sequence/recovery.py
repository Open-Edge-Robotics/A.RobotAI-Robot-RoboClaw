"""시퀀스 스킬 실패 복구 및 정리 정책 유틸리티."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def recover_arm_to_stow(runtime: Any, arm_group: str | None = None) -> tuple[bool, str]:
    """실패 후 gripper 상태는 유지하고 arm만 stow로 복귀한다."""
    if runtime is None:
        return False, "manipulation runtime이 없어 arm stow를 시도할 수 없습니다."
    try:
        runtime.move_to_named_pose("stow", group_name=arm_group)
        return True, "arm stow 복구 완료"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Arm stow recovery failed: %s", exc)
        return False, f"arm stow 복구 실패: {exc}"


def recover_head_to_pose(
    skill_or_node: Any, pose_name: str = "travel_head", timeout_sec: float = 5.0
) -> tuple[bool, str]:
    """헤드를 안전 이동 자세(기본 travel_head)로 복귀한다."""
    node = getattr(skill_or_node, "node", skill_or_node)
    if node is None:
        return False, "ROS 노드가 없어 헤드 복귀를 수행할 수 없습니다."
    try:
        from ..head import HeadPanTiltSkill

        head = HeadPanTiltSkill()
        head.set_node(node)
        result = head.execute({"pose_name": pose_name, "timeout_sec": timeout_sec})
        if result.get("success", False):
            return True, f"헤드 {pose_name} 복귀 완료"
        return False, f"헤드 {pose_name} 복귀 실패: {result.get('message', '')}"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Head recovery failed: %s", exc)
        return False, f"헤드 복귀 예외: {exc}"


def cleanup_attempt_state(
    skill: Any,
    runtime: Any,
    *,
    open_preset: str = "open",
    arm_group: str | None = None,
    gripper_group: str | None = None,
) -> tuple[bool, str]:
    """재시도 전에 arm, gripper, head 상태를 안전하게 정리한다."""
    errors: list[str] = []
    if runtime is not None:
        try:
            if open_preset in getattr(runtime.config, "gripper_presets", {}):
                runtime.execute_gripper_preset(open_preset, group_name=gripper_group)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"gripper open 실패: {exc}")

        arm_ok, arm_msg = recover_arm_to_stow(runtime, arm_group)
        if not arm_ok:
            errors.append(arm_msg)

    node = getattr(skill, "node", None)
    if node is not None:
        head_ok, head_msg = recover_head_to_pose(node)
        if not head_ok:
            errors.append(head_msg)

    if errors:
        return False, "; ".join(errors)
    return True, "재시도 전 상태 정리 완료"


def build_sequence_failure(
    message: str,
    failed_stage: str,
    executed_steps: list[str] | None = None,
    recovery_attempted: bool = True,
    recovery_success: bool = True,
    recovery_message: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """실패 단계와 복구 상태를 포함하는 표준 실패 결과 생성."""
    steps = executed_steps if executed_steps is not None else []
    return {
        "success": False,
        "message": message,
        "failed_stage": failed_stage,
        "partial_execution": bool(steps),
        "executed_steps": steps,
        "recovery_attempted": recovery_attempted,
        "recovery_success": recovery_success,
        "recovery_message": recovery_message,
        "requires_recovery": not recovery_success if recovery_attempted else False,
        **extra,
    }
