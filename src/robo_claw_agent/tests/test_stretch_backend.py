"""StretchDriverBackend 단위 테스트.

모듈을 ROS2 의존도에 따라 분리해 임포트한다.

- 순수 키네마틱/검증(`stretch_kinematics`): numpy 만 필요. ROS2 메시지가
  없는 로컬 환경에서도 실행된다.
- config 파싱(`config`/`exceptions`): ROS2 메시지 의존 없음.
- `stretch_backend` 본체: rclpy + control_msgs/trajectory_msgs 필요.
  ROS2 환경(CI / 개발 머신)에서만 실행된다.

- 순수 검증 함수(_validate_joint_names, _check_exclusive): ROS2 객체 미사용.
- _build_goal: 실제 control_msgs/trajectory_msgs 메시지 검증.
- move_to_*: 인스턴스를 __new__ 로 우회 생성해 서비스/action 없이 로직 검증.
- config 파싱: FakeNode 로 manipulation_backend/named_pose_joint_values 분기 검증.
"""

import threading
import time

import pytest
from robo_claw_agent.skills.limits import check_joint_targets, get_joint_limits_raw

# 순수 키네마틱/검증: numpy 만 필요 (ROS2 메시지 의존 없음).
try:
    import numpy as np
    from robo_claw_agent.manipulation_runtime import stretch_kinematics as sk

    KIN_OK = True
except ImportError:
    sk = None
    np = None
    KIN_OK = False

# config 파싱 / 예외: ROS2 메시지 의존 없음.
try:
    from robo_claw_agent.manipulation_runtime.config import load_manipulation_config
    from robo_claw_agent.manipulation_runtime.exceptions import (
        ManipulationConfigError,
        ManipulationError,
        ManipulationRuntimeUnavailableError,
    )

    CONFIG_OK = True
except ImportError:
    load_manipulation_config = None
    ManipulationConfigError = RuntimeError
    ManipulationError = RuntimeError
    ManipulationRuntimeUnavailableError = RuntimeError
    CONFIG_OK = False

# stretch_backend 본체: rclpy + control_msgs/trajectory_msgs 필요.
try:
    from robo_claw_agent.manipulation_runtime import stretch_backend as sb

    SB_OK = True
except ImportError:
    sb = None
    SB_OK = False

skip_kin = pytest.mark.skipif(not KIN_OK, reason="numpy 가 필요합니다")
skip_config = pytest.mark.skipif(not CONFIG_OK, reason="config 로드에 실패했습니다")
skip_sb = pytest.mark.skipif(
    not SB_OK, reason="ROS2(control_msgs/trajectory_msgs) 환경이 필요합니다"
)


@skip_sb
def test_is_stowed_requires_fresh_joint_state():
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._joint_positions = {
        "joint_lift": 0.2,
        "wrist_extension": 0.0,
        "joint_wrist_yaw": 0.0,
    }
    backend._joint_state_monotonic = time.monotonic()
    backend._joint_state_lock = threading.Lock()

    assert backend.is_stowed() is True
    backend._joint_state_monotonic -= 2.0
    assert backend.is_stowed() is False


@skip_sb
def test_is_stowed_rejects_extended_arm_or_missing_joint():
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._joint_state_monotonic = time.monotonic()
    backend._joint_state_lock = threading.Lock()
    backend._joint_positions = {"joint_lift": 0.2, "wrist_extension": 0.2}

    assert backend.is_stowed() is False


# ── _validate_joint_names (순수 함수) ──


@skip_kin
def test_validate_joint_names_valid():
    assert sk._validate_joint_names({"joint_lift": 0.5, "wrist_extension": 0.2}) is None


@skip_kin
def test_validate_joint_names_invalid():
    err = sk._validate_joint_names({"joint_arm_l0": 0.1})
    assert err is not None
    assert "joint_arm_l0" in err


@skip_kin
def test_validate_joint_names_empty():
    err = sk._validate_joint_names({})
    assert err is not None


# ── _check_exclusive (순수 함수) ──


@skip_kin
def test_check_exclusive_single_ok():
    assert sk._check_exclusive(["wrist_extension"]) is None
    assert sk._check_exclusive(["gripper_aperture"]) is None


