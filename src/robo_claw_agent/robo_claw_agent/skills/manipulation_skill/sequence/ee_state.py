"""엔드이펙터/프레임 상태 조회 헬퍼 (TF 우선, FK 폴백).

헤드 카메라로 관측한 물체 3D 좌표와 그리퍼의 현재 3D 좌표를 같은 프레임
(base_link)에서 비교하려면 "지금 그리퍼가 어디 있는가"가 필요하다. 로봇은
joint encoder + URDF 로 이 값을 이미 알고 있으므로(``link_grasp_center`` TF),
마커 등으로 다시 추정할 필요가 없다. TF 가 없는 환경(단위 테스트, TF 미발행)
에서는 ``stretch_kinematics._fk_position`` 으로 폴백한다.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from robo_claw_agent.manipulation_runtime.stretch_kinematics import _arm_reach_bearing_rad
from robo_claw_agent.skill_manager import BaseSkill

from .joint_state import _joint_positions

logger = logging.getLogger(__name__)

_DEFAULT_BASE_FRAME = "base_link"
_DEFAULT_EE_FRAME = "link_grasp_center"


def _lookup_frame_origin(
    node: Any,
    target_frame: str,
    source_frame: str,
    *,
    max_age_sec: float | None = None,
) -> tuple[tuple[float, float, float], float | None] | None:
    """``target_frame`` 기준 ``source_frame`` 원점 좌표를 조회한다.

    ``BaseSkill.get_map_pose`` 와 같은 TF 사용 방식(최신 TF = ``rclpy.time.Time()``)을
    따르되, 실패 시 예외를 던지지 않고 ``None`` 을 반환해 호출부가 폴백하도록 한다.

    Args:
        max_age_sec: 지정하면 변환의 stamp 가 이보다 오래된 경우 ``None`` 을 반환한다.
            ArUco 마커처럼 "지금 보이는가"가 중요한 프레임에 사용한다. 시계를 읽을 수
            없는 환경에서는 나이 검사를 건너뛴다(값은 그대로 반환).

    Returns:
        ((x, y, z), age_sec) 또는 실패 시 None. age_sec 은 계산 불가 시 None.
    """
    if node is None:
        return None
    tf_buffer = getattr(node, "_tf_buffer", None)
    if tf_buffer is None:
        return None

    try:
        import rclpy.time

        transform = tf_buffer.lookup_transform(target_frame, source_frame, rclpy.time.Time())
    except Exception as exc:  # noqa: BLE001 - TF 예외 종류가 많아 광범위 캐치가 적절
        logger.debug("TF lookup failed (%s <- %s): %s", target_frame, source_frame, exc)
        return None

    try:
        translation = transform.transform.translation
        xyz = (float(translation.x), float(translation.y), float(translation.z))
    except (AttributeError, TypeError, ValueError) as exc:
        logger.debug("Malformed TF transform (%s <- %s): %s", target_frame, source_frame, exc)
        return None

    age_sec = _transform_age_sec(node, transform)
    if max_age_sec is not None and age_sec is not None and age_sec > max_age_sec:
        logger.debug(
            "TF too old (%s <- %s): age=%.2fs > max=%.2fs",
            target_frame,
            source_frame,
            age_sec,
            max_age_sec,
        )
        return None
    return xyz, age_sec


def _transform_age_sec(node: Any, transform: Any) -> float | None:
    """TransformStamped 의 stamp 와 현재 시각의 차이(초). 계산 불가 시 None."""
    try:
        stamp = transform.header.stamp
        now = node.get_clock().now()
        stamp_ns = int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)
        if stamp_ns == 0:
            # stamp 미설정(static transform 등)은 나이를 판정하지 않는다.
            return None
        return (now.nanoseconds - stamp_ns) / 1e9
    except (AttributeError, TypeError, ValueError):
        return None


def _grasp_center_base_xyz(
    skill: BaseSkill,
    *,
    base_frame: str | None = None,
    ee_frame: str | None = None,
) -> tuple[float, float, float] | None:
    """현재 grasp center 위치를 base_frame 기준으로 반환한다.

    프레임 이름을 주지 않으면 노드 파라미터(``manipulation_base_frame`` /
    ``manipulation_end_effector_link``)에서 읽는다.

    1순위 TF(``base_frame <- ee_frame``), 2순위 joint_states + FK.
    둘 다 실패하면 None.
    """
    if base_frame is None:
        base_frame = skill.get_string_param({}, "manipulation_base_frame", _DEFAULT_BASE_FRAME)
    if ee_frame is None:
        ee_frame = skill.get_string_param(
            {}, "manipulation_end_effector_link", _DEFAULT_EE_FRAME
        )
    origin = _lookup_frame_origin(getattr(skill, "node", None), base_frame, ee_frame)
    if origin is not None:
        return origin[0]

    joints = _joint_positions(skill, ["joint_lift", "wrist_extension", "joint_wrist_yaw"])
    if not joints or "joint_lift" not in joints or "wrist_extension" not in joints:
        return None

    from robo_claw_agent.manipulation_runtime.stretch_kinematics import _fk_position

    position = _fk_position(
        {
            "joint_lift": joints["joint_lift"],
            "wrist_extension": joints["wrist_extension"],
            "joint_wrist_yaw": joints.get("joint_wrist_yaw", 0.0),
        }
    )
    return float(position[0]), float(position[1]), float(position[2])


def _arm_axis_unit() -> tuple[float, float]:
    """팔 작업축(wrist_extension 이 뻗어나가는 방향)의 base_link 단위벡터 (x, y).

    Stretch3 는 팔이 몸통 오른쪽(-y)으로 텔레스코핑하므로 대략 (0, -1) 이다.
    정확한 값은 URDF 체인에서 유도한 ``_arm_reach_bearing_rad`` 를 그대로 쓴다.
    """
    bearing = _arm_reach_bearing_rad()
    return math.cos(bearing), math.sin(bearing)


def _decompose_base_error(
    err_xyz: tuple[float, float, float],
) -> tuple[float, float, float]:
    """base_link 오차 벡터를 (팔 작업축 성분, 축 직교 성분, 수직 성분)으로 분해한다.

    - along: +면 물체가 팔이 뻗는 방향으로 더 멀리 있다 → wrist_extension 을 늘려야 함
    - lateral: 축에 직교한 좌우 오차 → 팔 관절로는 보정 불가, 베이스 회전이 필요
    - vertical: z 오차 → joint_lift 로 보정
    """
    axis_x, axis_y = _arm_axis_unit()
    along = err_xyz[0] * axis_x + err_xyz[1] * axis_y
    # 축을 +90도 회전한 벡터에 사영 = 직교 성분(부호 포함).
    lateral = -err_xyz[0] * axis_y + err_xyz[1] * axis_x
    return along, lateral, err_xyz[2]
