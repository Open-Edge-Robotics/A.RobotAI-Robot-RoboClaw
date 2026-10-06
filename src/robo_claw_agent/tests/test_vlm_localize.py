"""VLM 오픈어휘 물체 로컬라이즈(vlm_localize) 단위 테스트.

YOLO(COCO 80클래스)가 못 잡는 물체(물통 등)를 VLM 폴백으로 로컬라이즈하는
경로를 검증한다. ROS 노드 의존은 없도록 fake skill로 격리한다.
"""

from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.unit

from robo_claw_agent.skills.perception_skill import vlm_localize
from robo_claw_agent.skills.perception_skill.vlm_localize import (
    _norm_coord,
    _observe_target_vlm,
    _use_vlm_fallback,
    _vlm_normalized_center,
    _vlm_object_center_px,
)


class _FakeLLM:
    def __init__(self, text: str):
        self.text = text

    def analyze_image(self, prompt: str, image_base64: str) -> str:
        return self.text


class _FakeSkill:
    def __init__(self, llm=None, image=None):
        self.node = SimpleNamespace(_llm=llm, _map_frame="map")
        self._image = image

    def get_opencv_image(self, params, timeout_sec=10.0):
        return (self._image, "/camera", False)


def test_norm_coord_accepts_in_range():
    assert _norm_coord(0.5) == pytest.approx(0.5)
    assert _norm_coord(0.0) is None  # 경계는 픽셀 좌표 오인 방지를 위해 폐기
    assert _norm_coord(1.0) is None
    assert _norm_coord(None) is None
    assert _norm_coord("0.33") == pytest.approx(0.33)


def test_norm_coord_rejects_out_of_range_and_bad_value():
    assert _norm_coord(1.5) is None
    assert _norm_coord(-0.1) is None
    assert _norm_coord("abc") is None


def test_vlm_normalized_center_parses_object():
    llm = _FakeLLM('OBJECTS: [{"name": "물통", "x_rel": 0.5, "y_rel": 0.4}]')
    skill = _FakeSkill(llm)
    assert _vlm_normalized_center(skill, "물통", "dummy_b64") == (0.5, 0.4, "물통")


def test_vlm_normalized_center_empty_when_no_llm():
    skill = _FakeSkill(llm=None)
    assert _vlm_normalized_center(skill, "물통", "dummy_b64") is None


def test_vlm_normalized_center_empty_when_not_found():
    llm = _FakeLLM("OBJECTS: []")
    skill = _FakeSkill(llm)
    assert _vlm_normalized_center(skill, "물통", "dummy_b64") is None


def test_vlm_object_center_px_converts_to_pixels():
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    llm = _FakeLLM('OBJECTS: [{"name": "물통", "x_rel": 0.5, "y_rel": 0.5}]')
    skill = _FakeSkill(llm, image)
    cx, cy, name = _vlm_object_center_px(skill, {"target_object": "물통"}, "물통")
    assert cx == pytest.approx(319.5, abs=1.0)
    assert cy == pytest.approx(239.5, abs=1.0)
    assert name == "물통"


def test_vlm_object_center_px_none_without_llm():
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    skill = _FakeSkill(llm=None, image=image)
    assert _vlm_object_center_px(skill, {"target_object": "물통"}, "물통") is None


def test_use_vlm_fallback_true_for_non_coco_bucket():
    skill = _FakeSkill(None)
    assert _use_vlm_fallback(skill, {"target_object": "물통"}, "물통") is True


def test_use_vlm_fallback_false_for_coco_cup_by_default():
    skill = _FakeSkill(None)
    assert _use_vlm_fallback(skill, {"target_object": "컵"}, "컵") is False


def test_use_vlm_fallback_force_enables_coco_target():
    skill = _FakeSkill(None)
    params = {"target_object": "컵", "force_vlm_fallback": True}
    assert _use_vlm_fallback(skill, params, "컵") is True


def test_use_vlm_fallback_disabled_by_flag():
    skill = _FakeSkill(None)
    params = {"target_object": "물통", "use_vlm_fallback": False}
    assert _use_vlm_fallback(skill, params, "물통") is False


def test_observe_target_vlm_success_builds_3d(monkeypatch):
    """VLM 중심 → depth → base 3D 좌표까지 이어지는 성공 경로."""
    skill = _FakeSkill(_FakeLLM("dummy"))

    monkeypatch.setattr(
        vlm_localize,
        "_vlm_object_center_px",
        lambda skill, params, target: (319.5, 239.5, "bucket"),
    )
    depth_frame = SimpleNamespace(
        depth_img=np.zeros((480, 640), dtype=np.uint16),
        encoding="16UC1",
        frame_id="head_camera_link",
        stamp=SimpleNamespace(sec=0, nanosec=0),
        camera_model=None,
    )
    monkeypatch.setattr(vlm_localize, "_fetch_depth_frame", lambda skill, params: depth_frame)
    monkeypatch.setattr(
        "robo_claw_agent.skills.perception_skill.core._depth_point_for_pixel",
        lambda skill, depth_frame, params, cx, cy: ((1.0, 2.0, 0.1), 0.5),
    )

    runtime = SimpleNamespace(config=SimpleNamespace(base_frame="base_link"))
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.core._get_runtime", lambda node: runtime
    )
    monkeypatch.setattr(
        "robo_claw_agent.manipulation_runtime.pose_goal_from_params",
        lambda params, default_frame: SimpleNamespace(
            position={"x": 0.0, "y": 0.0, "z": 0.0}
        ),
    )
    monkeypatch.setattr(
        "robo_claw_agent.skills.manipulation_skill.core._transform_pose_goal",
        lambda node, pose, frame: SimpleNamespace(
            position={"x": 0.5, "y": 0.6, "z": 0.2}
        ),
    )

    result = _observe_target_vlm(skill, {"target_object": "물통"})
    assert result["success"] is True
    assert result["class_name"] == "bucket"
    assert result["object_base_xyz"] == {"x": 0.5, "y": 0.6, "z": 0.2}
    assert result["target_pose"]["position"]["x"] == pytest.approx(1.0)


def test_observe_target_vlm_fails_cleanly_without_center(monkeypatch):
    skill = _FakeSkill(_FakeLLM("dummy"))
    monkeypatch.setattr(
        vlm_localize, "_vlm_object_center_px", lambda skill, params, target: None
    )
    result = _observe_target_vlm(skill, {"target_object": "물통"})
    assert result["success"] is False
    assert "중심을 얻지 못했습니다" in result["message"]
