import pytest

pytest.importorskip("rclpy")

from robo_claw_agent.skills.autonomous_skill.helpers import _extract_rag_location_candidates
from robo_claw_agent.skills.navigation_skill.core import _resolve_target_coordinates
from robo_claw_agent.skills.system_skill.rag import _normalize_location_metadata


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


def test_normalize_location_metadata_supports_ordered_pair_and_comma_coordinates():
    """순서쌍 (3, 4) 및 '3,4 좌표를 실험실로 기억해줘' 형태에서 x, y, 장소명을 정규화해야 한다."""
    # 1. 괄호 순서쌍
    meta1 = _normalize_location_metadata("실험실 위치 (3.0, 4.0)", {})
    assert meta1.get("location_name") == "실험실"
    assert meta1.get("x") == pytest.approx(3.0)
    assert meta1.get("y") == pytest.approx(4.0)
    assert meta1.get("type") == "location"

    # 2. '3,4 좌표를 실험실로 기억해줘'
    meta2 = _normalize_location_metadata("3.5, 4.2 좌표를 실험실로 기억해줘", {})
    assert meta2.get("location_name") == "실험실"
    assert meta2.get("x") == pytest.approx(3.5)
    assert meta2.get("y") == pytest.approx(4.2)
    assert meta2.get("type") == "location"

    # 3. 음수 좌표 포함 순서쌍
    meta3 = _normalize_location_metadata("회의실 (-1.5, 2.8)", {})
    assert meta3.get("location_name") == "회의실"
    assert meta3.get("x") == pytest.approx(-1.5)
    assert meta3.get("y") == pytest.approx(2.8)


def test_virtual_obstacle_and_scan_room_excluded_from_navigation_targets():
    """virtual_obstacle이나 scan_room 로그는 목적지 장소로 선택되지 않아야 한다."""
    entries = [
        {
            "id": "obs-1",
            "text": "가상 장애물 등록: '의자' — 위치 x=1.00, y=1.00 (감지 방법: vlm)",
            "metadata": {
                "type": "virtual_obstacle",
                "label": "의자",
                "x": 1.0,
                "y": 1.0,
                "source": "mark_virtual_obstacle",
            },
        },
        {
            "id": "scan-1",
            "text": " [위치 x=5.0 y=5.0 frame=map] 방 스캔 결과: 의자(거리=1.5m)",
            "metadata": {
                "type": "scan_room",
                "detected_objects": ["의자"],
                "source": "scan_room",
            },
        },
    ]
    memory = FakeMemory(entries=entries)

    # 의자로 이동하려 할 때 장애물/스캔로그 좌표를 목적지로 채택해선 안 된다
    result = _resolve_target_coordinates(memory, "의자")
    assert result is None


def test_extract_rag_location_candidates_filters_observations_and_ghost_waypoints():
    """자율 정리 후보 추출에서 관찰/장애물 로그를 제외하고, 이름 없는 유령 목적지(RAG위치1)를 만들지 않아야 한다."""
    entries = [
        {
            "id": "obs-1",
            "text": "가상 장애물 등록: '상자' — 위치 x=1.00, y=1.00",
            "metadata": {"type": "virtual_obstacle", "x": 1.0, "y": 1.0},
        },
        {
            "id": "scan-1",
            "text": " [위치 x=2.0 y=2.0 frame=map] 방 스캔 결과: 테이블",
            "metadata": {"type": "scan_room", "x": 2.0, "y": 2.0},
        },
        {
            "id": "odom-1",
            "text": " [위치 x=3.0 y=3.0 frame=odom] 장면 분석: 거실",
            "metadata": {"type": "scene_observation"},
        },
        {
            "id": "noname-1",
            "text": "알 수 없는 좌표 로그 (x=4.0, y=4.0)",
            "metadata": {"x": 4.0, "y": 4.0},
        },
        {
            "id": "valid-1",
            "text": "이동 성공 좌표: x=2.61, y=2.02 (주방 (2.61, 2.02))",
            "metadata": {
                "type": "navigated_coordinate",
                "location_name": "주방",
                "x": 2.61,
                "y": 2.02,
            },
        },
    ]
    memory = FakeMemory(entries=entries)

    candidates = _extract_rag_location_candidates(memory, limit=10)
    # 유효한 주방만 1건 추출되어야 함
    assert len(candidates) == 1
    assert candidates[0]["name"] == "주방"
    assert candidates[0]["position"]["x"] == pytest.approx(2.61)
    # 'RAG위치1' 같은 유령 목적지가 없어야 함
    assert not any("RAG위치" in c["name"] for c in candidates)
