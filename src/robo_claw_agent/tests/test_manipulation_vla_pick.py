"""그리퍼 카메라 폐루프(VLA) 집기 스킬 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    FakeBackend,
    PrepareRightSidePickSkill,
    VLABasedPickFrontObjectSkill,
    VLABasedPickGripperObjectSkill,
    _grasp_positions,
    _make_node,
    _stub_head_observation,
    _stub_joint_positions,
    grasp_verification,
    pytest,
    sequence,
    stretch_mode,
)


def test_vla_pick_front_object_runs_safe_sequence(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_ready":{"joint_lift":0.4},'
                '"right_side_pick_lift":{"joint_lift":0.5},'
                '"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", _grasp_positions(0.03)
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("named", "arm", "right_side_pick_ready"),
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("joint", "gripper", {"gripper_aperture": 0.0}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.68)}),
    ]


def test_vla_pick_front_object_does_not_close_when_servo_not_ready(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
            "manipulation_named_poses_json": '{"right_side_pick_ready":{"joint_lift":0.4}}',
        },
        _manipulation_backend=backend,
    )

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": False},
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is False
    assert "gripper close" in result["message"]
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("named", "arm", "right_side_pick_ready"),
        # 서보가 ready 가 아니어서 닫지 못하고, 복구 stow 로 접는다.
        ("named", "arm", "stow"),
    ]


def test_vla_pick_front_object_uses_active_search_when_requested(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_ready":{"joint_lift":0.4},'
                '"right_side_pick_lift":{"joint_lift":0.5},'
                '"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )

    calls = []

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: (
            calls.append("search")
            or {"success": True, "align_angle_deg": 60.0, "body_rotation_applied_deg": 0.0}
        ),
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(("align", params["angle_deg"])) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", _grasp_positions(0.03)
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "use_active_search": True})

    assert result["success"] is True
    assert result["used_active_search"] is True
    assert result["base_rotation_applied_deg"] == pytest.approx(60.0)
    # 고정 90도 대신 search가 찾은 60도가 사용되어야 한다.
    assert calls == ["search", ("align", 60.0)]


def test_vla_pick_front_object_defaults_to_fixed_angle_without_active_search(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
            "manipulation_named_poses_json": '{"right_side_pick_ready":{"joint_lift":0.4}}',
        },
        _manipulation_backend=backend,
    )

    def fail_if_called(self, params):
        raise AssertionError(
            "use_active_search 기본값(False)에서는 SearchObjectSkill이 호출되면 안 됩니다"
        )

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(sequence.SearchObjectSkill, "execute", fail_if_called)
    align_calls = []
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: align_calls.append(params["angle_deg"]) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": False},
    )

    skill = VLABasedPickFrontObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    # servo가 ready_to_grasp=False를 반환하는 조기 실패 경로라 고정 각도(90.0)로
    # align이 한 번만 호출되었는지만 확인한다(search는 호출되지 않아야 함).
    assert result["success"] is False
    assert align_calls == [90.0]


def test_prepare_right_side_pick_opens_and_moves_ready():
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
            "manipulation_named_poses_json": '{"right_side_pick_ready":{"joint_lift":0.4}}',
        },
        _manipulation_backend=backend,
    )
    skill = PrepareRightSidePickSkill()
    skill.set_node(node)

    result = skill.execute({})

    assert result["success"] is True
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("named", "arm", "right_side_pick_ready"),
    ]


def test_vla_pick_gripper_object_does_not_align_or_move_ready_by_default(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": '{"close":{"gripper_aperture":0.0}}',
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", _grasp_positions(0.03)
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.0}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.68)}),
    ]


def test_vla_pick_gripper_object_uses_loose_close_for_cup(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"close":{"gripper_aperture":0.0},"close_loose":{"gripper_aperture":0.025}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", _grasp_positions(0.04)
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert result["close_preset"] == "close_loose"
    assert backend.calls[0] == ("joint", "gripper", {"gripper_aperture": 0.025})


def test_vla_pick_gripper_object_explicit_close_preset_wins(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"close":{"gripper_aperture":0.0},"close_loose":{"gripper_aperture":0.025}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_lift":{"joint_lift":0.5},"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", _grasp_positions(0.03)
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "close_preset": "close"})

    assert result["success"] is True
    assert result["close_preset"] == "close"
    assert backend.calls[0] == ("joint", "gripper", {"gripper_aperture": 0.0})


def test_vla_pick_gripper_object_does_not_close_when_not_ready(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": False},
    )

    skill = VLABasedPickGripperObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is False
    # 서보가 ready 가 아니면 gripper 를 닫지 않는다(사전 stow 이동은 허용).
    assert [call for call in backend.calls if call[0] == "gripper"] == []