@skip_kin
def test_check_exclusive_two_in_group_violation():
    err = sk._check_exclusive(["wrist_extension", "joint_arm"])
    assert err is not None
    assert "배타" in err


@skip_kin
def test_check_exclusive_gripper_group_violation():
    err = sk._check_exclusive(["gripper_aperture", "stretch_gripper"])
    assert err is not None


@skip_kin
def test_check_exclusive_cross_group_ok():
    # 서로 다른 배타 그룹에 하나씩이면 OK
    assert sk._check_exclusive(["wrist_extension", "gripper_aperture"]) is None


# ── _build_goal (ROS2 메시지) ──


@skip_sb
def test_build_goal_structure():
    goal = sb._build_goal({"wrist_extension": 0.3, "joint_lift": 0.5})
    assert list(goal.trajectory.joint_names) == ["wrist_extension", "joint_lift"]
    assert len(goal.trajectory.points) == 1
    pt = goal.trajectory.points[0]
    assert list(pt.positions) == [0.3, 0.5]
    assert pt.time_from_start.sec == 0
    assert pt.time_from_start.nanosec == 0
    assert goal.goal_time_tolerance.sec == 1


@skip_sb
def test_result_error_names_uses_available_constants():
    names = sb._result_error_names()
    result_type = sb.FollowJointTrajectory.Result
    assert names[int(result_type.SUCCESSFUL)] == "SUCCESSFUL"
    assert names[int(result_type.INVALID_GOAL)] == "INVALID_GOAL"
    assert "OLD_HEADER_TIMING_OUT" in names.values() or "OLD_HEADER_TIMESTAMP" in names.values()


# ── move_to_pose_target: 수치 IK (FK/IK 순수 함수 + pose→joint 매핑) ──


def _pose(x, y, z, frame="base_link", quat=None):
    pose = type("P", (), {})()
    pose.frame_id = frame
    pose.position = {"x": x, "y": y, "z": z}
    pose.orientation = quat if quat is not None else {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0}
    return pose


@skip_kin
def test_fk_position_zero_consistent():
    """관절 0 EE 위치 — IK 공식의 기준점 일관성."""
    p = sk._fk_position({})
    # 분석값 (urdf_numeric_ik 검증) 과 일치해야 한다.
    assert abs(p[2] - 0.1097) < 1e-3


@skip_kin
def test_ik_position_roundtrip():
    """FK 샘플 → IK 역산 시 joint 오차 1e-2 이내."""
    rng = np.random.default_rng(11)
    max_err = 0.0
    for _ in range(60):
        ql = rng.uniform(0.1, 0.9)
        qe = rng.uniform(0.05, 0.45)
        qy = rng.uniform(-1.2, 1.2)
        pos = sk._fk_position({"joint_lift": ql, "wrist_extension": qe, "joint_wrist_yaw": qy})
        sol = sk._ik_position(pos)
        assert sol is not None
        el, ee, ey = sol
        ye = (ey - qy + np.pi) % (2 * np.pi) - np.pi
        max_err = max(max_err, abs(el - ql), abs(ee - qe), abs(ye))
    assert max_err < 1e-2


@skip_kin
def test_ik_position_unreachable_returns_none():
    # arm 가동 범위 밖 (y=-3m)
    assert sk._ik_position([0.0, -3.0, 0.5]) is None
    # z 너무 높 (lift 한계 1.1m + offset)
    assert sk._ik_position([0.0, -0.3, 5.0]) is None


# ── compute_align_rotation_rad / compute_align_rotation_from_bearing_rad (순수 함수) ──


@skip_kin
def test_compute_align_rotation_zero_when_already_reachable():
    # 이미 도달 가능한 FK 샘플 좌표는 회전 불필요(0.0)를 반환해야 한다.
    pos = sk._fk_position({"joint_lift": 0.5, "wrist_extension": 0.3, "joint_wrist_yaw": 0.2})
    assert sk.compute_align_rotation_rad(pos) == 0.0


@skip_kin
def test_compute_align_rotation_unreachable_returns_none():
    # test_ik_position_unreachable_returns_none 과 동일 좌표(거리 자체가 초과) 재사용.
    assert sk.compute_align_rotation_rad([0.0, -3.0, 0.5]) is None
    assert sk.compute_align_rotation_rad([0.0, -0.3, 5.0]) is None


