"""Stretch 모드 전환과 stow 시퀀스 테스트."""

import pytest

pytestmark = pytest.mark.ros2
pytest.importorskip("rclpy")

from _manip_helpers import (  # noqa: F401
    StowForNavigationSkill,
    StretchNavigationModeSkill,
    StretchPositionModeSkill,
    SwitchStretchModeSkill,
    _FakeFuture,
    _FakeTriggerResponse,
    _patch_stretch_mode_node,
    _patch_stretch_mode_nodes,
    _stub_head_observation,
    _stub_joint_positions,
)


def test_switch_stretch_mode_skill_calls_position_service(monkeypatch):
    future = _FakeFuture(_FakeTriggerResponse(True, "position ok"))
    holder = _patch_stretch_mode_node(monkeypatch, future)
    skill = SwitchStretchModeSkill()

    result = skill.execute({"mode": "position"})

    assert result["success"] is True
    assert result["mode"] == "position"
    assert result["service"] == "/switch_to_position_mode"
    assert result["message"] == "position ok"
    assert holder["node"].clients[0].service_name == "/switch_to_position_mode"
    assert holder["node"].destroyed is True


def test_switch_stretch_mode_skill_calls_navigation_service(monkeypatch):
    future = _FakeFuture(_FakeTriggerResponse(True, "navigation ok"))
    holder = _patch_stretch_mode_node(monkeypatch, future)
    skill = SwitchStretchModeSkill()

    result = skill.execute({"mode": "nav"})

    assert result["success"] is True
    assert result["mode"] == "navigation"
    assert result["service"] == "/switch_to_navigation_mode"
    assert holder["node"].clients[0].service_name == "/switch_to_navigation_mode"


def test_switch_stretch_mode_skill_rejects_invalid_mode():
    skill = SwitchStretchModeSkill()

    assert skill.validate_params({"mode": "unknown"}) is False
    result = skill.execute({"mode": "unknown"})

    assert result["success"] is False
    assert "position 또는 navigation" in result["message"]


def test_stretch_mode_skill_reports_timeout(monkeypatch):
    future = _FakeFuture(None, done=False)
    _patch_stretch_mode_node(monkeypatch, future)
    skill = StretchNavigationModeSkill()

    result = skill.execute({"timeout_sec": 0.1})

    assert result["success"] is False
    assert result["mode"] == "navigation"
    assert "타임아웃" in result["message"]
    assert future.cancelled is True


def test_stretch_position_mode_skill_uses_position(monkeypatch):
    future = _FakeFuture(_FakeTriggerResponse(True, ""))
    holder = _patch_stretch_mode_node(monkeypatch, future)
    skill = StretchPositionModeSkill()

    result = skill.execute({})

    assert result["success"] is True
    assert result["mode"] == "position"
    assert holder["node"].clients[0].service_name == "/switch_to_position_mode"


def test_stow_for_navigation_calls_stow_then_navigation(monkeypatch):
    stow_future = _FakeFuture(_FakeTriggerResponse(True, "stow ok"))
    nav_future = _FakeFuture(_FakeTriggerResponse(True, "nav ok"))
    holder = _patch_stretch_mode_nodes(monkeypatch, [stow_future, nav_future])
    skill = StowForNavigationSkill()

    result = skill.execute({})

    assert result["success"] is True
    assert result["stowed"] is True
    assert result["navigation_mode"] is True
    assert holder["nodes"][0].clients[0].service_name == "/stow_the_robot"
    assert holder["nodes"][1].clients[0].service_name == "/switch_to_navigation_mode"


def test_stow_for_navigation_reports_navigation_failure(monkeypatch):
    stow_future = _FakeFuture(_FakeTriggerResponse(True, "stow ok"))
    nav_future = _FakeFuture(_FakeTriggerResponse(False, "nav failed"))
    _patch_stretch_mode_nodes(monkeypatch, [stow_future, nav_future])
    skill = StowForNavigationSkill()

    result = skill.execute({})

    assert result["success"] is False
    assert result["stowed"] is True
    assert result["navigation_mode"] is False
    assert "navigation 모드 전환 실패" in result["message"]


def test_stow_for_navigation_can_skip_navigation_switch(monkeypatch):
    stow_future = _FakeFuture(_FakeTriggerResponse(True, "stow ok"))
    holder = _patch_stretch_mode_node(monkeypatch, stow_future)
    skill = StowForNavigationSkill()

    result = skill.execute({"switch_navigation": False})

    assert result["success"] is True
    assert result["stowed"] is True
    assert result["navigation_mode"] is False
    assert len(holder["nodes"]) == 1
    assert holder["nodes"][0].clients[0].service_name == "/stow_the_robot"
