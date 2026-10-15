"""그리퍼 관측/시각 서보/joint 합성 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    FakeBackend,
    ObserveGripperTargetSkill,
    ServoGripperToObjectSkill,
    SimpleNamespace,
    _FakeJointState,
    _make_node,
    _stub_head_observation,
    _stub_joint_positions,
    gripper_target_skills,
    gripper_vision,
    np,
    perception_core,
    pytest,
    sequence,
)


def test_observe_gripper_target_skill_uses_observation_helper(monkeypatch):
    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: {
            "success": True,
            "target_object": params["target_object"],
            "ready_to_grasp": True,
        },
    )
    skill = ObserveGripperTargetSkill()
    skill.set_node(_make_node())

    result = skill.execute({"target_object": "cup"})

    assert result["success"] is True
    assert result["target_object"] == "cup"
    assert result["ready_to_grasp"] is True


def test_fallback_cup_center_from_gripper_image_detects_green_cup():
    skill = ServoGripperToObjectSkill()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (120, 120, 120)
    image[240:320, 260:340] = (120, 150, 110)
    skill.get_opencv_image = lambda params, timeout_sec=1.0: (image, "/camera", False)

    fallback = gripper_vision._fallback_cup_center_from_gripper_image(
        skill, {"target_object": "cup"}
    )

    assert fallback is not None
    cx, cy, size_x, size_y, message = fallback
    assert cx == pytest.approx(299.5, abs=2.0)
    assert cy == pytest.approx(279.5, abs=2.0)
    assert size_x >= 70
    assert size_y >= 70
    assert "fallback" in message


def test_fallback_cup_center_default_hsv_does_not_match_blue_cup():
    """기본 HSV 범위는 (다른 현장의) 파란 컵까지 잡아내지 않아야 한다 —
    하드코딩된 녹색 전용 폴백이라는 한계를 보여주는 회귀 테스트."""
    skill = ServoGripperToObjectSkill()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (120, 120, 120)
    image[240:320, 260:340] = (200, 60, 40)  # BGR 강한 파랑
    skill.get_opencv_image = lambda params, timeout_sec=1.0: (image, "/camera", False)

    fallback = gripper_vision._fallback_cup_center_from_gripper_image(
        skill, {"target_object": "cup"}
    )

    assert fallback is None


def test_fallback_cup_center_hsv_params_generalize_to_other_colors():
    """cup_fallback_hsv_lower/upper 파라미터로 녹색이 아닌 컵도 잡을 수 있어야 한다."""
    skill = ServoGripperToObjectSkill()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (120, 120, 120)
    image[240:320, 260:340] = (200, 60, 40)  # BGR 강한 파랑
    skill.get_opencv_image = lambda params, timeout_sec=1.0: (image, "/camera", False)

    fallback = gripper_vision._fallback_cup_center_from_gripper_image(
        skill,
        {
            "target_object": "cup",
            "cup_fallback_hsv_lower": [95, 60, 40],
            "cup_fallback_hsv_upper": [130, 255, 255],
        },
    )

    assert fallback is not None
    cx, cy, size_x, size_y, message = fallback
    assert cx == pytest.approx(299.5, abs=2.0)
    assert cy == pytest.approx(279.5, abs=2.0)
    assert "fallback" in message


def test_fallback_cup_center_invalid_param_length_falls_back_to_default():
    """길이가 맞지 않는 잘못된 파라미터는 조용히 기본값으로 폴백해야 한다."""
    skill = ServoGripperToObjectSkill()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (120, 120, 120)
    image[240:320, 260:340] = (120, 150, 110)
    skill.get_opencv_image = lambda params, timeout_sec=1.0: (image, "/camera", False)

    fallback = gripper_vision._fallback_cup_center_from_gripper_image(
        skill,
        {"target_object": "cup", "cup_fallback_hsv_lower": [1, 2]},  # 길이 2 (잘못됨, 3 필요)
    )

    assert fallback is not None
    cx, cy, size_x, size_y, message = fallback
    assert cx == pytest.approx(299.5, abs=2.0)
    assert cy == pytest.approx(279.5, abs=2.0)


def test_observe_gripper_target_uses_stricter_y_tolerance_for_cup(monkeypatch):
    det = SimpleNamespace(
        bbox=SimpleNamespace(
            center=SimpleNamespace(position=SimpleNamespace(x=320.0, y=337.9)),
            size_x=80.0,
            size_y=90.0,
        )
    )
    monkeypatch.setattr(
        gripper_vision,
        "_best_gripper_detection",
        lambda *args, **kwargs: (det, "cup", 0.9, ""),
    )
    depth_frame = SimpleNamespace(depth_img=np.zeros((480, 640), dtype=np.uint16))
    monkeypatch.setattr(perception_core, "_fetch_depth_frame", lambda skill, params: depth_frame)
    monkeypatch.setattr(
        perception_core,
        "_depth_point_for_pixel",
        lambda skill, depth_frame, params, cx, cy: ((0.0, 0.0, 0.0), 0.42),
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node())
    result = gripper_vision._observe_gripper_target(
        skill,
        {
            "target_object": "cup",
            "target_center_y_ratio": 0.58,
            "center_tolerance_px": 60.0,
            "center_tolerance_y_px": 35.0,
            "target_depth_m": 0.42,
            "depth_tolerance_m": 0.07,
        },
    )

    assert result["success"] is True
    assert result["image_error_px"]["y"] == pytest.approx(59.5)
    assert result["centered"] is False
    assert result["ready_to_grasp"] is False


def test_observe_gripper_target_allows_wider_x_tolerance_for_open_cup_grasp(monkeypatch):
    det = SimpleNamespace(
        bbox=SimpleNamespace(
            center=SimpleNamespace(position=SimpleNamespace(x=406.4, y=302.1)),
            size_x=80.0,
            size_y=90.0,
        )
    )
    monkeypatch.setattr(
        gripper_vision,
        "_best_gripper_detection",
        lambda *args, **kwargs: (det, "cup", 0.9, ""),
    )
    depth_frame = SimpleNamespace(depth_img=np.zeros((480, 640), dtype=np.uint16))
    monkeypatch.setattr(perception_core, "_fetch_depth_frame", lambda skill, params: depth_frame)
    monkeypatch.setattr(
        perception_core,
        "_depth_point_for_pixel",
        lambda skill, depth_frame, params, cx, cy: ((0.0, 0.0, 0.0), 0.174),
    )

    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node())
    result = gripper_vision._observe_gripper_target(
        skill,
        {
            "target_object": "cup",
            "target_center_y_ratio": 0.58,
            "center_tolerance_y_px": 30.0,
            "target_depth_m": 0.22,
            "depth_tolerance_m": 0.05,
        },
    )

    assert result["image_error_px"]["x"] == pytest.approx(86.4)
    assert result["image_error_px"]["y"] == pytest.approx(23.7)
    assert result["center_tolerance_px"]["x"] == pytest.approx(100.0)
    assert result["ready_to_grasp"] is True


def test_servo_gripper_to_object_moves_extension_and_lift(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    observations = [
        {
            "success": True,
            "ready_to_grasp": False,
            "depth_m": 0.35,
            "image_error_px": {"x": 0.0, "y": -100.0},
        },
        {
            "success": True,
            "ready_to_grasp": True,
            "depth_m": 0.25,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
    ]

    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: observations.pop(0),
    )
    monkeypatch.setattr(
        gripper_target_skills,
        "_joint_positions",
        lambda skill, names: {"wrist_extension": 0.1, "joint_lift": 0.4},
    )
    skill = ServoGripperToObjectSkill()
    skill.set_node(node)

    result = skill.execute({"target_object": "cup", "max_steps": 2, "use_head_assist": False})

    assert result["success"] is True
    assert len(backend.calls) == 1
    assert backend.calls[0][0:2] == ("joint", "arm")
    assert backend.calls[0][2] == {
        "wrist_extension": pytest.approx(0.15),
        "joint_lift": pytest.approx(0.45),
    }


def test_servo_gripper_to_object_scans_lift_after_losing_seen_target(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    observations = [
        {
            "success": True,
            "ready_to_grasp": False,
            "depth_m": 0.55,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
        {"success": False, "message": "lost target"},
        {
            "success": True,
            "ready_to_grasp": True,
            "depth_m": 0.25,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
    ]

    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: observations.pop(0),
    )
    monkeypatch.setattr(
        gripper_target_skills,
        "_joint_positions",
        lambda skill, names: {"wrist_extension": 0.1, "joint_lift": 0.4},
    )
    skill = ServoGripperToObjectSkill()
    skill.set_node(node)

    result = skill.execute({"target_object": "cup", "max_steps": 4, "use_head_assist": False})

    assert result["success"] is True
    assert backend.calls == [
        ("joint", "arm", {"wrist_extension": pytest.approx(0.15)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.37)}),
    ]


def test_servo_gripper_to_object_alternates_lift_recovery_direction(monkeypatch):
    backend = FakeBackend()
    node = _make_node({}, _manipulation_backend=backend)
    observations = [
        {
            "success": True,
            "ready_to_grasp": False,
            "depth_m": 0.55,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
        {"success": False, "message": "lost target"},
        {"success": False, "message": "still lost"},
        {
            "success": True,
            "ready_to_grasp": True,
            "depth_m": 0.25,
            "image_error_px": {"x": 0.0, "y": 0.0},
        },
    ]

    monkeypatch.setattr(
        gripper_target_skills,
        "_observe_gripper_target",
        lambda skill, params: observations.pop(0),
    )
    monkeypatch.setattr(
        gripper_target_skills,
        "_joint_positions",
        lambda skill, names: {"wrist_extension": 0.1, "joint_lift": 0.4},
    )
    skill = ServoGripperToObjectSkill()
    skill.set_node(node)

    result = skill.execute({"target_object": "cup", "max_steps": 5, "use_head_assist": False})

    assert result["success"] is True
    assert backend.calls == [
        ("joint", "arm", {"wrist_extension": pytest.approx(0.15)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.37)}),
        ("joint", "arm", {"joint_lift": pytest.approx(0.43)}),
    ]


def test_joint_positions_synthesizes_wrist_extension_from_arm_segments():
    skill = ServoGripperToObjectSkill()
    msg = _FakeJointState(
        ["joint_lift", "joint_arm_l0", "joint_arm_l1", "joint_arm_l2", "joint_arm_l3"],
        [0.4, 0.01, 0.02, 0.03, 0.04],
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=1.0, **kwargs: msg

    positions = sequence._joint_positions(skill, ["joint_lift", "wrist_extension"])

    assert positions == {"joint_lift": 0.4, "wrist_extension": 0.1}


def test_joint_positions_synthesizes_gripper_aperture_from_finger_joints():
    skill = ServoGripperToObjectSkill()
    msg = _FakeJointState(
        ["joint_gripper_finger_left", "joint_gripper_finger_right"],
        [0.25, 0.25],
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=1.0, **kwargs: msg

    positions = sequence._joint_positions(skill, ["gripper_aperture"])

    assert positions == {"gripper_aperture": pytest.approx(0.0855)}
