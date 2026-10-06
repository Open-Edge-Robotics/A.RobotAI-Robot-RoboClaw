import math
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from robo_claw_agent.skills.perception_skill.scan import DescribeSurroundingsSkill

pytestmark = pytest.mark.unit


def test_describe_surroundings_handles_none_lidar_distance():
    """라이다 측정값이 없거나 유효하지 않아 None이 반환될 때 TypeError 없이 정상 처리되어야 한다."""
    skill = DescribeSurroundingsSkill()
    skill.node = None
    skill.get_map_pose = MagicMock(return_value={"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"})

    dummy_scan = SimpleNamespace(
        angle_min=-math.pi,
        angle_max=math.pi,
        range_min=0.1,
        range_max=10.0,
        ranges=[float("inf")] * 360,
    )

    def fake_wait(msg_type, topic, **kwargs):
        if topic == "/scan":
            return dummy_scan
        return None

    skill.wait_for_message = MagicMock(side_effect=fake_wait)

    result = skill.execute({"use_vlm": False, "lidar_topic": "/scan"})

    assert result["success"] is True
    assert "surroundings" in result
    surroundings = result["surroundings"]
    for direction in ["front", "left", "right", "back"]:
        assert direction in surroundings
        assert surroundings[direction] == "측정 불가 (개방)"


def test_describe_surroundings_valid_distances():
    """정상적인 라이다 거리 측정값이 있을 때 레이블링 및 포맷팅이 올바르게 동작해야 한다."""
    skill = DescribeSurroundingsSkill()
    skill.node = None
    skill.get_map_pose = MagicMock(return_value={"x": 1.0, "y": 2.0, "yaw": 0.0, "frame": "map"})

    # 0.5m(장애물), 1.5m(가까움), 3.0m(개방)
    dummy_scan = SimpleNamespace(
        angle_min=-math.pi,
        angle_max=math.pi,
        range_min=0.1,
        range_max=10.0,
        ranges=[0.8] * 360,
    )

    def fake_wait(msg_type, topic, **kwargs):
        if topic == "/scan":
            return dummy_scan
        return None

    skill.wait_for_message = MagicMock(side_effect=fake_wait)

    result = skill.execute({"use_vlm": False, "lidar_topic": "/scan"})

    assert result["success"] is True
    assert "surroundings" in result
    surroundings = result["surroundings"]
    for direction in ["front", "left", "right", "back"]:
        assert direction in surroundings
        assert "0.8m — 장애물" in surroundings[direction]
