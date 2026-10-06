import threading
import time
from unittest.mock import MagicMock, patch

from robo_claw_agent.skills.navigation_skill.core import (
    _cancel_cmd_vel_rotation,
    _get_cmd_vel_topic,
    _rotate_via_cmd_vel,
    _rotate_via_navigation,
)


def test_get_cmd_vel_topic_default():
    assert _get_cmd_vel_topic(None) == "/cmd_vel"

    # 1. 2번 방식 (최우선): node._cmd_vel_topic / 파라미터 / _robot_limits_dict
    node_custom = MagicMock()
    node_custom._cmd_vel_topic = "/custom/cmd_vel"
    assert _get_cmd_vel_topic(node_custom) == "/custom/cmd_vel"

    node_limits = MagicMock()
    node_limits._cmd_vel_topic = None
    node_limits._robot_limits_dict = {"cmd_vel_topic": "/robot_a/cmd_vel"}
    assert _get_cmd_vel_topic(node_limits) == "/robot_a/cmd_vel"

    # 2. 3번 방식 (보험): ROS graph active topics 감지
    node_disc = MagicMock(spec=["get_topic_names_and_types"])
    node_disc._cmd_vel_topic = None
    node_disc._robot_limits_dict = {}
    node_disc.get_topic_names_and_types.return_value = [
        ("/my_robot/cmd_vel", ["geometry_msgs/msg/Twist"]),
        ("/sensor_data", ["sensor_msgs/msg/LaserScan"]),
    ]
    assert _get_cmd_vel_topic(node_disc) == "/my_robot/cmd_vel"


def test_rotate_via_cmd_vel_success():
    node = MagicMock()
    node.create_publisher = MagicMock()
    node._cmd_vel_topic = "/cmd_vel"

    poses = [{"yaw": 0.0}, {"yaw": 0.5}, {"yaw": 1.0}]
    call_count = 0

    def mock_get_pose():
        nonlocal call_count
        idx = min(call_count, len(poses) - 1)
        call_count += 1
        return poses[idx]

    node.get_map_pose = mock_get_pose
    mock_pub = MagicMock()
    node.create_publisher.return_value = mock_pub

    success, msg = _rotate_via_cmd_vel(node, target_yaw=1.0, timeout_sec=2.0)
    assert success is True
    assert "목표 방향 정렬 성공" in msg
    assert mock_pub.publish.called
    assert node.destroy_publisher.called


def test_cancel_cmd_vel_rotation_stops_worker_and_publishes_zero():
    node = MagicMock()
    node._cmd_vel_topic = "/cmd_vel"
    node.create_publisher = MagicMock()
    node.get_map_pose.return_value = {"yaw": 0.0}
    publisher = MagicMock()
    node.create_publisher.return_value = publisher
    result: list[tuple[bool, str]] = []

    worker = threading.Thread(
        target=lambda: result.append(
            _rotate_via_cmd_vel(node, target_yaw=1.0, timeout_sec=5.0)
        )
    )
    worker.start()
    for _ in range(20):
        if publisher.publish.called:
            break
        time.sleep(0.01)

    cancelled, message = _cancel_cmd_vel_rotation()
    worker.join(timeout=1.0)

    assert cancelled is True
    assert "취소" in message
    assert not worker.is_alive()
    assert result[0][0] is False
    assert publisher.publish.call_args.args[0].angular.z == 0.0


@patch("robo_claw_agent.skills.navigation_skill.core._wait_for_goal_result")
@patch("robo_claw_agent.skills.navigation_skill.core._send_navigation_goal")
def test_rotate_via_navigation_success(mock_send_goal, mock_wait_result):
    node = MagicMock()
    node.get_map_pose.return_value = {"x": 1.2, "y": 3.4, "yaw": 0.0, "frame": "map"}
    mock_send_goal.return_value = (True, "accepted", MagicMock())
    mock_wait_result.return_value = (True, "success")

    success, msg = _rotate_via_navigation(node, target_yaw=1.57, timeout_sec=5.0)
    assert success is True
    assert msg == "success"
    mock_send_goal.assert_called_once_with(node, 1.2, 3.4, "map", yaw=1.57)
