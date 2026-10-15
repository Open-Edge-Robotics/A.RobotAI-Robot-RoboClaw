"""navigation skill localize and approach"""

import pytest

pytest.importorskip("rclpy")

from _navigation_helpers import (  # noqa: F401
    NavigateToSkill,
    SelfLocalizeSkill,
    SimpleNamespace,
    _attach_fake_node,
    _FakeNode,
    _make_amcl_pose,
    _patch_navigate_deps,
    pytest,
)


def test_self_localize_fails_when_amcl_service_unavailable(monkeypatch):
    skill = SelfLocalizeSkill()
    skill.node = object()
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        skill, "_reinitialize_particles", lambda: (False, "AMCL 로컬라이제이션 모드가 아닙니다.")
    )
    result = skill.execute({})
    assert result["success"] is False
    assert "AMCL" in result["message"]
def test_self_localize_succeeds_when_covariance_converges(monkeypatch):
    skill = SelfLocalizeSkill()
    skill.node = _FakeNode(_make_amcl_pose(0.01, 0.01, 0.01, x=1.5, y=-2.0))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(skill, "_reinitialize_particles", lambda: (True, "ok"))
    monkeypatch.setattr(skill, "_spin_full_turn", lambda: None)
    monkeypatch.setattr(skill, "_nudge_forward", lambda step: None)

    result = skill.execute({"xy_std_threshold": 0.35, "yaw_std_threshold": 0.35})

    assert result["success"] is True
    assert result["pose"]["x"] == 1.5
    assert result["pose"]["y"] == -2.0
def test_self_localize_reports_nonzero_yaw_via_geo_utils(monkeypatch):
    """geo_utils.yaw_from_quaternion 재사용 회귀 테스트 (yaw != 0 케이스)."""
    skill = SelfLocalizeSkill()
    skill.node = _FakeNode(_make_amcl_pose(0.01, 0.01, 0.01, x=0.0, y=0.0, yaw_deg=90.0))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(skill, "_reinitialize_particles", lambda: (True, "ok"))
    monkeypatch.setattr(skill, "_spin_full_turn", lambda: None)
    monkeypatch.setattr(skill, "_nudge_forward", lambda step: None)

    result = skill.execute({"xy_std_threshold": 0.35, "yaw_std_threshold": 0.35})

    assert result["success"] is True
    assert result["pose"]["yaw_deg"] == pytest.approx(90.0)
def test_self_localize_fails_when_never_converges(monkeypatch):
    skill = SelfLocalizeSkill()
    skill.node = _FakeNode(_make_amcl_pose(1.0, 1.0, 1.0))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(skill, "_reinitialize_particles", lambda: (True, "ok"))
    monkeypatch.setattr(skill, "_spin_full_turn", lambda: None)
    nudges = []
    monkeypatch.setattr(skill, "_nudge_forward", lambda step: nudges.append(step))

    result = skill.execute({"max_attempts": 3})

    assert result["success"] is False
    # max_attempts=3 이면 마지막 시도 제외 2회 전진 유도
    assert len(nudges) == 2
def test_self_localize_check_converged_handles_none():
    skill = SelfLocalizeSkill()
    converged, xy_std, yaw_std = skill._check_converged(None, 0.35, 0.35)
    assert converged is False
    assert xy_std is None and yaw_std is None
def test_approach_object_handles_none_lidar_distance_with_depth_fallback(monkeypatch):
    from types import SimpleNamespace

    import numpy as np
    from robo_claw_agent.skills.navigation_skill.approach_object import ApproachObjectSkill

    skill = ApproachObjectSkill()
    skill.node = SimpleNamespace(has_parameter=lambda name: False)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}
    skill.get_opencv_image = lambda *args, **kwargs: (
        np.zeros((100, 100, 3), dtype=np.uint8),
        "/cam",
        False,
    )
    monkeypatch.setattr(skill, "_vlm_locate", lambda target, img: (0.5, 0.5))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._get_nav2_dependency_error",
        lambda: None,
    )

    dummy_scan = SimpleNamespace(
        angle_min=-3.14, angle_max=3.14, range_min=0.1, range_max=10.0, ranges=[float("inf")] * 360
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=2.0: dummy_scan

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._compute_depth_point_base",
        lambda *args, **kwargs: ((1.5, 0.0, 0.0), 1.5),
    )
    dummy_goal = SimpleNamespace(
        get_result_async=lambda: SimpleNamespace(
            add_done_callback=lambda cb: None,
            result=lambda: SimpleNamespace(status=4),
        )
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._send_navigation_goal",
        lambda *args, **kwargs: (True, "success", dummy_goal),
    )

    result = skill.execute({"target_object": "cup", "stop_distance_m": 0.5})
    assert result["success"] is True
    assert result["measured_distance_m"] == 1.5
