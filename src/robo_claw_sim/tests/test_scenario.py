"""시나리오 로딩·검증·판정 테스트."""

from __future__ import annotations

import pytest
from robo_claw_sim.scenario import (
    Injection,
    RunResult,
    ScenarioError,
    build_scenario,
    load_scenario,
)

pytestmark = pytest.mark.unit

_MINIMAL = {
    "name": "minimal",
    "world": {
        "width": 6.0,
        "height": 4.0,
        "robot": {"x": 1.0, "y": 1.0, "yaw": 0.0},
        "objects": [{"name": "cup1", "class_id": "cup", "x": 2.0, "y": 1.0}],
    },
    "goals": [{"x": 2.0, "y": 2.0, "tolerance": 0.3, "label": "goal_a"}],
    "expect": {"max_collisions": 0, "min_goals_reached": 1},
}


def test_build_scenario_maps_world_and_goals() -> None:
    scenario = build_scenario(_MINIMAL)

    assert scenario.name == "minimal"
    assert scenario.world.robot.x == pytest.approx(1.0)
    assert len(scenario.world.objects) == 1
    assert scenario.world.objects[0].class_id == "cup"
    assert scenario.goals[0].label == "goal_a"
    assert scenario.goals[0].tolerance == pytest.approx(0.3)


def test_build_scenario_rejects_unknown_injection_kind() -> None:
    raw = dict(_MINIMAL, injections=[{"kind": "teleport_robot"}])

    with pytest.raises(ScenarioError):
        build_scenario(raw)


def test_build_scenario_rejects_goal_without_coordinates() -> None:
    raw = dict(_MINIMAL, goals=[{"y": 2.0}])

    with pytest.raises(ScenarioError):
        build_scenario(raw)


def test_build_scenario_rejects_invalid_world_size() -> None:
    raw = dict(_MINIMAL, world={"width": 0.0, "height": 4.0})

    with pytest.raises(ScenarioError):
        build_scenario(raw)


def test_load_scenario_from_yaml(tmp_path) -> None:
    path = tmp_path / "room.yaml"
    path.write_text(
        "\n".join(
            [
                "name: room",
                "timeout_sec: 30",
                "world:",
                "  width: 5.0",
                "  height: 5.0",
                "  obstacles:",
                "    - {type: box, name: wall, x: 2.5, y: 2.5, width: 0.2, height: 2.0}",
                "  objects:",
                "    - {name: bottle, class_id: bottle, x: 1.5, y: 1.5}",
                "goals:",
                "  - {x: 3.0, y: 1.0}",
                "expect:",
                "  min_goals_reached: 1",
                "injections:",
                "  - {kind: scan_drop, at_sec: 5.0, duration_sec: 2.0}",
            ]
        ),
        encoding="utf-8",
    )

    scenario = load_scenario(path)

    assert scenario.timeout_sec == pytest.approx(30.0)
    assert len(scenario.world.obstacles) == 1
    assert scenario.injections[0].kind == "scan_drop"


def test_injection_window_is_half_open() -> None:
    injection = Injection(kind="scan_drop", at_sec=2.0, duration_sec=1.0)

    assert injection.active(1.9) is False
    assert injection.active(2.0) is True
    assert injection.active(2.9) is True
    assert injection.active(3.0) is False


def test_evaluate_passes_when_expectations_met() -> None:
    scenario = build_scenario(_MINIMAL)
    result = RunResult(reached=[True], elapsed_sec=4.0)

    outcome = scenario_outcome(scenario, result)

    assert outcome.passed is True
    assert outcome.metrics["goals_reached"] == 1


def test_evaluate_reports_collision_and_missed_goal() -> None:
    scenario = build_scenario(_MINIMAL)
    result = RunResult(reached=[False], collisions=2, elapsed_sec=4.0)

    outcome = scenario_outcome(scenario, result)

    assert outcome.passed is False
    assert any("충돌" in reason for reason in outcome.reasons)
    assert any("목표 도달" in reason for reason in outcome.reasons)


def test_evaluate_reports_timeout_and_abort() -> None:
    scenario = build_scenario(_MINIMAL)
    result = RunResult(reached=[True], timed_out=True, aborted=[0])

    outcome = scenario_outcome(scenario, result)

    assert outcome.passed is False
    assert any("초과" in reason for reason in outcome.reasons)
    assert any("abort" in reason for reason in outcome.reasons)


def test_evaluate_honours_slow_run_limit() -> None:
    raw = dict(_MINIMAL, expect={"min_goals_reached": 1, "max_elapsed_sec": 5.0})
    scenario = build_scenario(raw)
    result = RunResult(reached=[True], elapsed_sec=9.0)

    outcome = scenario_outcome(scenario, result)

    assert outcome.passed is False
    assert any("소요 시간" in reason for reason in outcome.reasons)


def scenario_outcome(scenario, result):  # noqa: ANN001
    from robo_claw_sim.scenario import evaluate

    return evaluate(scenario, result)
