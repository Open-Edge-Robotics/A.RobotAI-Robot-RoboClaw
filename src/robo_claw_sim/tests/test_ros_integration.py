"""실제 ROS 그래프에서 시뮬레이터 폐루프를 검증한다.

Gazebo/GPU 없이 돈다. ``ros2`` 마커를 붙여 ROS 가 없는 환경에서는 skip 되지만,
ROS 가 있는 개발 PC/CI 에서는 ``task sim-test`` 로 실행된다.
"""

from __future__ import annotations

import pytest

rclpy = pytest.importorskip("rclpy")
pytest.importorskip("nav2_msgs")
pytest.importorskip("vision_msgs")

from robo_claw_sim.raycast import ScanConfig, cast_scan  # noqa: E402
from robo_claw_sim.scenario import build_scenario  # noqa: E402
from robo_claw_sim.world import BoxObstacle, Pose2D, Twist2D, World  # noqa: E402

pytestmark = pytest.mark.ros2

_SCENARIO = {
    "name": "ros_smoke",
    "world": {
        "width": 6.0,
        "height": 4.0,
        "robot": {"x": 1.0, "y": 2.0, "yaw": 0.0},
        "obstacles": [
            {"type": "box", "name": "south", "x": 3.0, "y": 0.3, "width": 6.0, "height": 0.2}
        ],
        "objects": [{"name": "cup", "class_id": "cup", "x": 2.0, "y": 2.0}],
    },
    "goals": [{"x": 2.5, "y": 2.0, "tolerance": 0.3}],
    "expect": {"min_goals_reached": 1, "max_collisions": 0},
}


def test_sim_node_publishes_core_interfaces() -> None:
    """RoboClaw 가 요구하는 최소 인터페이스가 실제로 발행되는지 확인한다."""
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.node import Node
    from robo_claw_sim.nodes.sim_node import SimulationNode
    from sensor_msgs.msg import JointState, LaserScan

    rclpy.init()
    sim = SimulationNode(scenario=build_scenario(_SCENARIO))
    probe = Node("sim_probe")
    received: dict[str, object] = {}

    probe.create_subscription(LaserScan, "/scan", lambda msg: received.setdefault("scan", msg), 10)
    probe.create_subscription(
        JointState, "/joint_states", lambda msg: received.setdefault("joints", msg), 10
    )

    executor = SingleThreadedExecutor()
    executor.add_node(sim)
    executor.add_node(probe)
    try:
        for _ in range(200):
            executor.spin_once(timeout_sec=0.05)
            if len(received) >= 2:
                break
    finally:
        executor.shutdown()
        sim.destroy_navigation()
        sim.destroy_node()
        probe.destroy_node()
        rclpy.try_shutdown()

    assert "scan" in received, "/scan 이 발행되지 않았습니다"
    assert "joints" in received, "/joint_states 가 발행되지 않았습니다"
    scan = received["scan"]
    assert len(scan.ranges) > 0
    assert scan.header.frame_id == "base_scan"


def test_scenario_reaches_goal_through_action_server(tmp_path) -> None:
    """NavigateToPose 액션으로 실제로 목표에 도달하고 시나리오가 통과해야 한다."""
    import yaml
    from robo_claw_sim.runner import run_scenario

    path = tmp_path / "ros_smoke.yaml"
    path.write_text(yaml.safe_dump(_SCENARIO, allow_unicode=True), encoding="utf-8")
    output = tmp_path / "outcome.json"

    outcome = run_scenario(path, timeout_sec=40.0, output_path=output)

    assert outcome.passed, outcome.reasons
    assert outcome.metrics["goals_reached"] == 1
    assert output.exists()


# --- 순수 모듈 보강(ROS 없이도 동작하지만 같은 파일에서 회귀를 함께 잡는다) ---


def test_world_blocks_forward_motion_into_wall() -> None:
    world = World(width=4.0, height=4.0, robot_radius=0.2)
    world.robot = Pose2D(x=1.0, y=2.0, yaw=0.0)
    world.add_obstacle(BoxObstacle(name="wall", x=2.0, y=2.0, width=0.1, height=4.0))

    for _ in range(50):
        world.apply_twist(Twist2D(vx=0.4), dt=0.1)

    assert world.robot.x < 2.0
    assert world.collisions > 0


def test_scan_sees_wall_ahead() -> None:
    world = World(width=4.0, height=4.0, resolution=0.05, robot_radius=0.2)
    world.robot = Pose2D(x=1.0, y=2.0, yaw=0.0)
    world.add_obstacle(BoxObstacle(name="wall", x=2.0, y=2.0, width=0.1, height=4.0))

    ranges = cast_scan(world, ScanConfig(num_beams=181))

    assert min(ranges) < 1.2