def test_approach_object_fails_gracefully_when_all_sensors_none(monkeypatch):
    from types import SimpleNamespace

    import numpy as np
    from robo_claw_agent.skills.navigation_skill.approach_object import ApproachObjectSkill

    skill = ApproachObjectSkill()
    skill.node = SimpleNamespace(has_parameter=lambda name: False)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}
    skill.get_opencv_image = lambda *args, **kwargs: (
        np.zeros((100, 100, 3), dtype=np.uint8),
        "/cam",
        False,
    )
    monkeypatch.setattr(skill, "_vlm_locate", lambda target, img: (0.5, 0.5))
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._get_nav2_dependency_error",
        lambda: None,
    )

    dummy_scan = SimpleNamespace(
        angle_min=-3.14, angle_max=3.14, range_min=0.1, range_max=10.0, ranges=[float("inf")] * 360
    )
    skill.wait_for_message = lambda msg_type, topic, timeout_sec=2.0: dummy_scan

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.approach_object._compute_depth_point_base",
        lambda *args, **kwargs: (None, None),
    )

    result = skill.execute({"target_object": "cup"})
    assert result["success"] is False
    assert "측정하지 못했습니다" in result["message"] or "실패" in result["message"]
def test_navigate_to_supports_explicit_yaw(monkeypatch):
    """명시적 yaw 파라미터 전달 시 computed_yaw 대신 지정된 yaw가 전달된다."""
    skill = _attach_fake_node(NavigateToSkill())
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    captured = {}
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )

    def fake_send(node, x, y, frame, yaw=0.0):
        captured["yaw"] = yaw
        captured["frame"] = frame
        return True, "accepted", object()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        fake_send,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    result = skill.execute({"x": 1.0, "y": 1.0, "yaw": 1.57})

    assert result["success"] is True
    assert captured["yaw"] == pytest.approx(1.57)
def test_navigate_to_early_abort_diagnostics_when_robot_barely_moved(monkeypatch):
    """출발 직후 로봇이 거의 이동하지 못한 상태에서 ABORTED 발생 시 진행검사(progress checker) 진단 메시지를 제공한다."""
    skill = _attach_fake_node(NavigateToSkill())
    # 출발 위치와 거의 동일한 위치(0.02m 이동)
    skill.get_map_pose = lambda: {"x": 0.02, "y": 0.0, "yaw": 0.5, "frame": "map"}

    _patch_navigate_deps(monkeypatch, (False, "내비게이션 실패(status=6/ABORTED)"))

    result = skill.execute({"x": 5.0, "y": 5.0})

    assert result["success"] is False
    assert (
        "진행 검사" in result["message"]
        or "progress checker" in result["message"].lower()
        or "회전" in result["message"]
    )
    assert result.get("failure_reason") in (
        "progress_checker_timeout",
        "early_rotation_abort",
    ) or "progress_checker" in str(result)
def test_navigate_to_align_heading_first_performs_spin(monkeypatch):
    """align_heading_first=True일 때 큰 회전각이 필요하면 _send_spin_goal로 사전 정렬 후 이동한다."""
    import math

    skill = _attach_fake_node(NavigateToSkill())
    # 현재 x=0, y=0, yaw=0 (동쪽), 목표는 x=0, y=2 (북쪽: 90도 회전 필요)
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    spin_calls = []
    nav_calls = []

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (
            spin_calls.append(angle_rad) or (True, "accepted", object())
        ),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._send_navigation_goal",
        lambda node, x, y, frame, yaw=0.0: (
            nav_calls.append((x, y, yaw)) or (True, "accepted", object())
        ),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.navigate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    result = skill.execute({"x": 0.0, "y": 2.0, "align_heading_first": True})

    assert result["success"] is True
    assert len(spin_calls) == 1
    assert spin_calls[0] == pytest.approx(math.pi / 2.0, abs=0.05)
    assert len(nav_calls) == 1
