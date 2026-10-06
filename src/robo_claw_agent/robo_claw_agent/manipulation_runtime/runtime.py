import logging
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any

from .backend import MoveItPyBackend
from .config import load_manipulation_config
from .exceptions import ManipulationConfigError, ManipulationContactError
from .types import ManipulationBackend, PoseGoal

logger = logging.getLogger(__name__)


@contextmanager
def tolerate_contact(label: str = "move"):
    """guarded contact 로 인한 이동 중단을 치명적 실패가 아닌 것으로 수용.

    준비자세(pre-grasp / ready pose) 이동은 "contact 시점의 현재 자세에서
    이후 단계(시각 서보 등)가 안전하게 시작될 수 있을" 때만 이 컨텍스트로
    감싼다. ``ManipulationContactError``(접촉으로 trajectory 가 조기 종료된
    경우)만 수용하고, 그 외 ``ManipulationError``(goal 거부·tolerance 위반·
    도달 불가·타임아웃 등)는 그대로 전파된다.

    수용 시 warning 로그만 남기고 블록을 정상 종료한다.
    """
    try:
        yield
    except ManipulationContactError as exc:
        logger.warning(
            "Detected motion stopped due to contact — tolerating (tolerate, %s): %s", label, exc
        )


class MoveItManipulationRuntime:
    """manipulation skill에서 사용하는 공통 런타임"""

    def __init__(
        self, node: Any, backend: ManipulationBackend | None = None
    ) -> None:
        self._node = node
        self.config = load_manipulation_config(node)
        self._backend = backend

    def _ensure_backend(self) -> ManipulationBackend:
        if not self.config.enabled:
            raise ManipulationConfigError("manipulation 기능이 비활성화되어 있습니다.")

        if self._backend is None:
            if self.config.backend == "stretch":
                # lazy import: stretch_backend 는 rclpy action/control_msgs 의존.
                from .stretch_backend import StretchDriverBackend

                self._backend = StretchDriverBackend(self._node, self.config)
            else:
                self._backend = MoveItPyBackend(self._node, self.config)

        return self._backend

    def _arm_group(self, group_name: str | None) -> str:
        resolved = str(group_name or self.config.arm_group).strip()
        if not resolved:
            raise ManipulationConfigError("arm group 설정이 비어 있습니다.")
        return resolved

    def _gripper_group(self, group_name: str | None) -> str:
        resolved = str(group_name or self.config.gripper_group).strip()
        if not resolved:
            raise ManipulationConfigError("gripper group 설정이 비어 있습니다.")
        return resolved

    def move_to_named_pose(
        self, pose_name: str, *, group_name: str | None = None
    ) -> dict[str, Any]:
        if not str(pose_name).strip():
            raise ManipulationConfigError("pose_name 이 비어 있습니다.")

        target_name = self.config.named_poses.get(
            str(pose_name).strip(), str(pose_name).strip()
        )
        joint_map = self.config.named_pose_joint_values.get(target_name)
        if joint_map is not None:
            self._validate_joint_limits(joint_map, f"named pose '{target_name}'")
        backend = self._ensure_backend()
        backend.move_to_named_pose(self._arm_group(group_name), target_name)
        return {
            "pose_name": str(pose_name).strip(),
            "moveit_target": target_name,
            "group_name": self._arm_group(group_name),
        }

    def move_to_joint_target(
        self, joint_values: Mapping[str, Any], *, group_name: str | None = None
    ) -> dict[str, Any]:
        normalized = {
            str(name).strip(): float(value)
            for name, value in joint_values.items()
            if str(name).strip()
        }
        if not normalized:
            raise ManipulationConfigError("joint target 이 비어 있습니다.")

        self._validate_joint_limits(normalized, "joint target")
        backend = self._ensure_backend()
        backend.move_to_joint_target(self._arm_group(group_name), normalized)
        return {
            "group_name": self._arm_group(group_name),
            "joint_count": len(normalized),
            "joints": normalized,
        }

    def move_to_pose_target(
        self,
        pose: PoseGoal,
        *,
        group_name: str | None = None,
        end_effector_link: str = "",
        cartesian: bool = False,
    ) -> dict[str, Any]:
        ee_link = str(
            end_effector_link or self.config.end_effector_link or self.config.tool_frame
        ).strip()
        if not ee_link:
            raise ManipulationConfigError(
                "pose target 실행에는 manipulation_end_effector_link 또는 manipulation_tool_frame 설정이 필요합니다."
            )

        backend = self._ensure_backend()
        backend.move_to_pose_target(
            self._arm_group(group_name),
            pose,
            ee_link,
            cartesian=cartesian,
        )
        return {
            "group_name": self._arm_group(group_name),
            "frame_id": pose.frame_id,
            "position": dict(pose.position),
            "orientation": dict(pose.orientation),
            "end_effector_link": ee_link,
            "cartesian": cartesian,
        }

    def _validate_joint_limits(
        self, joint_map: Mapping[str, Any], label: str
    ) -> None:
        """모든 backend dispatch 직전에 robot limits를 강제한다."""
        robot_limits = getattr(self._node, "_robot_limits_dict", None)
        if not robot_limits:
            return
        from robo_claw_agent.skills.limits import check_joint_targets

        limit_error = check_joint_targets(dict(joint_map), robot_limits)
        if limit_error:
            raise ManipulationConfigError(f"{label} limits 위반: {limit_error}")

    def execute_gripper_preset(
        self, preset_name: str, *, group_name: str | None = None
    ) -> dict[str, Any]:
        preset_key = str(preset_name).strip()
        joint_map = self.config.gripper_presets.get(preset_key)
        if not joint_map:
            raise ManipulationConfigError(
                f"gripper preset '{preset_key}' 이 설정되지 않았습니다."
            )

        self._validate_joint_limits(joint_map, f"gripper preset '{preset_key}'")

        backend = self._ensure_backend()
        backend.move_to_joint_target(self._gripper_group(group_name), joint_map)
        return {
            "preset_name": preset_key,
            "group_name": self._gripper_group(group_name),
            "joints": dict(joint_map),
        }
