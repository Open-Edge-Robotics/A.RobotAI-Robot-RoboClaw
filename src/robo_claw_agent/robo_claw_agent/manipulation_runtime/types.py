from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class PoseGoal:
    """MoveIt pose target 표현"""

    frame_id: str
    position: dict[str, float]
    orientation: dict[str, float]
    #: 호출자가 orientation 을 직접 지정했는지 여부. False 면 위치만 지정한 요청이므로
    #: 백엔드가 현재 end-effector 자세(orientation)를 유지한다. 위치만 지정했는데
    #: identity 자세로 해석하면 도달 불가능한 목표가 되어 팔이 최대 신장에서 멈춘다.
    orientation_explicit: bool = True


@dataclass(frozen=True)
class MotionOutcome:
    """백엔드가 실제로 수행한 이동 결과(사후 검증 요약).

    ``already_at_target`` 은 명령이 목표에 도달했지만 발행 시점에 이미 그 자세였음을
    뜻한다. 스킬 계층은 이 값으로 "이동 완료" 와 "이미 그 자세" 를 구분해 보고한다.
    """

    moved: bool = True
    already_at_target: bool = False
    detail: str = ""


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
    # AGX(agx_arm_ctrl) 드라이버 토픽 기반 백엔드 설정.
    # MoveIt 없이 표준 메시지 토픽만 사용한다(배포판 혼용 환경용).
    agx_move_joint_topic: str = "control/move_j"
    agx_move_pose_topic: str = "control/move_p"
    agx_move_linear_topic: str = "control/move_l"
    agx_joint_state_topic: str = "feedback/joint_states"
    agx_tcp_pose_topic: str = "feedback/tcp_pose"
    #: 드라이버 DiagnosticArray. ERROR 레벨이면 팔 계열 명령을 즉시 거부한다
    #: (예: arm_status=NO_SOLUTION 상태에서 30초 대기 후 "도달 실패" 만 보고하는 것 방지).
    agx_arm_status_topic: str = "feedback/arm_status"
    agx_joint_tolerance_rad: float = 0.02
    agx_pose_tolerance_m: float = 0.01
    agx_motion_timeout_sec: float = 30.0
    agx_feedback_timeout_sec: float = 5.0
    agx_home_service: str = "move_home"
    agx_enable_service: str = "enable_agx_arm"
    agx_control_enable_service: str = "control_enable"


class ManipulationBackend(Protocol):
    """실제 planner backend 인터페이스"""

    def move_to_named_pose(self, group_name: str, target_name: str) -> None: ...

    def move_to_joint_target(self, group_name: str, joint_values: Mapping[str, float]) -> None: ...

    def move_to_pose_target(
        self,
        group_name: str,
        pose: PoseGoal,
        end_effector_link: str,
        cartesian: bool = False,
    ) -> None: ...
