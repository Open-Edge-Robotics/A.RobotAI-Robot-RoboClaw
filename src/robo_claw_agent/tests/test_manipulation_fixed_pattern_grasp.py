"""표면 분류와 고정 패턴 파지 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    AdaptivePickObjectSkill,
    ClassifyObjectSurfaceSkill,
    FakeBackend,
    FixedPatternGraspSkill,
    _make_node,
    _stub_head_observation,
    _stub_joint_positions,
    grasp_verification,
    sequence,
)

# ── 고정 패턴 파지 / 표면 분류 스킬 (옵션 B) ────────────────────────────────

def _expect_joints(xyz):
    """FixedPatternGraspSkill._ik_reach와 동일한 클램프 적용 기대 관절값."""
    from robo_claw_agent.manipulation_runtime.stretch_kinematics import _ik_position

    lift, ext, yaw = _ik_position(xyz)
    return {
        "joint_lift": max(0.0, min(1.1, float(lift))),
        "wrist_extension": max(0.0, min(0.52, float(ext))),
        "joint_wrist_yaw": float(yaw),
    }


def test_classify_object_surface_from_object_base_xyz():
    skill = ClassifyObjectSurfaceSkill()
    skill.set_node(_make_node({}))
    floor = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.12}}
    )
    assert floor["success"] is True
    assert floor["surface"] == "floor"
    assert floor["surface_from"] == "object_base_xyz"

    elevated = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.45}}
    )
    assert elevated["success"] is True
    assert elevated["surface"] == "elevated"


def test_classify_object_surface_uses_hint():
    skill = ClassifyObjectSurfaceSkill()
    skill.set_node(_make_node({}))
    result = skill.execute({"target_object": "cup", "surface_hint": "elevated"})
    assert result["success"] is True
    assert result["surface"] == "elevated"
    assert result["surface_from"] == "hint"


def test_classify_object_surface_custom_threshold():
    skill = ClassifyObjectSurfaceSkill()
    skill.set_node(_make_node({}))
    # z=0.30은 기본 임계(0.15)로는 elevated지만 임계를 0.4로 올리면 floor.
    result = skill.execute(
        {
            "target_object": "cup",
            "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.30},
            "floor_z_threshold_m": 0.4,
        }
    )
    assert result["success"] is True
    assert result["surface"] == "floor"


def test_fixed_pattern_grasp_floor_deterministic_sequence(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {
            name: (0.03 if name == "gripper_aperture" else 0.4) for name in names
        },
    )
    monkeypatch.setattr(
        sequence.fixed_pattern_grasp,
        "_log_achieved_gripper_aperture",
        lambda skill, logger, stage: None,
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.12}}
    )

    assert result["success"] is True
    assert result["surface"] == "floor"
    assert result["grasp_verified"] is True

    approach = _expect_joints((0.0, -0.6, 0.12 + 0.10))  # floor approach height 0.10
    grasp = _expect_joints((0.0, -0.6, 0.12))  # floor grasp offset 0.0
    expected = [
        ("joint", "gripper", {"gripper_aperture": 0.16}),  # open
        ("joint", "arm", approach),  # approach hover
        ("joint", "arm", grasp),  # reach grasp
        ("joint", "gripper", {"gripper_aperture": 0.0}),  # close
        ("joint", "arm", {"joint_lift": 0.4 + 0.18}),  # floor post-grasp lift
    ]
    assert backend.calls == expected


def test_fixed_pattern_grasp_elevated_deterministic_sequence(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {
            name: (0.03 if name == "gripper_aperture" else 0.4) for name in names
        },
    )
    monkeypatch.setattr(
        sequence.fixed_pattern_grasp,
        "_log_achieved_gripper_aperture",
        lambda skill, logger, stage: None,
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 0.0, "y": -0.6, "z": 0.45}}
    )

    assert result["success"] is True
    assert result["surface"] == "elevated"

    approach = _expect_joints((0.0, -0.6, 0.45 + 0.06))  # elevated approach height 0.06
    grasp = _expect_joints((0.0, -0.6, 0.45))  # elevated grasp offset 0.0
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("joint", "arm", approach),
        ("joint", "arm", grasp),
        ("joint", "gripper", {"gripper_aperture": 0.0}),
        ("joint", "arm", {"joint_lift": 0.4 + 0.12}),  # elevated post-grasp lift
    ]


def test_fixed_pattern_grasp_fails_when_object_unreachable(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            )
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {name: 0.03 for name in names},
    )

    skill = FixedPatternGraspSkill()
    skill.set_node(node)
    # 팔 도달 범위 밖(아주 먼 곳)의 좌표 → IK 실패로 우아하게 실패해야 한다.
    result = skill.execute(
        {"target_object": "cup", "object_base_xyz": {"x": 5.0, "y": 5.0, "z": 5.0}}
    )
    assert result["success"] is False
    assert "도달" in result["message"] or "IK" in result["message"]
    # IK 실패 시 그리퍼 open/팔 이동 어느 것도 실행하면 안 된다.
    assert backend.calls == []


def test_adaptive_pick_object_default_uses_fixed_pattern_grasp(monkeypatch):
    """기본 경로(옵션 B)는 고정 패턴 파지를 사용해야 한다."""
    calls = []

    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 0.0, "body_rotation_applied_deg": 0.0}
        ),
    )
    monkeypatch.setattr(
        sequence.PrepareRightSidePickSkill,
        "execute",
        lambda self, params: calls.append("prepare") or {"success": True},
    )
    # 기본 경로에서는 그리퍼 서보/VLA가 호출되면 안 된다.
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: (_ for _ in ()).throw(AssertionError("기본 경로에서 observe가 호출되면 안 됩니다")),
    )
    monkeypatch.setattr(
        sequence.VLABasedPickGripperObjectSkill,
        "execute",
        lambda self, params: (_ for _ in ()).throw(AssertionError("기본 경로에서 vla_pick이 호출되면 안 됩니다")),
    )
    monkeypatch.setattr(
        sequence.FixedPatternGraspSkill,
        "execute",
        lambda self, params: calls.append("fixed_pattern_grasp") or {"success": True},
    )

    skill = AdaptivePickObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "restore_head_after": False})

    assert result["success"] is True
    assert calls == ["search", "prepare", "fixed_pattern_grasp"]
