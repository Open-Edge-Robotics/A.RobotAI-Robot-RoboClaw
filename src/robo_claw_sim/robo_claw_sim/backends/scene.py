"""시나리오 세계를 MuJoCo 장면(MJCF)으로 변환한다(순수 파이썬, mujoco 비의존).

별도 MJCF 자산을 저장소에 두지 않고 시나리오 YAML 하나로 세계를 만든다. 그래야
kinematic 백엔드와 mujoco 백엔드가 **같은 시나리오·같은 판정**을 공유한다.

로봇은 평면주행 베이스로 모델링한다: 자유 관절(free joint) + 원기둥 지오메트리.
바퀴·서스펜션은 넣지 않는다(명령을 속도로 직접 주입하므로 필요 없고, 접촉 파라미터
튜닝 없이도 재현성이 유지된다). 지오메트리는 z=0 바닥에 닿지 않게 띄워 두어 접촉은
벽·장애물에서만 발생한다.
"""

from __future__ import annotations

from xml.sax.saxutils import quoteattr

from ..world import BoxObstacle, CircleObstacle, World
from .manipulation import ManipulationConfig, arm_xml, objects_xml

# 물리 스텝(초). 20Hz 노드 갱신 대비 4배 세밀도면 접촉 판정에 충분하다.
TIMESTEP = 0.005
# 벽 두께/높이(m). 벽은 시나리오 경계 바로 바깥에 세운다.
WALL_THICKNESS = 0.1
WALL_HEIGHT = 1.0
# 로봇 베이스 형상(m). 바닥과 접촉하지 않도록 z 를 띄운다.
ROBOT_Z = 0.12
ROBOT_HALF_HEIGHT = 0.06

# MuJoCo 접촉 판정에서 "장애물"로 취급할 지오메트리 이름 접두사.
WALL_PREFIX = "wall_"
OBSTACLE_PREFIX = "obstacle_"
ROBOT_GEOM_NAME = "base_geom"
ROBOT_BODY_NAME = "base"
# 로봇 헤드 카메라(정적 TF base_link→camera_link 와 같은 위치/자세).
HEAD_CAMERA_NAME = "head_cam"


def build_scene_xml(
    world: World,
    manipulation: ManipulationConfig | None = None,
) -> str:
    """``World`` 배치를 MJCF 문자열로 만든다.

    ``manipulation`` 을 주면 로봇에 3축 팔 + 그리퍼를 붙이고, 파지 대상 객체를
    free body 로 장면에 넣는다(기본값 None 은 주행 전용 장면).
    """
    lines: list[str] = [
        '<mujoco model="robo_claw_sim">',
        '  <compiler angle="radian" autolimits="true"/>',
        f'  <option timestep="{TIMESTEP}" gravity="0 0 -9.81" integrator="implicitfast"/>',
        "  <visual>",
        '    <global offwidth="640" offheight="480"/>',
        "  </visual>",
        "  <worldbody>",
        (
            f'    <geom name="floor" type="plane" pos="{_f(world.width / 2)} {_f(world.height / 2)} 0"'
            f' size="{_f(world.width / 2)} {_f(world.height / 2)} 0.05"'
            ' rgba="0.55 0.55 0.55 1"/>'
        ),
        '    <light name="sun" pos="0 0 4" dir="0 0 -1" directional="true"/>',
    ]
    lines.extend(_wall_geoms(world))
    lines.extend(_obstacle_geoms(world))
    lines.extend(_robot_body(world, manipulation))
    if manipulation is not None:
        lines.extend(objects_xml(world, manipulation))
    lines.append("  </worldbody>")
    lines.append("</mujoco>")
    return "\n".join(lines) + "\n"


def _wall_geoms(world: World) -> list[str]:
    """경계 밖으로 나가지 못하게 하는 4개 벽."""
    half_t = WALL_THICKNESS / 2.0
    half_h = WALL_HEIGHT / 2.0
    z = half_h
    width, height = world.width, world.height
    geoms = []
    specs = [
        (WALL_PREFIX + "west", -half_t, height / 2.0, half_t, height / 2.0 + WALL_THICKNESS),
        (WALL_PREFIX + "east", width + half_t, height / 2.0, half_t, height / 2.0 + WALL_THICKNESS),
        (WALL_PREFIX + "south", width / 2.0, -half_t, width / 2.0 + WALL_THICKNESS, half_t),
        (WALL_PREFIX + "north", width / 2.0, height + half_t, width / 2.0 + WALL_THICKNESS, half_t),
    ]
    for name, x, y, size_x, size_y in specs:
        geoms.append(
            f'    <geom name={quoteattr(name)} type="box" pos="{_f(x)} {_f(y)} {_f(z)}"'
            f' size="{_f(size_x)} {_f(size_y)} {_f(half_h)}" rgba="0.75 0.72 0.68 1"/>'
        )
    return geoms


def _obstacle_geoms(world: World) -> list[str]:
    geoms = []
    for index, obstacle in enumerate(world.obstacles):
        name = _safe_name(OBSTACLE_PREFIX, getattr(obstacle, "name", "") or str(index))
        z = WALL_HEIGHT / 2.0
        if isinstance(obstacle, BoxObstacle):
            geoms.append(
                f'    <geom name={quoteattr(name)} type="box"'
                f' pos="{_f(obstacle.x)} {_f(obstacle.y)} {_f(z)}"'
                f' size="{_f(obstacle.width / 2)} {_f(obstacle.height / 2)} {_f(z)}"'
                ' rgba="0.6 0.45 0.35 1"/>'
            )
        elif isinstance(obstacle, CircleObstacle):
            geoms.append(
                f'    <geom name={quoteattr(name)} type="cylinder"'
                f' pos="{_f(obstacle.x)} {_f(obstacle.y)} {_f(z)}"'
                f' size="{_f(obstacle.radius)} {_f(z)}"'
                ' rgba="0.6 0.45 0.35 1"/>'
            )
    return geoms


def _robot_body(world: World, manipulation: ManipulationConfig | None = None) -> list[str]:
    robot = world.robot
    lines = [
        f'    <body name="{ROBOT_BODY_NAME}" pos="{_f(robot.x)} {_f(robot.y)} {_f(ROBOT_Z)}">',
        '      <freejoint name="base_free"/>',
        (
            f'      <geom name="{ROBOT_GEOM_NAME}" type="cylinder"'
            f' size="{_f(world.robot_radius)} {_f(ROBOT_HALF_HEIGHT)}"'
            ' rgba="0.2 0.45 0.85 1"/>'
        ),
        '      <site name="scan_site" pos="0 0 0" size="0.01"/>',
        (f'      <camera name="{HEAD_CAMERA_NAME}" pos="0.15 0 0.23" euler="-0.26 0 0"/>'),
    ]
    if manipulation is not None:
        lines.extend(arm_xml(manipulation))
    lines.append("    </body>")
    return lines


def _safe_name(prefix: str, raw: str) -> str:
    """지오메트리 이름을 XML/식별자 안전한 형태로 만든다."""
    sanitized = "".join(ch if (ch.isalnum() or ch == "_") else "_" for ch in raw).strip("_")
    return f"{prefix}{sanitized}" if sanitized else f"{prefix}unnamed"


def _f(value: float) -> str:
    """MJCF 실수 표기. -0.0 을 0 으로 정규화해 diff 잡음을 줄인다."""
    return f"{value + 0.0:.6g}"