@skip_kin
def test_compute_align_rotation_front_object_becomes_reachable():
    # 정면(로봇 x축 위) 물체는 팔이 오른쪽으로만 뻗는 구조상 회전 없이는 도달 불가.
    target = (0.3, 0.0, 0.5)
    assert sk._ik_position(target) is None

    theta = sk.compute_align_rotation_rad(target)
    assert theta is not None
    assert theta != 0.0

    rotated = sk._rotate_xy(target, theta)
    assert sk._ik_position(rotated) is not None


@skip_kin
def test_compute_align_rotation_roundtrip():
    """FK 샘플을 임의 각도만큼 되돌린 좌표에서, 계산된 회전각으로 되돌리면 다시
    도달 가능해지는지 확인(정확히 원래 각도를 복원할 필요는 없음 — 도달 가능한
    회전각이 여러 개 있을 수 있으므로, 계산된 회전각 적용 후 실제 도달 가능성만 검증)."""
    rng = np.random.default_rng(123)
    for _ in range(10):
        ql = rng.uniform(0.1, 0.9)
        qe = rng.uniform(0.05, 0.45)
        qy = rng.uniform(-0.3, 0.3)
        pos = sk._fk_position({"joint_lift": ql, "wrist_extension": qe, "joint_wrist_yaw": qy})
        applied_rotation = rng.uniform(-np.radians(80), np.radians(80))
        # pos 는 회전 후 도달 가능한 점이므로, applied_rotation 만큼 회전하기 전
        # (현재) 좌표계에서는 그 역회전한 위치에 있는 것으로 취급한다.
        target = sk._rotate_xy(pos, -applied_rotation)

        theta = sk.compute_align_rotation_rad(target)
        assert theta is not None
        rotated = sk._rotate_xy(target, theta)
        assert sk._ik_position(rotated) is not None


@skip_sb
@skip_kin
def test_check_reachable_wraps_ik_position():
    reachable = sk._fk_position({"joint_lift": 0.5, "wrist_extension": 0.3, "joint_wrist_yaw": 0.2})
    assert sb.check_reachable(reachable) is True
    assert sb.check_reachable([0.0, -3.0, 0.5]) is False


@skip_kin
def test_compute_align_rotation_from_bearing_matches_arm_axis():
    arm_bearing = sk._arm_reach_bearing_rad()
    # 방위값이 팔 작업축과 정확히 일치하면 회전 불필요(0.0에 근접).
    theta = sk.compute_align_rotation_from_bearing_rad(arm_bearing)
    assert abs(theta) < 1e-9

    # 정면 방위(0rad)라면, 팔 작업축까지의 각도 차이를 그대로 반환해야 한다.
    theta_front = sk.compute_align_rotation_from_bearing_rad(0.0)
    assert abs(theta_front - sk._normalize_angle(-arm_bearing)) < 1e-9


@skip_kin
def test_compute_centering_rotation_rotates_reachable_target_to_arm_axis():
    arm_bearing = sk._arm_reach_bearing_rad()
    target_bearing = arm_bearing + np.radians(20.0)
    target = (0.7 * np.cos(target_bearing), 0.7 * np.sin(target_bearing), 0.5)

    theta = sk.compute_centering_rotation_rad(target)

    assert theta == pytest.approx(np.radians(20.0), abs=1e-6)


@skip_sb
def test_move_to_pose_target_calls_joint_target(monkeypatch):
    """pose → IK → joint map 이 move_to_joint_target 에 전달되는지 검증."""
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._config = type("C", (), {"base_frame": "base_link"})()

    captured = {}

    def fake_move(group_name, joint_values):
        captured["group"] = group_name
        captured["joints"] = dict(joint_values)

    monkeypatch.setattr(backend, "move_to_joint_target", fake_move)

    # FK 로 정합한 도달 가능 pose 사용.
    target_pos = sk._fk_position(
        {"joint_lift": 0.5, "wrist_extension": 0.3, "joint_wrist_yaw": 0.5}
    )
    backend.move_to_pose_target(
        "arm", _pose(target_pos[0], target_pos[1], target_pos[2]), "link_grasp_center"
    )

    assert captured["group"] == "arm"
    j = captured["joints"]
    assert set(j.keys()) == {
        "joint_lift",
        "wrist_extension",
        "joint_wrist_yaw",
        "joint_wrist_pitch",
        "joint_wrist_roll",
    }
    assert abs(j["joint_lift"] - 0.5) < 1e-2
    assert abs(j["wrist_extension"] - 0.3) < 1e-2
    assert abs((j["joint_wrist_yaw"] - 0.5 + np.pi) % (2 * np.pi) - np.pi) < 1e-2


