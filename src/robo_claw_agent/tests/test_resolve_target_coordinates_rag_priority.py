import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.skills.navigation_skill.core import _resolve_target_coordinates


class FakeVectorStore:
    def __init__(self, entries):
        self.entries = list(entries)

    def list_entries(self):
        return list(self.entries)


class FakeMemory:
    def __init__(self, entries=None):
        self._objects = {}
        self._vector_store = FakeVectorStore(entries or [])

    def get_object_location(self, name: str):
        return self._objects.get(name)

    def get_all_objects(self):
        return list(self._objects.values())


def test_resolve_target_coordinates_prefers_location_over_latest_navigated_coordinate():
    """type=location(2.61, 2.02)이 오염된 최신 navigated_coordinate(0, 0)보다 우선해야 한다."""
    entries = [
        {
            "id": "loc-1",
            "text": "주방 위치: 현재 로봇 좌표는 map 기준 x=2.61, y=2.02, heading=117.0도입니다.",
            "metadata": {
                "location_name": "주방",
                "x": 2.61,
                "y": 2.02,
                "type": "location",
                "frame": "map",
            },
        },
        {
            "id": "nav-1",
            "text": "이동 성공 좌표: x=0.00, y=0.00 (주방 (0.00, 0.00))",
            "metadata": {
                "type": "navigated_coordinate",
                "kind": "place",
                "x": 0.0,
                "y": 0.0,
                "source": "navigate_to",
            },
        },
    ]
    memory = FakeMemory(entries=entries)

    result = _resolve_target_coordinates(memory, "주방")
    assert result is not None
    assert result["position"]["x"] == pytest.approx(2.61)
    assert result["position"]["y"] == pytest.approx(2.02)


def test_resolve_target_coordinates_ignores_scene_observation_odom_coordinates():
    """scene_observation이나 관찰 로그 내의 odom 좌표가 장소 좌표로 파싱되어서는 안 된다."""
    entries = [
        {
            "id": "scene-1",
            "text": " [위치 x=0.0 y=0.0 frame=odom] 장면 분석: 오른쪽에는 주방 또는 작업 공간이 있으며",
            "metadata": {
                "type": "scene_observation",
                "source": "analyze_scene",
            },
        }
    ]
    memory = FakeMemory(entries=entries)

    result = _resolve_target_coordinates(memory, "주방")
    assert result is None


def test_resolve_target_coordinates_falls_back_to_navigated_coordinate_if_no_location():
    """type=location이 전혀 없는 경우 이동 기록(navigated_coordinate)을 최후 순위로 허용한다."""
    entries = [
        {
            "id": "nav-1",
            "text": "이동 성공 좌표: x=5.15, y=-1.18 (키친 (5.15, -1.18))",
            "metadata": {
                "type": "navigated_coordinate",
                "kind": "place",
                "x": 5.15,
                "y": -1.18,
                "source": "navigate_to",
            },
        }
    ]
    memory = FakeMemory(entries=entries)

    result = _resolve_target_coordinates(memory, "키친")
    assert result is not None
    assert result["position"]["x"] == pytest.approx(5.15)
    assert result["position"]["y"] == pytest.approx(-1.18)
