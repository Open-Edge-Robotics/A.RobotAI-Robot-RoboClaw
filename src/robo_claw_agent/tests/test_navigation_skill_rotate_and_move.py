"""navigation skill rotate and move"""

import pytest

pytest.importorskip("rclpy")

from _navigation_helpers import (  # noqa: F401
    FaceDirectionSkill,
    MoveRelativeSkill,
    RotateSkill,
    SelfLocalizeSkill,
)


def test_rotate_skill_succeeds(monkeypatch):
    skill = RotateSkill()
    skill.node = object()
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    result = skill.execute({"angle_deg": 90.0})

    assert result["success"] is True
    assert result["angle_deg"] == 90.0
def test_rotate_skill_reports_cancellation(monkeypatch):
    skill = RotateSkill()
    skill.node = object()
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_navigation",
        lambda *args, **kwargs: (False, "취소되었습니다."),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (False, "취소되었습니다."),
    )

    result = skill.execute({"angle_deg": 45.0})

    assert result["success"] is False
    assert result["message"] == "회전 작업 취소됨"
def test_rotate_skill_fails_when_goal_rejected(monkeypatch):
    skill = RotateSkill()
    skill.node = object()
    skill.get_map_pose = lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"}

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_navigation",
        lambda *args, **kwargs: (False, "rotate goal rejected"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.rotate._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (False, "rotate goal rejected", None),
    )

    result = skill.execute({"angle_deg": 30.0})

    assert result["success"] is False
    assert result["message"] == "회전 실패: rotate goal rejected"
def test_move_relative_skill_succeeds(monkeypatch):
    skill = MoveRelativeSkill()
    skill.node = object()

    monkeypatch.setattr(
        skill,
        "get_map_pose",
        lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._send_navigation_goal",
        lambda node, x, y, frame: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (True, "success"),
    )

    result = skill.execute({"forward": 1.0})

    assert result["success"] is True
    assert result["to"]["x"] == 1.0
def test_move_relative_skill_rejects_zero_movement():
    skill = MoveRelativeSkill()
    skill.node = object()

    result = skill.execute({"forward": 0.0, "lateral": 0.0})

    assert result["success"] is False
    assert "0이 아니어야" in result["message"]
def test_move_relative_skill_fails_when_goal_rejected(monkeypatch):
    skill = MoveRelativeSkill()
    skill.node = object()

    monkeypatch.setattr(
        skill,
        "get_map_pose",
        lambda: {"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.move_relative._send_navigation_goal",
        lambda node, x, y, frame: (False, "move goal rejected", None),
    )

    result = skill.execute({"forward": 1.0})

    assert result["success"] is False
    assert "move goal rejected" in result["message"]
def test_face_direction_falls_back_to_navigation_when_spin_aborts(monkeypatch):
    skill = FaceDirectionSkill()
    skill.node = object()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_navigation",
        lambda *args, **kwargs: (True, "success"),
    )
    monkeypatch.setattr(
        skill,
        "get_map_pose",
        lambda: {"x": 1.0, "y": 2.0, "yaw": 1.0, "frame": "map"},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (True, "accepted", object()),
    )

    wait_results = iter(
        [
            (False, "방향 정렬 회전 실패(status=6/ABORTED)"),
            (True, "success"),
        ]
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: next(wait_results),
    )

    result = skill.execute({"direction": "east"})

    assert result["success"] is True
    assert result["method"] == "navigate_to_pose"
def test_face_direction_returns_failure_when_spin_and_fallback_fail(monkeypatch):
    skill = FaceDirectionSkill()
    skill.node = object()

    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_cmd_vel",
        lambda *args, **kwargs: (False, "fallback"),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.core._rotate_via_navigation",
        lambda *args, **kwargs: (False, "nav rejected"),
    )
    monkeypatch.setattr(
        skill,
        "get_map_pose",
        lambda: {"x": 1.0, "y": 2.0, "yaw": 1.0, "frame": "map"},
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._get_nav2_dependency_error",
        lambda: None,
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._send_spin_goal",
        lambda node, angle_rad, time_allowance_sec=None: (True, "accepted", object()),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.face_direction._wait_for_goal_result",
        lambda goal_handle, timeout_sec, label: (
            False,
            "방향 정렬 회전 실패(status=6/ABORTED)",
        ),
    )

    result = skill.execute({"direction": "east"})

    assert result["success"] is False
    assert "nav rejected" in result["message"]
def test_self_localize_fails_fast_when_nav2_dependency_missing(monkeypatch):
    skill = SelfLocalizeSkill()
    skill.node = object()
    monkeypatch.setattr(
        "robo_claw_agent.skills.navigation_skill.self_localize._get_nav2_dependency_error",
        lambda: "nav2 dependency missing",
    )
    result = skill.execute({})
    assert result["success"] is False
    assert result["message"] == "nav2 dependency missing"
