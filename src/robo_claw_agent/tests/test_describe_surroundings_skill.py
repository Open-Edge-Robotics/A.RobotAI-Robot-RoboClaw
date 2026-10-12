import math
import sys
import threading
from types import ModuleType, SimpleNamespace
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


def test_four_way_observation_skips_image_when_rotation_fails(monkeypatch):
    import robo_claw_agent.skills.navigation_skill as navigation_skill
    import robo_claw_agent.skills.navigation_skill.orientation as orientation

    class FakeImage:
        def copy(self):
            return self

    class FakeLLM:
        def __init__(self):
            self.prompts = []

        def analyze_image(self, prompt, _image):
            self.prompts.append(prompt)
            return "정리 후보가 보이지 않습니다."

        def chat(self, _messages):
            return "다방향 관찰 요약입니다."

    fake_cv2 = ModuleType("cv2")
    fake_cv2.imwrite = lambda *_args: True
    fake_cv2.imencode = lambda *_args: (True, b"image")
    fake_cv2.resize = lambda image, _size: image
    fake_cv2.putText = lambda *_args: None
    fake_cv2.FONT_HERSHEY_SIMPLEX = 0
    fake_numpy = ModuleType("numpy")
    fake_numpy.vstack = lambda _images: FakeImage()
    fake_numpy.hstack = lambda _images: FakeImage()
    fake_numpy.zeros = lambda *_args, **_kwargs: FakeImage()
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setattr(navigation_skill, "_get_nav2_dependency_error", lambda: None)
    monkeypatch.setattr(
        orientation,
        "yaw_targets_from_offsets_rad",
        lambda _yaw, _offsets: [0.0, 1.0, 2.0, 3.0],
    )

    llm = FakeLLM()
    skill = DescribeSurroundingsSkill()
    skill.node = SimpleNamespace(_llm=llm)
    skill.get_map_pose = MagicMock(
        return_value={"x": 1.0, "y": 2.0, "yaw": 0.0, "heading_deg": 0.0, "frame": "map"}
    )
    rotations = iter(
        [
            (False, "Nav2 rotation failed", 90.0),
            (True, "ok", 0.0),
            (True, "ok", 0.0),
            (True, "ok", 0.0),
        ]
    )
    skill._rotate_to_target_yaw = MagicMock(side_effect=lambda *_args: next(rotations))
    image_calls = []
    skill.get_opencv_image = MagicMock(
        side_effect=lambda *_args, **_kwargs: (
            image_calls.append(True) or FakeImage(),
            "/camera",
            False,
        )
    )

    result = skill._capture_4way(
        {"observation_prompt": "폐기물과 명확한 빨래만 관찰하세요."},
        {"success": True, "pose": None},
    )

    assert result["success"] is True
    assert result["partial"] is True
    assert result["successful_directions"] == ["우측", "후방", "좌측"]
    assert result["failed_directions"] == ["정면"]
    assert len(image_calls) == 3
    assert "방향 회전 실패" in result["per_direction"]["정면"]
    assert all("명확한 빨래" in prompt for prompt in llm.prompts)


def test_four_way_observation_does_not_start_rotation_after_cancellation(monkeypatch):
    import robo_claw_agent.skills.navigation_skill as navigation_skill
    import robo_claw_agent.skills.navigation_skill.orientation as orientation

    fake_cv2 = ModuleType("cv2")
    fake_numpy = ModuleType("numpy")
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "numpy", fake_numpy)
    monkeypatch.setattr(navigation_skill, "_get_nav2_dependency_error", lambda: None)
    monkeypatch.setattr(
        orientation,
        "yaw_targets_from_offsets_rad",
        lambda _yaw, _offsets: [0.0, 1.0, 2.0, 3.0],
    )

    active_event = threading.Event()
    llm = SimpleNamespace(chat=MagicMock(return_value="요약"))
    skill = DescribeSurroundingsSkill(active_event=active_event)
    skill.node = SimpleNamespace(_llm=llm)
    skill.get_map_pose = MagicMock(
        return_value={"x": 1.0, "y": 2.0, "yaw": 0.0, "heading_deg": 0.0, "frame": "map"}
    )
    skill._rotate_to_target_yaw = MagicMock()
    skill.get_opencv_image = MagicMock()

    result = skill._capture_4way(
        {"return_to_start": True},
        {"success": True, "pose": None},
    )

    assert result["success"] is False
    assert result["cancelled"] is True
    assert result["partial"] is True
    skill._rotate_to_target_yaw.assert_not_called()
    skill.get_opencv_image.assert_not_called()


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
