"""list_topics 스킬 테스트.

``system_skill/__init__.py`` 가 ROS2 모듈을 import 하므로, datetime 테스트와 동일하게
ros.py 를 직접 로드해 rclpy 없이 검증한다.
"""

import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__),
    "..",
    "robo_claw_agent",
    "skills",
    "system_skill",
    "ros.py",
)

_spec = importlib.util.spec_from_file_location("_ros_skill_under_test", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None, _MODULE_PATH
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ListTopicsSkill = _mod.ListTopicsSkill


class _FakeNode:
    """``get_topic_names_and_types`` 만 제공하는 노드 스텁."""

    def __init__(self, responses=None, error=None) -> None:
        # responses: 호출 순서대로 반환할 목록. 마지막 항목은 반복 사용한다.
        self._responses = list(responses) if responses else [[]]
        self._error = error
        self.calls = 0

    def get_topic_names_and_types(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return list(self._responses[0])


def _make_skill(responses=None, error=None, *, with_node=True):
    skill = ListTopicsSkill()
    if with_node:
        skill.set_node(_FakeNode(responses, error))
    return skill


# ── 메타데이터 계약 ────────────────────────────────────────────────────


def test_metadata_contract():
    skill = ListTopicsSkill()

    assert skill.name == "list_topics"
    assert skill.risk_level == "read"
    assert skill.answer_mode == "informational"
    assert skill.input_schema["additionalProperties"] is False
    assert "timeout_sec" in skill.input_schema["properties"]
    assert "include_hidden" in skill.input_schema["properties"]
    assert skill.input_schema["properties"]["category"]["enum"] == ["all", "camera"]
    assert "토픽" in skill.description


# ── 정상 경로 ──────────────────────────────────────────────────────────


def test_lists_topics_with_types():
    skill = _make_skill(
        [
            [
                ("/cmd_vel", ["geometry_msgs/msg/Twist"]),
                ("/scan", ["sensor_msgs/msg/LaserScan"]),
                ("/odom", ["nav_msgs/msg/Odometry"]),
            ]
        ]
    )
    result = skill.execute({"timeout_sec": 0.0})

    assert result["success"] is True
    assert "3개" in result["message"]
    assert "/cmd_vel" in result["message"]
    assert result["topic_count"] == 3
    assert result["topic_names"] == ["/cmd_vel", "/scan", "/odom"]
    assert result["topics"][0] == {"name": "/cmd_vel", "type": "geometry_msgs/msg/Twist"}
    # 사람이 읽는 텍스트만 message 에 담는다.
    assert "{" not in result["message"]


def test_camera_category_returns_only_camera_topics_with_types():
    skill = _make_skill(
        [
            [
                (
                    "/rgb_cam_head_front_left/image_raw/compressed",
                    ["sensor_msgs/msg/CompressedImage"],
                ),
                ("/depth_cam_head/color/camera_info", ["sensor_msgs/msg/CameraInfo"]),
                ("/camera_front/camera_front/depth/image_rect_raw", ["sensor_msgs/msg/Image"]),
                ("/cmd_vel", ["geometry_msgs/msg/Twist"]),
                ("/amr/micom_manager/battery_status", ["std_msgs/msg/UInt8"]),
            ]
        ]
    )

    result = skill.execute({"category": "camera", "timeout_sec": 0.0})

    assert result["topic_count"] == 3
    assert result["topic_names"] == [
        "/rgb_cam_head_front_left/image_raw/compressed",
        "/depth_cam_head/color/camera_info",
        "/camera_front/camera_front/depth/image_rect_raw",
    ]
    assert result["topics"][0]["type"] == "sensor_msgs/msg/CompressedImage"


def test_message_preview_is_truncated_but_result_keeps_all():
    pairs = [(f"/topic_{index}", ["std_msgs/msg/String"]) for index in range(20)]
    skill = _make_skill([pairs])
    result = skill.execute({"timeout_sec": 0.0})

    assert result["topic_count"] == 20
    assert len(result["topics"]) == 20
    assert "외 8개" in result["message"]
    assert result["message"].count("/topic_") < 20


def test_hidden_topics_are_excluded_by_default():
    skill = _make_skill(
        [
            [
                ("/cmd_vel", ["geometry_msgs/msg/Twist"]),
                ("/_ros2cli_1234", ["std_msgs/msg/String"]),
                ("/foo/_internal", ["std_msgs/msg/String"]),
            ]
        ]
    )
    result = skill.execute({"timeout_sec": 0.0})

    assert result["topic_names"] == ["/cmd_vel"]


def test_hidden_topics_included_when_requested():
    skill = _make_skill(
        [
            [
                ("/cmd_vel", ["geometry_msgs/msg/Twist"]),
                ("/_ros2cli_1234", ["std_msgs/msg/String"]),
            ]
        ]
    )
    result = skill.execute({"include_hidden": True, "timeout_sec": 0.0})

    assert result["topic_count"] == 2


def test_empty_graph_reports_no_topics():
    skill = _make_skill([[]])
    result = skill.execute({"timeout_sec": 0.0})

    assert result["success"] is True
    assert result["topic_count"] == 0
    assert "없습니다" in result["message"]


def test_empty_graph_is_retried_until_topics_appear(monkeypatch):
    """디스커버리 직후 빈 그래프는 즉시 실패로 보지 않고 timeout 까지 재탐색한다."""
    monkeypatch.setattr(_mod, "_TOPIC_POLL_INTERVAL_SEC", 0.0)
    skill = _make_skill(
        [
            [],
            [("/cmd_vel", ["geometry_msgs/msg/Twist"])],
        ]
    )
    result = skill.execute({"timeout_sec": 1.0})

    assert result["success"] is True
    assert result["topic_names"] == ["/cmd_vel"]
    assert skill.node.calls == 2


# ── 실패 경로 ──────────────────────────────────────────────────────────


def test_graph_api_failure_is_reported_as_failure():
    skill = _make_skill(error=RuntimeError("graph unavailable"))
    result = skill.execute({"timeout_sec": 0.0})

    assert result["success"] is False
    assert result["failure_reason"] == "graph_unavailable"


def test_missing_node_fails_closed():
    skill = _make_skill(with_node=False)
    result = skill.execute({})

    assert result["success"] is False


def test_missing_type_name_does_not_crash():
    skill = _make_skill([[("/weird", [])], [("/weird", [])]])
    result = skill.execute({"timeout_sec": 0.0})

    assert result["success"] is True
    assert result["topics"] == [{"name": "/weird", "type": ""}]
