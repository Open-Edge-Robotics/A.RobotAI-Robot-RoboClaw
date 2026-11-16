"""시나리오 정의·로딩·판정(순수 파이썬).

"충분한 데이터가 없는 시뮬레이션" 문제를 해결하는 부분이다. 월드 배치, 객체의
정답 라벨, 도달해야 할 목표, 기대 결과, 실패 주입을 YAML 하나로 선언하고,
실행 결과와 비교해 통과/실패를 결정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .nav import GoalPose
from .world import BoxObstacle, CircleObstacle, Pose2D, SimObject, World

SUPPORTED_INJECTIONS = {
    "scan_drop",  # /scan 발행 중단
    "imu_drop",  # /imu 발행 중단
    "battery_drain",  # 즉시 배터리 소진
    "nav_reject",  # 지정 goal 즉시 거부
    "nav_abort",  # 지정 goal 진행 중 abort
}


class ScenarioError(ValueError):
    """시나리오 파일이 스키마를 위반했을 때."""


@dataclass(frozen=True)
class Injection:
    kind: str
    at_sec: float = 0.0
    duration_sec: float = 0.0
    goal_index: int = 0
    battery_percent: float = 0.0

    def active(self, elapsed_sec: float) -> bool:
        if self.duration_sec <= 0.0:
            return elapsed_sec >= self.at_sec
        return self.at_sec <= elapsed_sec < self.at_sec + self.duration_sec


@dataclass(frozen=True)
class Expectation:
    max_collisions: int = 0
    min_goals_reached: int = 1
    max_elapsed_sec: float | None = None
    require_no_abort: bool = True


@dataclass
class Scenario:
    name: str
    world: World
    goals: list[GoalPose] = field(default_factory=list)
    timeout_sec: float = 90.0
    expect: Expectation = field(default_factory=Expectation)
    injections: list[Injection] = field(default_factory=list)


@dataclass
class RunResult:
    reached: list[bool] = field(default_factory=list)
    aborted: list[int] = field(default_factory=list)
    rejected: list[int] = field(default_factory=list)
    elapsed_sec: float = 0.0
    collisions: int = 0
    traveled_m: float = 0.0
    timed_out: bool = False
    error: str = ""


@dataclass
class Outcome:
    passed: bool
    reasons: list[str]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
        }


def load_scenario(path: str | Path) -> Scenario:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ScenarioError(f"시나리오 최상위는 매핑이어야 합니다: {path}")
    return build_scenario(raw, source=str(path))


def build_scenario(raw: dict[str, Any], source: str = "<inline>") -> Scenario:
    name = str(raw.get("name") or Path(source).stem)
    world = _build_world(raw.get("world") or {}, source)
    goals = [_build_goal(item, source) for item in raw.get("goals") or []]
    expect = _build_expectation(raw.get("expect") or {}, source)
    injections = _build_injections(raw.get("injections") or [], source)
    timeout_sec = float(raw.get("timeout_sec", 90.0))
    if timeout_sec <= 0.0:
        raise ScenarioError("timeout_sec 는 0보다 커야 합니다")
    return Scenario(
        name=name,
        world=world,
        goals=goals,
        timeout_sec=timeout_sec,
        expect=expect,
        injections=injections,
    )


def evaluate(scenario: Scenario, result: RunResult) -> Outcome:
    """실행 결과와 기대치를 비교한다. 실패 사유를 사람이 읽을 수 있는 문장으로 남긴다."""
    reasons: list[str] = []
    metrics: dict[str, Any] = {
        "goals_total": len(scenario.goals),
        "goals_reached": sum(1 for flag in result.reached if flag),
        "aborted_goals": list(result.aborted),
        "rejected_goals": list(result.rejected),
        "collisions": result.collisions,
        "elapsed_sec": round(result.elapsed_sec, 3),
        "traveled_m": round(result.traveled_m, 3),
        "timed_out": result.timed_out,
    }

    if result.error:
        reasons.append(f"실행 오류: {result.error}")
    if result.timed_out:
        reasons.append(f"제한 시간({scenario.timeout_sec}s) 초과")
    if result.collisions > scenario.expect.max_collisions:
        reasons.append(f"충돌 {result.collisions}회 (허용 {scenario.expect.max_collisions}회)")
    reached_count = metrics["goals_reached"]
    if reached_count < scenario.expect.min_goals_reached:
        reasons.append(
            f"목표 도달 {reached_count}/{len(scenario.goals)} "
            f"(최소 {scenario.expect.min_goals_reached}개 필요)"
        )
    if scenario.expect.require_no_abort and result.aborted:
        reasons.append(f"abort 된 goal: {result.aborted}")
    max_elapsed = scenario.expect.max_elapsed_sec
    if max_elapsed is not None and result.elapsed_sec > max_elapsed:
        reasons.append(f"소요 시간 {result.elapsed_sec:.1f}s > 허용 {max_elapsed:.1f}s")

    return Outcome(passed=not reasons, reasons=reasons, metrics=metrics)


def _build_world(raw: dict[str, Any], source: str) -> World:
    robot_raw = raw.get("robot") or {}
    robot = Pose2D(
        x=float(robot_raw.get("x", 0.0)),
        y=float(robot_raw.get("y", 0.0)),
        yaw=float(robot_raw.get("yaw", 0.0)),
    )
    world = World(
        width=float(raw.get("width", 8.0)),
        height=float(raw.get("height", 6.0)),
        resolution=float(raw.get("resolution", 0.05)),
        robot=robot,
        robot_radius=float(raw.get("robot_radius", 0.22)),
        max_linear_speed=float(raw.get("max_linear_speed", 0.5)),
        max_angular_speed=float(raw.get("max_angular_speed", 1.5)),
    )
    if world.width <= 0.0 or world.height <= 0.0:
        raise ScenarioError(f"world 크기는 양수여야 합니다 ({source})")
    if world.resolution <= 0.0:
        raise ScenarioError(f"world.resolution 은 양수여야 합니다 ({source})")

    for item in raw.get("obstacles") or []:
        obstacle_type = str(item.get("type", "box"))
        if obstacle_type == "box":
            world.add_obstacle(
                BoxObstacle(
                    name=str(item.get("name", f"box_{len(world.obstacles)}")),
                    x=float(item["x"]),
                    y=float(item["y"]),
                    width=float(item["width"]),
                    height=float(item["height"]),
                )
            )
        elif obstacle_type == "circle":
            world.add_obstacle(
                CircleObstacle(
                    name=str(item.get("name", f"circle_{len(world.obstacles)}")),
                    x=float(item["x"]),
                    y=float(item["y"]),
                    radius=float(item["radius"]),
                )
            )
        else:
            raise ScenarioError(f"지원하지 않는 obstacle type: {obstacle_type} ({source})")

    for item in raw.get("objects") or []:
        world.add_object(
            SimObject(
                name=str(item["name"]),
                class_id=str(item.get("class_id", item["name"])),
                x=float(item["x"]),
                y=float(item["y"]),
                radius=float(item.get("radius", 0.06)),
                graspable=bool(item.get("graspable", True)),
            )
        )
    return world


def _build_goal(item: dict[str, Any], source: str) -> GoalPose:
    for key in ("x", "y"):
        if key not in item:
            raise ScenarioError(f"goal 에 '{key}' 가 없습니다 ({source})")
    yaw_raw = item.get("yaw")
    return GoalPose(
        x=float(item["x"]),
        y=float(item["y"]),
        yaw=None if yaw_raw is None else float(yaw_raw),
        tolerance=float(item.get("tolerance", 0.25)),
        yaw_tolerance=float(item.get("yaw_tolerance", 0.15)),
        label=str(item.get("label", "")),
    )


def _build_expectation(raw: dict[str, Any], source: str) -> Expectation:
    max_elapsed = raw.get("max_elapsed_sec")
    return Expectation(
        max_collisions=int(raw.get("max_collisions", 0)),
        min_goals_reached=int(raw.get("min_goals_reached", 1)),
        max_elapsed_sec=None if max_elapsed is None else float(max_elapsed),
        require_no_abort=bool(raw.get("require_no_abort", True)),
    )


def _build_injections(raw_list: list[dict[str, Any]], source: str) -> list[Injection]:
    injections: list[Injection] = []
    for raw in raw_list:
        kind = str(raw.get("kind", ""))
        if kind not in SUPPORTED_INJECTIONS:
            raise ScenarioError(
                f"지원하지 않는 injection kind: {kind!r} "
                f"(가능: {sorted(SUPPORTED_INJECTIONS)}) ({source})"
            )
        injections.append(
            Injection(
                kind=kind,
                at_sec=float(raw.get("at_sec", 0.0)),
                duration_sec=float(raw.get("duration_sec", 0.0)),
                goal_index=int(raw.get("goal_index", 0)),
                battery_percent=float(raw.get("battery_percent", 0.0)),
            )
        )
    return injections
