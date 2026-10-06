from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PoseGoal:
    """MoveIt pose target 표현"""

    frame_id: str
    position: dict[str, float]
    orientation: dict[str, float]


@dataclass(frozen=True)
class ManipulationConfig:
    """manipulation 공통 설정 (MoveIt / Stretch 등 백엔드 공통)"""

    enabled: bool = True
    backend: str = "moveit"
    arm_group: str = "arm"
    gripper_group: str = "gripper"
    end_effector_link: str = ""
    base_frame: str = "base_link"
    tool_frame: str = ""
    named_poses: dict[str, str] = field(
        default_factory=lambda: {
            "home": "home",
            "ready": "ready",
            "carry": "carry",
            "stow": "stow",
        }
    )
    # home/stow 외 named pose의 실제 joint 값 사전정의.
    # 값이 dict 형태인 named pose(alias→객체)가 여기 저장된다.
    named_pose_joint_values: dict[str, dict[str, float]] = field(default_factory=dict)
    gripper_presets: dict[str, dict[str, float]] = field(default_factory=dict)
    planning_pipeline: str = ""
    planner_id: str = ""
    cartesian_step: float = 0.01
    velocity_scaling: float = 0.2
    acceleration_scaling: float = 0.2
    default_approach_distance_m: float = 0.1
    default_retreat_distance_m: float = 0.1
    cmd_vel_topic: str = ""


class ManipulationBackend(Protocol):
    """실제 planner backend 인터페이스"""

    def move_to_named_pose(self, group_name: str, target_name: str) -> None: ...

    def move_to_joint_target(
        self, group_name: str, joint_values: Mapping[str, float]
    ) -> None: ...

    def move_to_pose_target(
        self,
        group_name: str,
        pose: PoseGoal,
        end_effector_link: str,
        cartesian: bool = False,
    ) -> None: ...
