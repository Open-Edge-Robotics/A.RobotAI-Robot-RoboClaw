"""정면/우측 구역 집기와 작업축 정렬 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    FakeBackend,
    PickFromRightSideZoneSkill,
    PickFrontObjectSkill,
    _grasp_positions,
    _make_node,
    _stub_head_observation,
    _stub_joint_positions,
    grasp_verification,
    pytest,
    sequence,
    stretch_mode,
)


def test_pick_from_right_side_zone_runs_calibrated_sequence(monkeypatch):
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_gripper_presets_json": (
                '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
            ),
            "manipulation_named_poses_json": (
                '{"right_side_pick_ready":{"joint_lift":0.4},'
                '"right_side_pick_reach":{"wrist_extension":0.3},'
                '"right_side_pick_lift":{"joint_lift":0.5},'
                '"carry":{"wrist_extension":0.05}}'
            ),
        },
        _manipulation_backend=backend,
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", _grasp_positions(0.03)
    )
    skill = PickFromRightSideZoneSkill()
    skill.set_node(node)

    result = skill.execute({})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert backend.calls == [
        ("joint", "gripper", {"gripper_aperture": 0.16}),
        ("named", "arm", "right_side_pick_ready"),
        ("named", "arm", "right_side_pick_reach"),
        ("joint", "gripper", {"gripper_aperture": 0.0}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.68)}),
        ("named", "arm", "carry"),
    ]


def test_align_right_arm_to_front_uses_stretch_rotate_mobile_base():
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    skill = sequence.AlignRightArmToFrontSkill()
    skill.set_node(node)

    result = skill.execute({"angle_deg": 90.0})

    assert result["success"] is True
    assert result["joint"] == "rotate_mobile_base"
    assert result["angle_source"] == "fixed"
    assert backend.calls == [
        ("joint", "base", {"rotate_mobile_base": pytest.approx(1.57079632679)})
    ]


def test_align_right_arm_to_front_falls_back_to_fixed_angle_without_target():
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    skill = sequence.AlignRightArmToFrontSkill()
    skill.set_node(node)

    result = skill.execute({})

    assert result["success"] is True
    assert result["angle_source"] == "fixed"
    assert result["angle_deg"] == pytest.approx(90.0)


def test_align_right_arm_to_front_computes_angle_from_target_pose():
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    skill = sequence.AlignRightArmToFrontSkill()
    skill.set_node(node)

    # 정면(로봇 x축 위) 물체 -- 회전 없이는 도달 불가하므로 계산된 각도로 회전해야 한다.
    result = skill.execute({"x": 0.3, "y": 0.0, "z": 0.5})

    assert result["success"] is True
    assert result["angle_source"] == "computed"
    assert result["angle_deg"] != pytest.approx(90.0)
    assert len(backend.calls) == 1
    kind, group, joints = backend.calls[0]
    assert kind == "joint"
    assert group == "base"
    assert set(joints.keys()) == {"rotate_mobile_base"}


def test_align_right_arm_to_front_rejects_rotation_exceeding_max_rotation_deg():
    backend = FakeBackend()
    robot_limits = {"navigation": {"max_rotation_deg": 10.0}}
    node = _make_node({}, _manipulation_backend=backend, _robot_limits_dict=robot_limits)
    skill = sequence.AlignRightArmToFrontSkill()
    skill.set_node(node)

    result = skill.execute({"angle_deg": 90.0})

    assert result["success"] is False
    assert backend.calls == []


def test_pick_front_object_composes_stow_align_and_right_side_pick(monkeypatch):
    calls = []

    def fake_stow_execute(self, params):
        calls.append(("stow", params))
        return {"success": True}

    def fake_align_execute(self, params):
        calls.append(("align", params["angle_deg"]))
        return {"success": True}

    def fake_pick_execute(self, params):
        calls.append(("pick", params.get("ready_pose", "default")))
        return {"success": True}

    monkeypatch.setattr(stretch_mode.StowForNavigationSkill, "execute", fake_stow_execute)
    monkeypatch.setattr(sequence.AlignRightArmToFrontSkill, "execute", fake_align_execute)
    monkeypatch.setattr(sequence.PickFromRightSideZoneSkill, "execute", fake_pick_execute)

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"align_angle_deg": 90.0, "ready_pose": "custom_ready"})

    assert result["success"] is True
    # stow 는 deadline 여유를 timeout_sec 로 함께 넘긴다(나머지 인자는 없음).
    assert calls[0][0] == "stow"
    assert set(calls[0][1]) <= {"timeout_sec"}
    assert calls[1:] == [("align", 90.0), ("pick", "custom_ready")]


def test_pick_front_object_can_restore_heading(monkeypatch):
    calls = []

    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(params["angle_deg"]) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.PickFromRightSideZoneSkill,
        "execute",
        lambda self, params: {"success": True},
    )

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"align_angle_deg": 90.0, "restore_heading": True})

    assert result["success"] is True
    assert calls == [90.0, -90.0]


def test_pick_front_object_uses_active_search_when_requested(monkeypatch):
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
            or {"success": True, "align_angle_deg": 32.0, "body_rotation_applied_deg": 90.0}
        ),
    )
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: calls.append(("align", params["angle_deg"])) or {"success": True},
    )
    monkeypatch.setattr(
        sequence.PickFromRightSideZoneSkill,
        "execute",
        lambda self, params: calls.append("pick") or {"success": True},
    )

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute(
        {"target_object": "cup", "use_active_search": True, "restore_heading": True}
    )

    assert result["success"] is True
    assert result["used_active_search"] is True
    # search가 찾은 각도(32.0)가 고정값(90.0) 대신 사용되어야 하고,
    # 복귀 시에는 search의 몸통 회전(90.0)까지 합친 누적치를 되돌려야 한다.
    assert calls == ["search", ("align", 32.0), "pick", ("align", -122.0)]


def test_pick_front_object_fails_when_active_search_fails(monkeypatch):
    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        sequence.SearchObjectSkill,
        "execute",
        lambda self, params: {"success": False, "message": "못 찾음"},
    )

    def fail_if_called(self, params):
        raise AssertionError("탐색 실패 시 정렬/집기를 시도하면 안 됩니다")

    monkeypatch.setattr(sequence.AlignRightArmToFrontSkill, "execute", fail_if_called)
    monkeypatch.setattr(sequence.PickFromRightSideZoneSkill, "execute", fail_if_called)

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup", "use_active_search": True})

    assert result["success"] is False
    assert "탐색 실패" in result["message"]


def test_pick_front_object_active_search_requires_target_object(monkeypatch):
    monkeypatch.setattr(
        stretch_mode.StowForNavigationSkill,
        "execute",
        lambda self, params: {"success": True},
    )

    def fail_if_called(self, params):
        raise AssertionError("target_object 없이 SearchObjectSkill이 호출되면 안 됩니다")

    monkeypatch.setattr(sequence.SearchObjectSkill, "execute", fail_if_called)

    skill = PickFrontObjectSkill()
    skill.set_node(_make_node())
    result = skill.execute({"use_active_search": True})

    assert result["success"] is False