@skip_sb
def test_move_to_pose_target_wrong_frame_raises():
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._config = type("C", (), {"base_frame": "base_link"})()
    with pytest.raises(ManipulationConfigError):
        backend.move_to_pose_target("arm", _pose(0.1, -0.3, 0.4, frame="map"), "link_grasp_center")


@skip_sb
def test_move_to_pose_target_unreachable_raises():
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._config = type("C", (), {"base_frame": "base_link"})()
    with pytest.raises(ManipulationError):
        backend.move_to_pose_target("arm", _pose(0.0, -3.0, 0.5), "link_grasp_center")


@skip_sb
def test_move_to_joint_target_switches_position_then_navigation(monkeypatch):
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    calls = []

    monkeypatch.setattr(backend, "_switch_to_position_mode", lambda: calls.append("position"))
    monkeypatch.setattr(backend, "_send_goal", lambda _goal: calls.append("goal"))
    monkeypatch.setattr(backend, "_restore_navigation_mode", lambda: calls.append("navigation"))

    backend.move_to_joint_target("gripper", {"gripper_aperture": 0.0})

    assert calls == ["position", "goal", "navigation"]


@skip_sb
def test_move_to_joint_target_restores_navigation_on_failure(monkeypatch):
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    calls = []

    def fail_goal(_goal):
        calls.append("goal")
        raise ManipulationError("goal failed")

    monkeypatch.setattr(backend, "_switch_to_position_mode", lambda: calls.append("position"))
    monkeypatch.setattr(backend, "_send_goal", fail_goal)
    monkeypatch.setattr(backend, "_restore_navigation_mode", lambda: calls.append("navigation"))

    with pytest.raises(ManipulationError):
        backend.move_to_joint_target("gripper", {"gripper_aperture": 0.0})

    assert calls == ["position", "goal", "navigation"]


# ── move_to_named_pose: joint map 경로 ──


class _StubConfig:
    def __init__(self, joint_values_map):
        self.named_pose_joint_values = joint_values_map


@skip_sb
def test_move_to_named_pose_uses_joint_map():
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._config = _StubConfig({"ready": {"joint_lift": 0.7, "wrist_extension": 0.2}})
    captured: dict = {}

    def fake_move_to_joint_target(group_name, joint_values):
        captured["group"] = group_name
        captured["joints"] = dict(joint_values)

    backend.move_to_joint_target = fake_move_to_joint_target

    backend.move_to_named_pose("arm", "ready")
    assert captured["group"] == "arm"
    assert captured["joints"] == {"joint_lift": 0.7, "wrist_extension": 0.2}


@skip_sb
def test_move_to_named_pose_unknown_raises_config_error():
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._config = _StubConfig({})
    backend.move_to_joint_target = lambda *_a, **_k: None
    with pytest.raises(ManipulationConfigError):
        backend.move_to_named_pose("arm", "nonexistent_pose")


@skip_sb
def test_move_to_named_pose_home_calls_service(monkeypatch):
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._config = _StubConfig({})

    calls: list = []

    monkeypatch.setattr(backend, "_switch_to_position_mode", lambda: calls.append("position"))
    monkeypatch.setattr(backend, "_restore_navigation_mode", lambda: calls.append("navigation"))

    def fake_call_trigger_service(client, service_name, timeout_sec):
        calls.append(service_name)

    backend._service_clients = {"home": object(), "stow": object()}
    backend._call_trigger_service = fake_call_trigger_service

    backend.move_to_named_pose("arm", "home")
    assert calls == ["position", "/home_the_robot", "navigation"]


