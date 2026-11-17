"""MuJoCo 조작/파지 백엔드 테스트.

파지 성공은 "양쪽 조가 물체에 접촉 + 조 폭이 물체 지름 이하" 로 판정한다.
실제 마찰·질량 튜닝과 실기 상관 검증은 후속 단계이며, 여기서는 판정 로직과
관절 목표 반영이 계약대로 동작하는지 고정한다.
"""

from __future__ import annotations

import pytest
from robo_claw_sim.backends import build_backend
from robo_claw_sim.backends.manipulation import ManipulationConfig
from robo_claw_sim.backends.scene import build_scene_xml
from robo_claw_sim.raycast import ScanConfig
from robo_claw_sim.world import Pose2D, SimObject, Twist2D, World

pytestmark = [pytest.mark.unit, pytest.mark.mujoco]

pytest.importorskip("mujoco", reason="mujoco 미설치(sim-mujoco extra)")

CONFIG = ManipulationConfig()

# 로봇(1.0, 1.0) 정면 0.45m, 바닥에 놓인 물체를 집을 수 있는 자세.
REACH_TARGETS = {"arm_x": 0.35, "arm_y": 0.0, "arm_z": -0.07, "gripper_joint": 0.10}
GRASP_TARGETS = {**REACH_TARGETS, "gripper_joint": -0.02}


def _world(object_x: float = 1.42, object_y: float = 1.0) -> World:
    return World(
        width=6.0,
        height=4.0,
        robot=Pose2D(x=1.0, y=1.0),
        objects=[SimObject(name="cup_1", class_id="cup", x=object_x, y=object_y)],
    )


def _backend(world: World):
    return build_backend(
        "mujoco",
        world=world,
        scan_config=ScanConfig(num_beams=9),
        manipulation=CONFIG,
    )


def _run(backend, targets: dict[str, float], seconds: float = 0.3) -> None:  # noqa: ANN001
    backend.set_joint_targets(targets)
    for _ in range(int(round(seconds / 0.05))):
        backend.step(Twist2D(), 0.05)


def test_scene_includes_arm_gripper_and_objects() -> None:
    xml = build_scene_xml(_world(), CONFIG)

    for name in ("arm_x", "arm_y", "arm_z", "gripper_joint"):
        assert name in xml
    assert "jaw_fixed_geom" in xml
    assert "jaw_moving_geom" in xml
    assert "object_cup_1_geom" in xml


def test_joint_positions_start_at_home() -> None:
    backend = _backend(_world())

    positions = backend.joint_positions()

    for name, value in CONFIG.home.items():
        assert positions[name] == pytest.approx(value)


def test_joint_targets_are_clamped_to_limits() -> None:
    # 물체가 없는 장면에서 관절 한계만 확인한다(물체 접촉이 관절을 밀지 않도록).
    backend = _backend(World(width=6.0, height=4.0, robot=Pose2D(x=1.0, y=1.0)))

    backend.set_joint_targets({"arm_x": 99.0, "gripper_joint": -5.0})
    _run(backend, {"arm_x": 99.0, "gripper_joint": -5.0}, seconds=1.0)

    positions = backend.joint_positions()
    # 직접 qpos 를 쓰므로 스텝 후 한계 근처에서 미세하게 흔들릴 수 있다.
    assert positions["arm_x"] == pytest.approx(CONFIG.limits["arm_x"][1], abs=0.02)
    assert positions["gripper_joint"] == pytest.approx(CONFIG.limits["gripper_joint"][0], abs=0.02)


def test_grasp_succeeds_when_jaws_close_on_object() -> None:
    backend = _backend(_world())

    _run(backend, REACH_TARGETS)
    assert backend.grasp_state().grasped is False

    _run(backend, GRASP_TARGETS)
    state = backend.grasp_state()

    assert state.grasped_object == "cup_1"
    assert state.contact_jaws >= 2
    assert state.gap_m <= 2.0 * CONFIG.object_radius + 0.01


def test_grasp_fails_when_gripper_stays_open() -> None:
    backend = _backend(_world())

    _run(backend, REACH_TARGETS, seconds=0.5)

    state = backend.grasp_state()
    assert state.grasped is False
    assert state.gap_m > 2.0 * CONFIG.object_radius


def test_grasp_fails_when_object_is_out_of_reach() -> None:
    backend = _backend(_world(object_x=1.9))

    _run(backend, GRASP_TARGETS, seconds=0.5)

    assert backend.grasp_state().grasped is False


def test_grasped_object_follows_end_effector() -> None:
    backend = _backend(_world())
    _run(backend, GRASP_TARGETS)
    assert backend.grasp_state().grasped_object == "cup_1"

    before = backend.object_pose("cup_1")
    assert before is not None

    # 팔을 뒤로 빼면 물체도 함께 이동해야 한다(부착 근사).
    _run(backend, {**GRASP_TARGETS, "arm_x": 0.15})
    after = backend.object_pose("cup_1")

    assert after is not None
    assert after.x == pytest.approx(1.0 + 0.15 + CONFIG.jaw_forward_x, abs=0.02)
    assert after.x < before.x - 0.1


def test_releasing_gripper_drops_grasp_state() -> None:
    backend = _backend(_world())
    _run(backend, GRASP_TARGETS)
    assert backend.grasp_state().grasped is True

    _run(backend, {**GRASP_TARGETS, "gripper_joint": 0.10})
    state = backend.grasp_state()

    assert state.grasped is False
    assert state.gap_m > 2.0 * CONFIG.object_radius
