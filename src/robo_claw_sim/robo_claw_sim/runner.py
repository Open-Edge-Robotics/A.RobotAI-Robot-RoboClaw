"""시나리오 실행 하네스.

시나리오 파일 하나를 받아 시뮬레이터 노드를 띄우고 목표를 순서대로 전송한 뒤,
``evaluate`` 로 통과/실패를 판정하고 결과 JSON 을 출력한다. Gazebo/GPU/실기 없이
``task sim-test`` 가 호출하는 진입점이다.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any

from .nav import GoalPose
from .scenario import Outcome, RunResult, Scenario, evaluate, load_scenario


def run_scenario(
    scenario_path: str | Path,
    *,
    timeout_sec: float | None = None,
    output_path: str | Path | None = None,
    spin_after_each_goal_sec: float = 0.0,
    backend: str | None = None,
) -> Outcome:
    """시나리오를 실제 ROS 그래프에서 실행한다."""
    import rclpy
    from nav2_msgs.action import NavigateToPose
    from rclpy.action import ActionClient
    from rclpy.executors import MultiThreadedExecutor

    from .nodes.sim_node import SimulationNode

    scenario = load_scenario(scenario_path)
    timeout = float(timeout_sec if timeout_sec is not None else scenario.timeout_sec)
    # 노드 파라미터 override 는 CLI 인자 경로로 넘겨야 rclpy 가 값을 그대로 보존한다
    # (parameter_overrides=... 로 넘기면 ParameterValue msg 가 기본값과 충돌한다).
    # 액션 서버의 goal 대기 시간은 시나리오 예산을 넘지 않게 묶는다. 넘기지 않으면
    # 서버가 기본 300초를 기다리는 동안 정리(destroy)가 막혀 하네스가 멈춘다.
    ros_args = ["--ros-args", "-p", f"navigate_timeout_sec:={timeout}"]
    if backend:
        ros_args += ["-p", f"backend:={backend}"]
    cli_args = ros_args

    rclpy.init()
    node = SimulationNode(scenario=scenario, cli_args=cli_args)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    client = ActionClient(node, NavigateToPose, "/navigate_to_pose")
    result = RunResult()
    started = time.monotonic()

    try:
        if not client.wait_for_server(timeout_sec=10.0):
            result.error = "navigate_to_pose 액션 서버를 찾을 수 없습니다"
        else:
            _execute_goals(
                node, client, scenario, result, started, timeout, spin_after_each_goal_sec
            )
    finally:
        result.elapsed_sec = time.monotonic() - started
        result.collisions = int(node.world.collisions)
        result.traveled_m = float(node.world.traveled_m)
        if result.elapsed_sec >= timeout:
            result.timed_out = True
        node.destroy_navigation()
        node.destroy_backend()
        executor.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()

    outcome = evaluate(scenario, result)
    if output_path is not None:
        payload = {
            "scenario": scenario.name,
            "source": str(scenario_path),
            **outcome.as_dict(),
        }
        Path(output_path).write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return outcome


def _execute_goals(
    node,  # noqa: ANN001
    client,  # noqa: ANN001
    scenario: Scenario,
    result: RunResult,
    started: float,
    timeout: float,
    spin_after_each_goal_sec: float,
) -> None:
    from action_msgs.msg import GoalStatus

    result.reached = [False] * len(scenario.goals)
    for index, goal in enumerate(scenario.goals):
        if time.monotonic() - started > timeout:
            result.timed_out = True
            return

        goal_handle = _send_goal(node, client, goal)
        if goal_handle is None or not goal_handle.accepted:
            # 서버가 거부했거나 응답이 없었다.
            result.rejected.append(index)
            continue

        deadline = min(started + timeout, time.monotonic() + scenario.timeout_sec)
        final = _wait_result(goal_handle, deadline)
        if final is None:
            result.timed_out = True
            return

        if final.status == GoalStatus.STATUS_SUCCEEDED:
            result.reached[index] = True
        else:
            result.aborted.append(index)

        if spin_after_each_goal_sec > 0.0:
            time.sleep(spin_after_each_goal_sec)


def _send_goal(node, client, goal: GoalPose):  # noqa: ANN001, ANN202
    from nav2_msgs.action import NavigateToPose

    request = NavigateToPose.Goal()
    request.pose.header.frame_id = "map"
    request.pose.header.stamp = node.get_clock().now().to_msg()
    request.pose.pose.position.x = goal.x
    request.pose.pose.position.y = goal.y
    request.pose.pose.orientation.w = 1.0
    send_future = client.send_goal_async(request)
    _wait_future(send_future, timeout_sec=10.0)
    return send_future.result() if send_future.done() else None


def _wait_future(future, timeout_sec: float) -> Any:  # noqa: ANN001
    deadline = time.monotonic() + timeout_sec
    while not future.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    return future.result() if future.done() else None


def _wait_result(goal_handle, deadline: float) -> Any:  # noqa: ANN001
    result_future = goal_handle.get_result_async()
    while not result_future.done() and time.monotonic() < deadline:
        time.sleep(0.02)
    return result_future.result() if result_future.done() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RoboClaw 시뮬레이터 시나리오 실행")
    parser.add_argument("scenario", help="시나리오 YAML 경로")
    parser.add_argument("--timeout", type=float, default=None, help="전체 제한 시간(초)")
    parser.add_argument("--output", default=None, help="결과 JSON 저장 경로")
    parser.add_argument(
        "--backend",
        default=None,
        choices=["kinematic", "mujoco"],
        help="물리 백엔드(기본: 노드 파라미터, kinematic)",
    )
    args = parser.parse_args(argv)

    outcome = run_scenario(
        args.scenario,
        timeout_sec=args.timeout,
        output_path=args.output,
        backend=args.backend,
    )
    print(json.dumps(outcome.as_dict(), indent=2, ensure_ascii=False))
    return 0 if outcome.passed else 1
