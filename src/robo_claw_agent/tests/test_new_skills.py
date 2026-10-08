import pytest

pytest.importorskip("rclpy")

"""
manipulation / perception / vision / map / system 스킬 단위 테스트
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from robo_claw_agent.skills.map_skill import AnalyzeMapSkill
from robo_claw_agent.skills.perception_skill import DetectObjectSkill, GetDistanceSkill
from robo_claw_agent.skills.system_skill import GetStatusSkill, RosCommandSkill
from robo_claw_agent.skills.vision_skill import AnalyzeSceneSkill
from sensor_msgs.msg import Image


def _make_image(width: int, height: int, encoding: str = "bgr8"):
    msg = Image()
    msg.width = width
    msg.height = height
    msg.encoding = encoding
    channels = 3 if encoding in {"bgr8", "rgb8"} else 1
    msg.step = width * channels
    msg.data = np.zeros((height, width, channels), dtype=np.uint8).tobytes()
    return msg


def _make_dummy_node(**extra):
    node = SimpleNamespace(
        has_parameter=lambda _name: False,
        get_parameter=lambda _name: None,
    )
    for key, value in extra.items():
        setattr(node, key, value)
    return node


def _make_scan_for_direction(direction: str, distance: float = 1.25):
    ranges = [9.0] * 361
    angle_to_index = {
        "front": 180,
        "left": 270,
        "right": 90,
        "back": 0,
    }
    ranges[angle_to_index[direction]] = distance
    if direction == "back":
        ranges[-1] = distance
    return SimpleNamespace(
        ranges=ranges,
        angle_min=-np.pi,
        angle_max=np.pi,
        range_min=0.1,
        range_max=10.0,
    )


def _make_map_message():
    return SimpleNamespace(
        info=SimpleNamespace(
            resolution=1.0,
            width=3,
            height=3,
            origin=SimpleNamespace(position=SimpleNamespace(x=0.0, y=0.0)),
        ),
        data=[
            0,
            0,
            100,
            0,
            0,
            100,
            -1,
            0,
            0,
        ],
    )


class TestDetectObjectSkill:
    def test_basic_detection(self):
        skill = DetectObjectSkill()
        skill.wait_for_message = MagicMock(return_value=_make_image(640, 480, "rgb8"))
        result = skill.execute({"object_class": "cup"})
        assert result["success"] is True
        assert result["resolution"] == "640x480"
        # get_opencv_image normalizes raw ROS images to OpenCV BGR.
        assert result["encoding"] == "bgr8"

    def test_no_class_filter(self):
        skill = DetectObjectSkill()
        skill.wait_for_message = MagicMock(return_value=_make_image(320, 240, "bgr8"))
        result = skill.execute({})
        assert result["success"] is True
        assert "이미지 수신 성공" in result["message"]


class TestGetDistanceSkill:
    def test_valid_directions(self):
        skill = GetDistanceSkill()
        for direction in ["front", "left", "right", "back"]:
            assert skill.validate_params({"direction": direction}) is True
            skill.wait_for_message = MagicMock(
                return_value=_make_scan_for_direction(direction, distance=1.5)
            )
            result = skill.execute({"direction": direction})
            assert result["success"] is True
            assert result["direction"] == direction
            assert result["distance_m"] == pytest.approx(1.5)

    def test_invalid_direction(self):
        skill = GetDistanceSkill()
        assert skill.validate_params({"direction": "diagonal"}) is False

    def test_default_direction(self):
        skill = GetDistanceSkill()
        skill.wait_for_message = MagicMock(
            return_value=_make_scan_for_direction("front", distance=0.8)
        )
        result = skill.execute({})
        assert result["direction"] == "front"
        assert result["distance_m"] == pytest.approx(0.8)


def test_get_status_skill():
    skill = GetStatusSkill()
    assert skill.name == "get_status"
    assert skill.input_schema.get("properties", {}).get("include_image") is not None

    result = skill.execute({})
    assert result["success"] is True
    assert "로봇 상태" in result["message"]
    assert "status_details" in result
    # 기본값으로 대용량 카메라 이미지 base64는 포함되지 않아야 함
    assert "camera_scene_base64" not in result["status_details"]


def test_ros_command_skill():
    skill = RosCommandSkill()
    assert skill.name == "ros_command"

    assert skill.validate_params({"command": "ros2 topic list"}) is True
    assert skill.validate_params({"command": "ls -al"}) is False
    assert skill.validate_params({"command": "ros2 topic list; touch /tmp/unsafe"}) is False
    assert skill.validate_params({"command": ""}) is False
    assert skill.validate_params({"command": "  ros2 node list  "}) is True

    result = skill.execute({"command": "ros2 topic list", "timeout_sec": 2.0})
    assert "command" in result
    assert result["success"] in (True, False)


def test_analyze_scene_skill(monkeypatch):
    skill = AnalyzeSceneSkill()
    assert skill.name == "analyze_scene"

    dummy_memory = SimpleNamespace(add_object_location=MagicMock())
    dummy_llm = SimpleNamespace(
        analyze_image=MagicMock(
            return_value='장면 분석 결과\nOBJECTS: [{"name":"cup","x_rel":1.0,"y_rel":0.5}]'
        )
    )
    skill.set_node(_make_dummy_node(_llm=dummy_llm, _memory=dummy_memory))
    skill.get_map_pose = MagicMock(return_value={"x": 0.0, "y": 0.0, "yaw": 0.0, "frame": "map"})
    skill.wait_for_message = MagicMock(return_value=_make_image(16, 16, "bgr8"))
    skill._bridge = SimpleNamespace(
        imgmsg_to_cv2=MagicMock(return_value=np.zeros((16, 16, 3), dtype=np.uint8))
    )
    monkeypatch.setattr("time.time", lambda: 1234567890)

    result = skill.execute({"camera_topic": "/test/camera"})
    assert result["success"] is True
    assert "시각 분석 완료" in result["message"]
    assert result["detected_objects"] == ["cup"]
    dummy_memory.add_object_location.assert_called_once()
    dummy_llm.analyze_image.assert_called_once()


def test_analyze_map_skill():
    skill = AnalyzeMapSkill()
    assert skill.name == "analyze_map"
    skill.wait_for_message = MagicMock(return_value=_make_map_message())

    result = skill.execute({"map_topic": "/test/map", "target_x": 1.0, "target_y": 1.0})
    assert result["success"] is True
    assert "맵 분석 완료" in result["message"]
    assert result["details"]["target_status"] == "free"
