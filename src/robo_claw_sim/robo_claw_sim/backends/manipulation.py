"""조작(매니퓰레이션) 모델 정의와 MJCF 조각 생성.

RoboClaw의 조작 스택은 ``control_msgs/FollowJointTrajectory`` 로 관절 목표를 보내고,
그리퍼도 별도 액션이 아니라 같은 트래젝토리에 관절로 포함한다
(``manipulation_runtime/stretch_backend.py`` 참고). 그래서 시뮬레이터도 관절 목표
하나로 팔과 그리퍼를 함께 다룬다.

모델은 3축 직교 슬라이더(전진/측면/높이) + 평행 조 그리퍼다. 실제 매니퓰레이터의
관절 구성과는 다르지만, (1) 목표 관절값 → 말단 자세 변환이 자명하고, (2) 그리퍼
폐합으로 실제 접촉이 발생하며, (3) 파지 성공/실패를 접촉으로 판정할 수 있다.
실제 관절 구성(리볼루트 체인)과 IK는 실기 드라이버 검증 영역으로 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..world import SimObject, World

# 파지 판정 여유(m). 조 폭이 물체 지름보다 이 값 이하로 좁혀지고 양쪽 조가 물체에
# 닿았을 때 grasped 로 본다.
GRASP_WIDTH_MARGIN = 0.01

# 물체를 조 사이에 두는지 판정할 때 쓰는 최대 거리(m).
GRASP_REACH_MARGIN = 0.05


@dataclass(frozen=True)
class GraspState:
    """현재 파지 상태(접촉 기반 판정)."""

    grasped_object: str | None = None
    contact_jaws: int = 0
    gripper_value: float = 0.0
    gap_m: float = 0.0

    @property
    def grasped(self) -> bool:
        return self.grasped_object is not None


@dataclass(frozen=True)
class ManipulationConfig:
    """조작 모델 파라미터. 시나리오/파라미터로 일부를 덮어쓸 수 있다."""

    arm_joints: tuple[str, str, str] = ("arm_x", "arm_y", "arm_z")
    gripper_joint: str = "gripper_joint"
    limits: dict[str, tuple[float, float]] = field(
        default_factory=lambda: {
            "arm_x": (-0.1, 0.5),
            "arm_y": (-0.35, 0.35),
            "arm_z": (-0.12, 0.4),
            "gripper_joint": (-0.02, 0.10),
        }
    )
    home: dict[str, float] = field(
        default_factory=lambda: {
            "arm_x": 0.0,
            "arm_y": 0.0,
            "arm_z": 0.0,
            "gripper_joint": 0.10,
        }
    )
    # 조 지오메트리(m). jaw_fixed 는 -y, jaw_moving 은 +y 에 붙는다.
    jaw_offset_y: float = 0.04
    jaw_half_width: float = 0.01
    jaw_half_depth: float = 0.02
    jaw_half_height: float = 0.05
    jaw_forward_x: float = 0.07
    # 물체 지오메트리(m).
    object_radius: float = 0.03
    object_half_height: float = 0.05
    object_mass: float = 0.1

    def clamp(self, name: str, value: float) -> float:
        low, high = self.limits.get(name, (float("-inf"), float("inf")))
        return max(low, min(high, value))

    def joint_names(self) -> tuple[str, ...]:
        return (*self.arm_joints, self.gripper_joint)

    @property
    def ee_body(self) -> str:
        return "ee"

    @property
    def jaw_geoms(self) -> tuple[str, str]:
        return ("jaw_fixed_geom", "jaw_moving_geom")

    def object_body(self, obj: SimObject) -> str:
        return f"object_{obj.name}"

    def object_geom(self, obj: SimObject) -> str:
        return f"object_{obj.name}_geom"

    def object_gap(self, gripper_value: float) -> float:
        """조 안쪽 면 사이 간격(m).

        고정 조는 움직이지 않고 이동 조만 +y 로 벌어지므로, 간격은
        (2*offset - 2*half_width) + gripper_value 다.
        """
        return 2.0 * self.jaw_offset_y - 2.0 * self.jaw_half_width + gripper_value


def _f(value: float) -> str:
    return f"{value + 0.0:.6g}"


def arm_xml(config: ManipulationConfig) -> list[str]:
    """로봇 베이스에 붙는 팔·그리퍼 MJCF(로봇 body 내부에 들어간다)."""
    arm_x, arm_y, arm_z = config.arm_joints
    return _arm_xml(config, arm_x, arm_y, arm_z)


def objects_xml(world: World, config: ManipulationConfig) -> list[str]:
    """파지 대상 물체 MJCF.

    free joint 를 쓰므로 반드시 worldbody 최상위에 넣는다(로봇 body 내부 금지).
    """
    lines: list[str] = []
    for obj in world.objects:
        lines.extend(_object_xml(config, obj))
    return lines


def _arm_xml(config: ManipulationConfig, arm_x: str, arm_y: str, arm_z: str) -> list[str]:
    def limits(name: str) -> str:
        low, high = config.limits[name]
        return f'range="{_f(low)} {_f(high)}"'

    return [
        '      <body name="arm_base" pos="0 0 0">',
        '        <inertial pos="0 0 0" mass="0.2" diaginertia="1e-4 1e-4 1e-4"/>',
        f'        <joint name="{arm_x}" type="slide" axis="1 0 0" {limits(arm_x)}/>',
        '        <body name="arm_link_x" pos="0 0 0">',
        '          <inertial pos="0 0 0" mass="0.2" diaginertia="1e-4 1e-4 1e-4"/>',
        f'          <joint name="{arm_y}" type="slide" axis="0 1 0" {limits(arm_y)}/>',
        '          <body name="arm_link_y" pos="0 0 0">',
        '            <inertial pos="0 0 0" mass="0.2" diaginertia="1e-4 1e-4 1e-4"/>',
        f'            <joint name="{arm_z}" type="slide" axis="0 0 1" {limits(arm_z)}/>',
        '            <body name="ee" pos="0 0 0">',
        '              <geom name="ee_geom" type="box" size="0.02 0.02 0.02"'
        ' rgba="0.25 0.25 0.25 0.4"/>',
        (
            f'              <geom name="jaw_fixed_geom" type="box"'
            f' pos="{_f(config.jaw_forward_x)} {_f(-config.jaw_offset_y)} 0"'
            f' size="{_f(config.jaw_half_depth)} {_f(config.jaw_half_width)}'
            f' {_f(config.jaw_half_height)}" rgba="0.85 0.55 0.2 1"'
            ' condim="4" friction="1.2 0.05 0.001"/>'
        ),
        '              <body name="jaw_moving_body"'
        f' pos="{_f(config.jaw_forward_x)} {_f(config.jaw_offset_y)} 0">',
        (
            f'                <joint name="{config.gripper_joint}" type="slide" axis="0 1 0"'
            f' range="0 {_f(config.limits[config.gripper_joint][1])}"/>'
        ),
        (
            '                <geom name="jaw_moving_geom" type="box" size="'
            f"{_f(config.jaw_half_depth)} {_f(config.jaw_half_width)}"
            f' {_f(config.jaw_half_height)}" rgba="0.85 0.55 0.2 1"'
            ' condim="4" friction="1.2 0.05 0.001"/>'
        ),
        "              </body>",
        "            </body>",
        "          </body>",
        "        </body>",
        "      </body>",
    ]


def _object_xml(config: ManipulationConfig, obj: SimObject) -> list[str]:
    radius = max(config.object_radius, float(getattr(obj, "radius", config.object_radius)))
    return [
        (
            f'    <body name="{config.object_body(obj)}"'
            f' pos="{_f(obj.x)} {_f(obj.y)} {_f(config.object_half_height)}">'
        ),
        "      <freejoint/>",
        (
            f'      <geom name="{config.object_geom(obj)}" type="cylinder"'
            f' size="{_f(radius)} {_f(config.object_half_height)}"'
            f' mass="{_f(config.object_mass)}" rgba="0.2 0.7 0.4 1"'
            ' condim="4" friction="1.2 0.05 0.001"/>'
        ),
        "    </body>",
    ]