@skip_sb
def test_move_to_named_pose_home_restores_navigation_on_failure(monkeypatch):
    backend = sb.StretchDriverBackend.__new__(sb.StretchDriverBackend)
    backend._config = _StubConfig({})
    backend._service_clients = {"home": object(), "stow": object()}
    calls: list = []

    def fail_service(_client, service_name, _timeout_sec):
        calls.append(service_name)
        raise ManipulationError("service failed")

    monkeypatch.setattr(backend, "_switch_to_position_mode", lambda: calls.append("position"))
    monkeypatch.setattr(backend, "_call_trigger_service", fail_service)
    monkeypatch.setattr(backend, "_restore_navigation_mode", lambda: calls.append("navigation"))

    with pytest.raises(ManipulationError):
        backend.move_to_named_pose("arm", "home")

    assert calls == ["position", "/home_the_robot", "navigation"]


# ── config 파싱: named_pose_joint_values dict/str 분기 ──


class _FakeParam:
    def __init__(self, value):
        self.value = value


class _FakeNode:
    def __init__(self, params):
        self._params = params

    def has_parameter(self, name):
        return name in self._params

    def get_parameter(self, name):
        return _FakeParam(self._params[name])


def _stretch_params():
    return {
        "manipulation_backend": "stretch",
        "manipulation_enabled": True,
        "manipulation_arm_group": "arm",
        "manipulation_gripper_group": "gripper",
        "manipulation_end_effector_link": "link_grasp_center",
        "manipulation_base_frame": "base_link",
        "manipulation_tool_frame": "",
        "manipulation_named_poses_json": (
            '{"home":"home","stow":"stow","ready":{"joint_lift":0.7,"wrist_extension":0.2}}'
        ),
        "manipulation_gripper_presets_json": '{"open":{"gripper_aperture":0.16}}',
        "manipulation_cmd_vel_topic": "/stretch/cmd_vel",
        "manipulation_planning_pipeline": "",
        "manipulation_planner_id": "",
        "manipulation_cartesian_step": 0.01,
        "manipulation_velocity_scaling": 0.2,
        "manipulation_acceleration_scaling": 0.2,
        "manipulation_default_approach_distance_m": 0.1,
        "manipulation_default_retreat_distance_m": 0.1,
    }


@skip_config
def test_load_config_backend_and_named_pose_joint_values():
    config = load_manipulation_config(_FakeNode(_stretch_params()))
    assert config.backend == "stretch"
    assert config.end_effector_link == "link_grasp_center"
    # home/stow: 문자열 → 서비스 매핑 (joint_values 에 없음)
    assert config.named_poses["home"] == "home"
    assert config.named_poses["stow"] == "stow"
    assert "home" not in config.named_pose_joint_values
    # ready: 객체 → joint 값 사전정의, alias 자기 자신으로 매핑
    assert config.named_poses["ready"] == "ready"
    assert config.named_pose_joint_values["ready"] == {
        "joint_lift": 0.7,
        "wrist_extension": 0.2,
    }
    # gripper preset
    assert config.gripper_presets["open"] == {"gripper_aperture": 0.16}
    assert config.cmd_vel_topic == "/stretch/cmd_vel"


@skip_config
def test_load_config_default_backend_moveit():
    params = _stretch_params()
    params["manipulation_backend"] = "moveit"
    config = load_manipulation_config(_FakeNode(params))
    assert config.backend == "moveit"


# ── limits raw 키 인식 (ROS2 의존 없음, 본 파일 가드와 무관하게 동작) ──


STRETCH_LIMITS = {
    "manipulation": {
        "joint_limits": {
            "joint_lift": [0.0, 1.1],
            "wrist_extension": [0.0, 0.52],
        }
    }
}


def test_get_joint_limits_raw():
    limits = get_joint_limits_raw(STRETCH_LIMITS)
    assert limits["joint_lift"] == (0.0, 1.1)
    assert limits["wrist_extension"] == (0.0, 0.52)
    assert get_joint_limits_raw(None) == {}


def test_check_joint_targets_raw_within():
    assert check_joint_targets({"joint_lift": 0.5, "wrist_extension": 0.2}, STRETCH_LIMITS) is None


def test_check_joint_targets_raw_exceeds():
    err = check_joint_targets({"wrist_extension": 0.9}, STRETCH_LIMITS)
    assert err is not None
    assert "wrist_extension" in err


def test_check_joint_targets_raw_below():
    err = check_joint_targets({"joint_lift": -0.1}, STRETCH_LIMITS)
    assert err is not None
    assert "joint_lift" in err
