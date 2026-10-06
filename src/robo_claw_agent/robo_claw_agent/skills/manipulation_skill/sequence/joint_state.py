"""joint_states 토픽에서 관절 값을 읽고 파생 값을 합성하는 헬퍼."""

from __future__ import annotations

import logging

from sensor_msgs.msg import JointState

from robo_claw_agent.skill_manager import BaseSkill

# Stretch3 그리퍼 손가락 링크 길이(m). gripper_aperture를 finger joint 각도에서
# 역산할 때 사용한다.
_STRETCH_GRIPPER_FINGER_LENGTH_M = 0.171


def _joint_positions(skill: BaseSkill, joint_names: list[str]) -> dict[str, float] | None:
    topic = "/joint_states"
    msg = skill.wait_for_message(JointState, topic, timeout_sec=1.0)
    if msg is None:
        topic = "/stretch/joint_states"
        msg = skill.wait_for_message(JointState, topic, timeout_sec=1.0)
    if msg is None:
        return None

    positions: dict[str, float] = {}
    for name in joint_names:
        if name not in msg.name:
            continue
        idx = msg.name.index(name)
        if idx < len(msg.position):
            positions[name] = float(msg.position[idx])

    if "wrist_extension" in joint_names and "wrist_extension" not in positions:
        arm_segments = ("joint_arm_l0", "joint_arm_l1", "joint_arm_l2", "joint_arm_l3")
        segment_values = []
        for segment in arm_segments:
            if segment not in msg.name:
                continue
            idx = msg.name.index(segment)
            if idx < len(msg.position):
                segment_values.append(float(msg.position[idx]))
        if len(segment_values) == len(arm_segments):
            positions["wrist_extension"] = sum(segment_values)

    if "gripper_aperture" in joint_names and "gripper_aperture" not in positions:
        finger_joints = ("joint_gripper_finger_left", "joint_gripper_finger_right")
        finger_values = []
        for finger in finger_joints:
            if finger not in msg.name:
                continue
            idx = msg.name.index(finger)
            if idx < len(msg.position):
                finger_values.append(float(msg.position[idx]))
        if finger_values:
            finger_rad = sum(finger_values) / len(finger_values)
            positions["gripper_aperture"] = 2.0 * finger_rad * _STRETCH_GRIPPER_FINGER_LENGTH_M

    return positions


def _log_achieved_gripper_aperture(
    skill: BaseSkill, logger: logging.Logger, context: str
) -> None:
    """gripper open 명령 직후 실제 도달한 aperture를 로그로 남긴다.

    stretch_core가 범위를 벗어난 목표를 에러 없이 실제 하드웨어 상한으로
    clamp하기 때문에, preset 설정값만으로는 실제 최대 개방폭을 알 수 없다.
    이 로그로 로봇이 실제 도달한 값을 확인할 수 있다.
    """
    positions = _joint_positions(skill, ["gripper_aperture"])
    aperture = positions.get("gripper_aperture") if positions else None
    if aperture is None:
        logger.warning("%s: failed to read gripper_aperture (joint_states not received)", context)
        return
    logger.info("%s: actual achieved gripper_aperture=%.4fm", context, aperture)
