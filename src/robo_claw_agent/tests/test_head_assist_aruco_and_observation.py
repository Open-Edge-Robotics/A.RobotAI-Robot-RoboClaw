"""head assist aruco and observation"""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _head_assist_helpers import (  # noqa: F401
    AdaptivePickObjectSkill,
    ServoGripperToObjectSkill,
    _aruco_skill,
    _head_obs,
    _make_node,
    _patch_adaptive_pick,
    _run_servo_with_residual,
    _stretch_node,
    _stub_joint_positions,
    adaptive_pick_skill,
    ee_state,
    head_assist,
    head_vision,
    pytest,
)


def test_without_aruco_residual_head_reports_ready(monkeypatch):
    """잔차가 없으면 오차 0 -> 파지 반경 이내이므로 관절 명령 없이 ready."""
    result, backend = _run_servo_with_residual(monkeypatch, None)

    assert result["ready_source"] == "head_camera"
    assert backend.calls == []
def test_aruco_residual_shifts_head_guidance(monkeypatch):
    """잔차가 헤드 3D 관측에 실제로 더해져 유도 명령을 바꾸는지 확인한다."""
    # 헤드가 물체를 실제보다 0.10m 낮게 본다고 보정 -> 파지 반경(0.05) 밖이 되어
    # lift 를 올려야 한다. 보정이 반영되지 않으면 위 테스트처럼 명령이 없어야 한다.
    result, backend = _run_servo_with_residual(monkeypatch, (0.0, 0.0, 0.10))

    lift_calls = [c for c in backend.calls if "joint_lift" in c[2]]
    assert lift_calls, "ArUco 잔차가 헤드 유도에 반영되지 않았습니다."
    # lift_delta = clamp(0.10, ±max_lift_step_m 0.06) = 0.06
    assert lift_calls[0][2]["joint_lift"] == pytest.approx(0.46)
    assert result["head_assist"]["guided_steps"] == 1
def test_head_camera_params_force_head_routing():
    """그리퍼용 params 가 섞여 들어와도 헤드 depth 토픽으로 라우팅되어야 한다."""
    routed = head_vision._head_camera_params(
        {
            "camera": "gripper",
            "camera_source": "wrist",
            "depth_topic": "/gripper_camera/aligned_depth_to_color/image_raw",
            "camera_info_topic": "/gripper_camera/aligned_depth_to_color/camera_info",
            "target_object": "cup",
        }
    )

    assert "camera" not in routed
    assert "camera_source" not in routed
    assert "depth_topic" not in routed
    assert "camera_info_topic" not in routed
    assert routed["target_object"] == "cup"
def test_head_camera_params_honor_explicit_head_overrides():
    routed = head_vision._head_camera_params(
        {"head_depth_topic": "/head/depth", "head_camera_info_topic": "/head/info"}
    )
    assert routed["depth_topic"] == "/head/depth"
    assert routed["camera_info_topic"] == "/head/info"
def test_object_base_xyz_extracts_tuple():
    assert head_vision._object_base_xyz(_head_obs(1.0, 2.0, 3.0)) == (1.0, 2.0, 3.0)
    assert head_vision._object_base_xyz({"success": False}) is None
    assert head_vision._object_base_xyz({"success": True}) is None
def test_grasp_center_prefers_tf():
    fresh = 9_500_000_000
    skill = _aruco_skill({"link_grasp_center": (0.01, -0.67, 0.71, fresh)})

    assert ee_state._grasp_center_base_xyz(skill) == pytest.approx((0.01, -0.67, 0.71))
def test_grasp_center_falls_back_to_fk(monkeypatch):
    """TF 가 없으면 joint_states + FK 로 계산한다."""
    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node({}))
    monkeypatch.setattr(
        ee_state,
        "_joint_positions",
        lambda s, names: {
            "joint_lift": 0.6,
            "wrist_extension": 0.26,
            "joint_wrist_yaw": 0.0,
        },
    )

    xyz = ee_state._grasp_center_base_xyz(skill)

    # FK 검증값: lift=0.6, ext=0.26 -> (-0.0213, -0.6749, 0.7097)
    assert xyz == pytest.approx((-0.0213, -0.6749, 0.7097), abs=1e-3)
def test_grasp_center_returns_none_without_tf_or_joints(monkeypatch):
    skill = ServoGripperToObjectSkill()
    skill.set_node(_make_node({}))
    monkeypatch.setattr(ee_state, "_joint_positions", lambda s, names: None)

    assert ee_state._grasp_center_base_xyz(skill) is None
def test_adaptive_pick_places_head_between_prepare_and_gripper_observe(monkeypatch):
    """헤드는 준비자세 직후, 그리퍼 확인 직전에 파지 감시 자세로 이동해야 한다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False})

    assert result["success"] is True
    stages = [name for name, _ in calls]
    assert stages.index("prepare") < stages.index("head") < stages.index("observe_gripper")
    assert ("head", "gripper_side_head") in calls
    # 주행 안전을 위한 travel_head 복귀는 그대로 유지되어야 한다.
    assert calls[-1] == ("head", "travel_head")
def test_adaptive_pick_marks_head_ready_for_servo(monkeypatch):
    """서보가 헤드를 다시 움직이지 않도록 head_assist_ready 를 내려보낸다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False})

    pick_calls = [value for name, value in calls if name == "pick"]
    assert pick_calls == [True]
def test_adaptive_pick_continues_when_head_move_fails(monkeypatch):
    """헤드 이동 실패는 치명적이지 않다 — 파지 시퀀스가 계속 진행되어야 한다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls, head_pose_succeeds=False)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False})

    assert result["success"] is True
    assert "observe_gripper" in [name for name, _ in calls]
def test_adaptive_pick_skips_head_assist_when_disabled(monkeypatch):
    """use_head_assist=False 면 gripper_side_head 이동/헤드 관측이 없어야 한다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    result = skill.execute(
        {"target_object": "cup", "use_head_assist": False, "use_fixed_pattern_grasp": False}
    )

    assert result["success"] is True
    assert ("head", "gripper_side_head") not in calls
    assert "observe_head" not in [name for name, _ in calls]
    # travel_head 복귀는 여전히 수행된다.
    assert calls[-1] == ("head", "travel_head")
def test_adaptive_pick_lateral_aligns_while_arm_is_retracted(monkeypatch):
    """헤드가 물체를 보면 팔이 접힌 지금(ready pose) 좌우 정렬을 시도해야 한다."""
    calls = []
    _patch_adaptive_pick(monkeypatch, calls)
    monkeypatch.setattr(
        head_assist.HeadAssist,
        "observe",
        lambda self: (calls.append(("observe_head", None)) or ((0.05, -0.60, 0.70), {"success": True})),
    )
    aligned = {}

    def _fake_align(skill, runtime, params, obj_xyz, allow_retract=False):
        aligned["obj"] = obj_xyz
        aligned["allow_retract"] = allow_retract
        return {"rotated": True, "angle_deg": 4.0}

    monkeypatch.setattr(adaptive_pick_skill, "lateral_align", _fake_align)

    skill = AdaptivePickObjectSkill()
    skill.set_node(_stretch_node())
    result = skill.execute({"target_object": "cup", "use_fixed_pattern_grasp": False})

    assert result["success"] is True
    assert aligned["obj"] == (0.05, -0.60, 0.70)
    # 팔이 접혀 있으므로 retract 없이 바로 회전 가능해야 한다.
    assert aligned["allow_retract"] is False
    stages = [name for name, _ in calls]
    assert stages.index("observe_head") < stages.index("observe_gripper")
