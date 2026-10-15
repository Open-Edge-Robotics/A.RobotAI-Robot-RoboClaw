"""헤드 pan/tilt 한계와 능동 탐색 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    EstimateGripperObjectPoseSkill,
    FakeBackend,
    FindObjectSkill,
    HeadPanTiltSkill,
    SearchObjectSkill,
    _make_node,
    _stub_head_observation,
    _stub_joint_positions,
    math,
    pytest,
    search_skill,
    sequence,
)


def test_estimate_gripper_object_pose_uses_gripper_low_score_defaults(monkeypatch):
    captured = []
    monkeypatch.setattr(
        FindObjectSkill,
        "execute",
        lambda self, params: (
            captured.append(params) or {"success": False, "message": "stop after capture"}
        ),
    )

    skill = EstimateGripperObjectPoseSkill()
    skill.set_node(_make_node())
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is False
    assert len(captured) == 1
    assert captured[0]["camera"] == "gripper"
    assert captured[0]["detections_topic"] == "/gripper_object_detector_node/detections"
    assert captured[0]["min_score"] == pytest.approx(0.1)
    assert captured[0]["timeout_sec"] == pytest.approx(6.0)


def _stretch_head_node(backend=None):
    backend = backend or FakeBackend()
    return _make_node({"manipulation_backend": "stretch"}, _manipulation_backend=backend), backend


def test_head_pan_tilt_rejects_non_stretch_backend():
    backend = FakeBackend()
    node = _make_node({}, manipulation_backend="moveit", _manipulation_backend=backend)  # manipulation_backend 미지정(기본 moveit)
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pan_deg": 10.0, "tilt_deg": -10.0})

    assert result["success"] is False
    assert "Stretch3" in result["message"]
    assert backend.calls == []


def test_head_pan_tilt_moves_with_explicit_deg_values():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pan_deg": 45.0, "tilt_deg": -30.0})

    assert result["success"] is True
    assert backend.calls == [
        (
            "joint",
            "head",
            {
                "joint_head_pan": pytest.approx(math.radians(45.0)),
                "joint_head_tilt": pytest.approx(math.radians(-30.0)),
            },
        )
    ]


def test_head_pan_tilt_moves_with_explicit_rad_values():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pan_rad": 0.5, "tilt_rad": -0.8})

    assert result["success"] is True
    assert backend.calls == [("joint", "head", {"joint_head_pan": 0.5, "joint_head_tilt": -0.8})]


def test_head_pan_tilt_uses_builtin_default_named_pose():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pose_name": "search_head_down"})

    assert result["success"] is True
    assert backend.calls == [("joint", "head", {"joint_head_pan": 0.1, "joint_head_tilt": -0.8})]


def test_head_pan_tilt_configured_named_pose_overrides_default():
    backend = FakeBackend()
    node = _make_node(
        {
            "manipulation_backend": "stretch",
            "manipulation_named_poses_json": (
                '{"search_head_down":{"joint_head_pan":0.5,"joint_head_tilt":-0.5}}'
            ),
        },
        _manipulation_backend=backend,
    )
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pose_name": "search_head_down"})

    assert result["success"] is True
    assert backend.calls == [("joint", "head", {"joint_head_pan": 0.5, "joint_head_tilt": -0.5})]


def test_head_pan_tilt_rejects_unknown_pose_name():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pose_name": "no_such_pose"})

    assert result["success"] is False
    assert backend.calls == []


def test_head_pan_tilt_requires_pose_name_or_angles():
    node, backend = _stretch_head_node()
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({})

    assert result["success"] is False
    assert backend.calls == []


def test_head_pan_tilt_enforces_robot_limits():
    backend = FakeBackend()
    robot_limits = {"manipulation": {"joint_limits": {"joint_head_pan": [-1.0, 1.0]}}}
    node = _make_node(
        {"manipulation_backend": "stretch"},
        _manipulation_backend=backend,
        _robot_limits_dict=robot_limits,
    )
    skill = HeadPanTiltSkill()
    skill.set_node(node)

    result = skill.execute({"pan_rad": 2.0})

    assert result["success"] is False
    assert backend.calls == []


def test_search_object_requires_target_object():
    skill = SearchObjectSkill()
    skill.set_node(_make_node())

    assert skill.validate_params({}) is False
    result = skill.execute({})
    assert result["success"] is False


def test_search_object_succeeds_on_first_head_pan_view(monkeypatch):
    node, backend = _stretch_head_node()
    head_calls = []

    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: head_calls.append(params) or {"success": True},
    )

    find_calls = []

    def fake_localize(node_, params, target_object):
        find_calls.append(target_object)
        return 0.5, {"success": True, "class_name": target_object}

    monkeypatch.setattr(search_skill, "_localize_rotation_with_base_camera", fake_localize)

    align_calls = []
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: align_calls.append(params["angle_deg"]) or {"success": True},
    )

    skill = SearchObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup", "head_settle_sec": 0})

    assert result["success"] is True
    assert result["align_angle_deg"] == pytest.approx(math.degrees(0.5))
    assert result["body_rotation_applied_deg"] == pytest.approx(0.0)
    # 헤드 팜 스윗 첫 번째 시도에서 바로 찾았으므로 몸통 회전은 필요하지 않다.
    assert align_calls == []
    assert len(find_calls) == 1
    assert len(head_calls) == 1


def test_search_object_sweeps_head_pan_before_rotating_body(monkeypatch):
    node, backend = _stretch_head_node()

    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: {"success": True},
    )

    find_results = [
        (None, {"success": False, "message": "not found"}),
        (None, {"success": False, "message": "not found"}),
        (None, {"success": False, "message": "not found"}),
        (0.2, {"success": True, "class_name": "cup"}),
    ]

    def fake_localize(node_, params, target_object):
        return find_results.pop(0)

    monkeypatch.setattr(search_skill, "_localize_rotation_with_base_camera", fake_localize)

    align_calls = []
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: align_calls.append(params["angle_deg"]) or {"success": True},
    )

    skill = SearchObjectSkill()
    skill.set_node(node)
    result = skill.execute(
        {
            "target_object": "cup",
            "pan_sweep_deg": [-68.8, 0.0, 68.8],
            "head_settle_sec": 0,
            # find_results 4개(pan 3회 실패 + 회전 후 1회 성공)에 맞춘다.
            "view_retries": 1,
        }
    )

    assert result["success"] is True
    # pan 3스텝을 다 시도한 뒤에야 몸통 회전(1회)이 일어난다.
    assert align_calls == [90.0]
    assert result["body_rotation_applied_deg"] == pytest.approx(90.0)


def test_search_object_gives_up_after_max_body_rotations(monkeypatch):
    node, backend = _stretch_head_node()

    monkeypatch.setattr(
        HeadPanTiltSkill,
        "execute",
        lambda self, params: {"success": True},
    )
    monkeypatch.setattr(
        search_skill,
        "_localize_rotation_with_base_camera",
        lambda node_, params, target_object: (None, {"success": False, "message": "not found"}),
    )
    align_calls = []
    monkeypatch.setattr(
        sequence.AlignRightArmToFrontSkill,
        "execute",
        lambda self, params: align_calls.append(params["angle_deg"]) or {"success": True},
    )

    skill = SearchObjectSkill()
    skill.set_node(node)
    result = skill.execute(
        {
            "target_object": "cup",
            "pan_sweep_deg": [0.0],
            "max_body_rotations": 2,
            "head_settle_sec": 0,
        }
    )

    assert result["success"] is False
    assert align_calls == [90.0, 90.0]
    assert result["body_rotation_applied_deg"] == pytest.approx(180.0)


def test_search_object_skips_head_when_not_stretch_backend(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, manipulation_backend="moveit", _manipulation_backend=backend)  # moveit 기본

    def fail_if_called(self, params):
        raise AssertionError("non-stretch 백엔드에서는 헤드 제어를 시도하면 안 됩니다")

    monkeypatch.setattr(HeadPanTiltSkill, "execute", fail_if_called)

    calls = []

    def fake_localize(node_, params, target_object):
        calls.append("find")
        return 0.3, {"success": True}

    monkeypatch.setattr(search_skill, "_localize_rotation_with_base_camera", fake_localize)

    skill = SearchObjectSkill()
    skill.set_node(node)
    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert calls == ["find"]
