"""grasp/place 및 TF·도달성·카메라 확인 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    EstimateGripperObjectPoseSkill,
    FakeBackend,
    GraspSkill,
    ManipulationConfigError,
    ManipulationRuntimeUnavailableError,
    MoveItManipulationRuntime,
    PlaceSkill,
    SimpleNamespace,
    _grasp_positions,
    _make_node,
    _stub_head_observation,
    _stub_joint_positions,
    grasp_verification,
    load_manipulation_config,
    math,
    pytest,
    rotation,
    sb,
    sequence,
)


class FakeTFBuffer:
    """map -> base_link 변환을 흉내내는 테스트용 TF 버퍼.

    base_link 원점이 map 프레임 (1.0, 2.0, 0.0)에 있다고 가정하고 평행이동만 수행한다.
    실제 로봇이 물체 감지 이후 이동했을 때, 저장된 map 프레임 pose가 grasp 실행 시점의
    최신 TF로 다시 변환되는지(스냅샷이 아니라 '지금' 기준인지)를 검증하기 위한 목적.
    """

    def transform(self, pose_stamped, target_frame, timeout=None):
        del timeout
        assert pose_stamped.header.frame_id == "map"
        assert target_frame == "base_link"
        from geometry_msgs.msg import PoseStamped

        result = PoseStamped()
        result.header.frame_id = target_frame
        result.pose.position.x = pose_stamped.pose.position.x - 1.0
        result.pose.position.y = pose_stamped.pose.position.y - 2.0
        result.pose.position.z = pose_stamped.pose.position.z
        result.pose.orientation = pose_stamped.pose.orientation
        return result


def test_grasp_skill_resolves_pose_from_memory_metadata(monkeypatch):
    backend = FakeBackend()
    params = {
        "manipulation_end_effector_link": "tool0",
        "manipulation_gripper_presets_json": (
            '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
        ),
    }
    memory = SimpleNamespace(
        get_object_location=lambda name: (
            {
                "metadata": {
                    "target_pose": {
                        "position": {"x": 0.7, "y": 0.0, "z": 0.3},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "frame_id": "map",
                    }
                }
            }
            if name == "cup"
            else None
        )
    )
    node = _make_node(
        params,
        _manipulation_backend=backend,
        _memory=memory,
        _tf_buffer=FakeTFBuffer(),
    )
    # 이 테스트는 TF 재변환에 집중한다. 도달성/회전 복구 경로는 별도 테스트에서 다룬다.
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)

    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"object_name": "cup", "require_gripper_confirmation": False})

    assert result["success"] is True
    # 저장된 pose는 map 프레임이지만, grasp 실행 시점의 최신 TF로 base_link 프레임으로
    # 재변환되어야 한다 (감지 시점 스냅샷이 아니라 소비 시점 기준).
    assert result["target_pose"]["frame_id"] == "base_link"
    assert result["target_pose"]["position"]["x"] == pytest.approx(0.7 - 1.0)
    assert result["target_pose"]["position"]["y"] == pytest.approx(0.0 - 2.0)


def test_grasp_skill_fails_when_pose_frame_needs_transform_but_no_tf_buffer():
    """TF 버퍼가 없으면 프레임이 다른 저장된 pose를 조용히 잘못 사용하지 않고 명시적으로 실패해야 한다."""
    backend = FakeBackend()
    memory = SimpleNamespace(
        get_object_location=lambda name: (
            {
                "metadata": {
                    "target_pose": {
                        "position": {"x": 0.7, "y": 0.0, "z": 0.3},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "frame_id": "map",
                    }
                }
            }
            if name == "cup"
            else None
        )
    )
    node = _make_node(
        {"manipulation_end_effector_link": "tool0"},
        _manipulation_backend=backend,
        _memory=memory,
    )
    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"object_name": "cup"})

    assert result["success"] is False
    assert "TF" in result["message"]


def _stretch_object_node(monkeypatch, backend, position, *, extra_params=None):
    """object_name='cup'가 base_link 기준 position 에 있는 stretch 백엔드 노드 생성."""
    params = {
        "manipulation_backend": "stretch",
        "manipulation_end_effector_link": "link_grasp_center",
        "manipulation_gripper_presets_json": (
            '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}'
        ),
    }
    params.update(extra_params or {})
    memory = SimpleNamespace(
        get_object_location=lambda name: (
            {
                "metadata": {
                    "target_pose": {
                        "position": dict(zip("xyz", position, strict=True)),
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "frame_id": "base_link",
                    }
                }
            }
            if name == "cup"
            else None
        )
    )
    return _make_node(params, _manipulation_backend=backend, _memory=memory)


def test_grasp_rotates_when_unreachable_then_retries(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, 0.0, 0.5))

    reachable_calls = []

    def fake_check_reachable(xyz):
        reachable_calls.append(tuple(xyz))
        return len(reachable_calls) > 1

    monkeypatch.setattr(sb, "check_reachable", fake_check_reachable)
    monkeypatch.setattr(rotation, "compute_align_rotation_rad", lambda xyz: math.radians(45.0))
    monkeypatch.setattr(
        rotation, "_joint_positions", lambda skill, names: {"wrist_extension": 0.05}
    )

    skill = GraspSkill()
    skill.set_node(node)

    result = skill.execute({"object_name": "cup", "require_gripper_confirmation": False})

    assert result["success"] is True
    assert result["reachability"]["rotated"] is True
    assert result["base_rotation_applied_deg"] == pytest.approx(45.0)
    rotate_calls = [c for c in backend.calls if c[0] == "joint" and c[1] == "base"]
    assert len(rotate_calls) == 1
    assert rotate_calls[0][2] == {"rotate_mobile_base": pytest.approx(math.radians(45.0))}
    # check_reachable가 회전 전/후 두 번 호출되어야 한다 (재시도 확인).
    assert len(reachable_calls) == 2


def test_grasp_skips_rotation_for_moveit_backend(monkeypatch):
    backend = FakeBackend()
    params = {
        "manipulation_end_effector_link": "tool0",
        "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16},"close":{"gripper_aperture":0.0}}',
    }
    memory = SimpleNamespace(
        get_object_location=lambda name: (
            {
                "metadata": {
                    "target_pose": {
                        "position": {"x": 0.3, "y": 0.0, "z": 0.5},
                        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
                        "frame_id": "base_link",
                    }
                }
            }
            if name == "cup"
            else None
        )
    )
    node = _make_node(
        params, manipulation_backend="moveit", _manipulation_backend=backend, _memory=memory
    )

    def fail_if_called(xyz):
        raise AssertionError("MoveIt 백엔드에서는 check_reachable이 호출되면 안 됩니다")

    monkeypatch.setattr(sb, "check_reachable", fail_if_called)

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})

    assert result["success"] is True
    assert result["reachability"]["rotated"] is False
    rotate_calls = [c for c in backend.calls if c[0] == "joint" and c[1] == "base"]
    assert rotate_calls == []


def test_grasp_defaults_to_gripper_camera_confirmation_for_object_name(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(grasp_verification, "_joint_positions", _grasp_positions(0.03))

    estimate_calls = []
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill,
        "execute",
        lambda self, params: estimate_calls.append(params) or {"success": True},
    )
    servo_calls = []
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: (
            servo_calls.append(params) or {"success": True, "ready_to_grasp": True}
        ),
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})  # camera 파라미터 미지정

    assert result["success"] is True
    assert result["gripper_camera_confirmed"] is True
    assert len(estimate_calls) == 1
    assert estimate_calls[0]["target_object"] == "cup"
    assert len(servo_calls) == 1
    assert servo_calls[0]["target_object"] == "cup"


def test_grasp_blocks_close_when_not_ready_to_grasp(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill, "execute", lambda self, params: {"success": True}
    )
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": False, "message": "안 보임"},
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})

    assert result["success"] is False
    assert result["gripper_camera_confirmed"] is False
    close_calls = [
        c
        for c in backend.calls
        if c[0] == "joint" and c[1] == "gripper" and c[2].get("gripper_aperture") == 0.0
    ]
    assert close_calls == []


def test_grasp_allows_opt_out_with_require_gripper_confirmation_false(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(grasp_verification, "_joint_positions", _grasp_positions(0.03))

    def fail_if_called(self, params):
        raise AssertionError("그리퍼 카메라 확인이 호출되면 안 됩니다")

    monkeypatch.setattr(EstimateGripperObjectPoseSkill, "execute", fail_if_called)
    monkeypatch.setattr(sequence.ServoGripperToObjectSkill, "execute", fail_if_called)

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup", "require_gripper_confirmation": False})

    assert result["success"] is True
    assert result["gripper_camera_confirmed"] is False
    assert result["confirmation_skipped_reason"] == "require_gripper_confirmation=False"


def test_grasp_verified_true_proceeds_normally(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill, "execute", lambda self, params: {"success": True}
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", _grasp_positions(0.03)
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert result["grasp_retry_count"] == 0


def test_grasp_verified_false_retries_then_succeeds(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill, "execute", lambda self, params: {"success": True}
    )
    # 1차 close: 명령값까지 도달(허공) -> 재시도 -> 2차 close: 물체에 걸려 성공.
    achieved_sequence = [0.0, 0.03]
    monkeypatch.setattr(
        grasp_verification,
        "_joint_positions",
        lambda skill, names: {"gripper_aperture": achieved_sequence.pop(0)},
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup"})

    assert result["success"] is True
    assert result["grasp_verified"] is True
    assert result["grasp_retry_count"] == 1
    close_calls = [
        c
        for c in backend.calls
        if c[0] == "joint" and c[1] == "gripper" and c[2].get("gripper_aperture") == 0.0
    ]
    assert len(close_calls) == 2  # 최초 시도 + 재시도 1회


def test_grasp_verified_false_after_retries_exhausted_fails(monkeypatch):
    backend = FakeBackend()
    node = _stretch_object_node(monkeypatch, backend, (0.3, -0.3, 0.5))
    monkeypatch.setattr(sb, "check_reachable", lambda xyz: True)
    monkeypatch.setattr(
        sequence.ServoGripperToObjectSkill,
        "execute",
        lambda self, params: {"success": True, "ready_to_grasp": True},
    )
    monkeypatch.setattr(
        EstimateGripperObjectPoseSkill, "execute", lambda self, params: {"success": True}
    )
    monkeypatch.setattr(
        grasp_verification, "_joint_positions", _grasp_positions(0.0)
    )

    skill = GraspSkill()
    skill.set_node(node)
    result = skill.execute({"object_name": "cup", "max_grasp_retries": 1})

    assert result["success"] is False
    assert result["grasp_verified"] is False
    assert result["grasp_retry_count"] == 1


def test_place_confirms_with_gripper_camera_when_target_object_given(monkeypatch):
    backend = FakeBackend()
    params = {
        "manipulation_backend": "stretch",
        "manipulation_end_effector_link": "link_grasp_center",
        "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
    }
    node = _make_node(params, _manipulation_backend=backend)

    observe_calls = []
    monkeypatch.setattr(
        sequence.ObserveGripperTargetSkill,
        "execute",
        lambda self, params: (
            observe_calls.append(params) or {"success": True, "ready_to_grasp": True}
        ),
    )

    skill = PlaceSkill()
    skill.set_node(node)
    result = skill.execute({"x": 0.4, "y": -0.2, "z": 0.15, "target_object": "table"})

    assert result["success"] is True
    assert result["gripper_camera_confirmed"] is True
    assert len(observe_calls) == 1
    assert observe_calls[0]["target_object"] == "table"


def test_place_marks_unconfirmed_when_no_target_object():
    backend = FakeBackend()
    params = {
        "manipulation_backend": "stretch",
        "manipulation_end_effector_link": "link_grasp_center",
        "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
    }
    node = _make_node(params, _manipulation_backend=backend)
    skill = PlaceSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.4, "y": -0.2, "z": 0.15})

    assert result["success"] is True
    assert result["gripper_camera_confirmed"] is False
    assert result["confirmation_skipped_reason"] == "no target_object given"


def test_place_skill_executes_release_sequence():
    backend = FakeBackend()
    params = {
        "manipulation_end_effector_link": "tool0",
        "manipulation_gripper_presets_json": '{"open":{"finger_joint":0.04}}',
    }
    node = _make_node(params, _manipulation_backend=backend)
    skill = PlaceSkill()
    skill.set_node(node)

    result = skill.execute({"x": 0.4, "y": -0.2, "z": 0.15})

    assert result["success"] is True
    assert backend.calls[1][0] == "pose"
    assert backend.calls[2] == ("joint", "gripper", {"finger_joint": 0.04})


def test_runtime_reports_unavailable_backend(monkeypatch):
    node = _make_node(
        {"manipulation_end_effector_link": "tool0"}, manipulation_backend="moveit"
    )
    runtime = MoveItManipulationRuntime(node)

    class RaisingBackend:
        def __init__(self, *args, **kwargs):
            raise ManipulationRuntimeUnavailableError("missing moveit")

    monkeypatch.setattr(
        "robo_claw_agent.manipulation_runtime.runtime.MoveItPyBackend",
        RaisingBackend,
    )

    with pytest.raises(ManipulationRuntimeUnavailableError, match="missing moveit"):
        runtime.move_to_named_pose("home")


def test_invalid_json_config_is_rejected():
    node = _make_node({"manipulation_named_poses_json": "not-json"})

    with pytest.raises(ManipulationConfigError):
        load_manipulation_config(node)
