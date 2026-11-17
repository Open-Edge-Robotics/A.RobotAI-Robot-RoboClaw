"""시나리오 로딩·검증·판정 테스트."""

from __future__ import annotations

from pathlib import Path

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


_SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"
_SHIPPED_SCENARIOS = sorted(_SCENARIO_DIR.glob("*.yaml"))


@pytest.mark.parametrize("path", _SHIPPED_SCENARIOS, ids=lambda p: p.stem)
def test_shipped_scenario_is_valid(path: Path) -> None:
    """저장소에 커밋된 시나리오는 모두 로딩·검증을 통과해야 한다.

    ``task sim-test``/``sim-compare`` 가 이 파일들을 그대로 실행하므로, 스키마 오류나
    존재하지 않는 goal 은 CI 이전에 여기서 걸러야 한다.
    """
    scenario = load_scenario(path)

    assert scenario.name == path.stem
    assert scenario.goals or scenario.manipulation, (
        f"{path.name}: goals 또는 manipulation 중 하나는 있어야 합니다"
    )
    assert scenario.timeout_sec > 0.0
    for goal in scenario.goals:
        assert scenario.world.is_free(goal.x, goal.y, scenario.world.robot_radius), (
            f"{path.name}: goal({goal.label}) 이 점유 위치입니다"
        )
    assert scenario.expect.min_goals_reached <= len(scenario.goals)


_MANIPULATION_SCENARIO = {
    "name": "grasp_probe",
    "world": {
        "width": 6.0,
        "height": 4.0,
        "robot": {"x": 1.0, "y": 1.0, "yaw": 0.0},
        "objects": [{"name": "cup_1", "class_id": "cup", "x": 1.42, "y": 1.0}],
    },
    "goals": [],
    "manipulation": {
        "points": [
            {"sec": 0.0, "joints": {"arm_x": 0.0, "gripper_joint": 0.1}},
            {"sec": 2.0, "joints": {"arm_x": 0.35, "gripper_joint": -0.02}},
        ]
    },
    "expect": {"min_goals_reached": 0, "grasped_objects": ["cup_1"]},
}


def test_manipulation_script_is_parsed() -> None:
    scenario = build_scenario(_MANIPULATION_SCENARIO)

    assert scenario.manipulation is not None
    assert scenario.manipulation.joint_names() == ["arm_x", "gripper_joint"]
    assert scenario.manipulation.duration_sec == pytest.approx(2.0)
    assert scenario.expect.grasped_objects == ("cup_1",)


def test_evaluate_reports_missing_grasp() -> None:
    scenario = build_scenario(_MANIPULATION_SCENARIO)
    result = RunResult(reached=[], grasped_objects=[])

    outcome = scenario_outcome(scenario, result)

    assert outcome.passed is False
    assert any("파지 실패" in reason for reason in outcome.reasons)


def test_evaluate_accepts_expected_grasp() -> None:
    scenario = build_scenario(_MANIPULATION_SCENARIO)
    result = RunResult(reached=[], grasped_objects=["cup_1"])

    outcome = scenario_outcome(scenario, result)

    assert outcome.passed is True


def test_manipulation_points_must_be_ordered() -> None:
    raw = dict(_MANIPULATION_SCENARIO)
    raw["manipulation"] = {
        "points": [
            {"sec": 2.0, "joints": {"arm_x": 0.1}},
            {"sec": 1.0, "joints": {"arm_x": 0.2}},
        ]
    }

    with pytest.raises(ScenarioError):
        build_scenario(raw)


def test_evaluate_reports_missing_detection() -> None:
    raw = dict(_MINIMAL)
    raw["expect"] = {"min_goals_reached": 1, "detected_objects": ["cup"]}
    scenario = build_scenario(raw)

    outcome = scenario_outcome(scenario, RunResult(reached=[True], detected_objects=[]))

    assert outcome.passed is False
    assert any("검출 실패" in reason for reason in outcome.reasons)


def test_evaluate_accepts_expected_detection() -> None:
    raw = dict(_MINIMAL)
    raw["expect"] = {"min_goals_reached": 1, "detected_objects": ["cup"]}
    scenario = build_scenario(raw)

    outcome = scenario_outcome(scenario, RunResult(reached=[True], detected_objects=["cup"]))

    assert outcome.passed is True
    assert outcome.metrics["detected_objects"] == ["cup"]
