"""장소와 객체(VLM/스캔 관측) 메모리 분리 회귀 테스트.

배경: `analyze_scene`/`find_object`/`scan_room`이 감지 객체를 `add_object_location`으로
저장했고, 이 API는 항상 `type=location`으로 기록했습니다. 그 결과 "person", "table"
같은 객체가 시맨틱 맵 장소 목록과 이동 목적지 해석에 섞여 순회 목록과 순찰 대상이
오염됐습니다(cloid1 장소 21건 중 15건이 객체 라벨).

객체는 조작(grasp/place)이 이름으로 조회해야 하므로 버리지 않고, `object_location`
타입으로 분리해 이동/순회 경로에서만 제외합니다.
"""

import pytest
from robo_claw_agent.memory_manager import MemoryManager
from robo_claw_agent.memory_manager.place_migration import demote_object_locations
from robo_claw_agent.memory_manager.structured import StructuredMemoryStore
from robo_claw_agent.skills.navigation_skill.core import _resolve_target_coordinates

pytestmark = pytest.mark.unit


class DummyEmbedder:
    def embed(self, text: str):
        return [1.0, 0.0, 0.0]


def _memory(tmp_path):
    return MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )


def test_vision_object_is_not_a_place(tmp_path):
    memory = _memory(tmp_path)

    memory.add_object_location(
        "person", 1.0, 2.0, {"source": "vlm", "kind": "object"}, as_object=True
    )
    memory.add_object_location("거실", 3.0, 4.0)

    assert [o["name"] for o in memory.get_all_objects()] == ["거실"]
    assert memory.get_object_location("person") is None
    # 조작 경로는 객체 관측을 계속 조회할 수 있어야 한다.
    object_info = memory.get_object_location("person", include_objects=True)
    assert object_info["position"] == {"x": 1.0, "y": 2.0}


def test_navigation_ignores_objects_but_resolves_places(tmp_path):
    memory = _memory(tmp_path)
    memory.add_object_location("table", 1.0, 1.0, {"kind": "object"}, as_object=True)
    memory.add_object_location("주방", 4.2, 1.6)

    assert _resolve_target_coordinates(memory, "table") is None
    assert _resolve_target_coordinates(memory, "주방")["position"] == {"x": 4.2, "y": 1.6}


def test_object_observation_is_upserted_by_name(tmp_path):
    store = StructuredMemoryStore(tmp_path / "places.structured.db")

    store.add(
        "person 1", {"type": "object_location", "location_name": "person", "x": 1.0, "y": 1.0}
    )
    store.add(
        "person 2", {"type": "object_location", "location_name": "person", "x": 2.0, "y": 2.0}
    )

    assert store.count() == 1
    assert store.location("person", include_objects=True)["position"] == {"x": 2.0, "y": 2.0}
    assert store.locations() == []


def test_demote_object_locations_moves_legacy_vision_rows(tmp_path):
    store = StructuredMemoryStore(tmp_path / "places.structured.db")
    # 과거 저장 형태: 객체인데 type=location
    store.add(
        "person의 위치는 x: 1.0, y: 1.0 입니다.",
        {"type": "location", "location_name": "person", "x": 1.0, "y": 1.0, "kind": "object"},
    )
    store.add(
        "거실의 위치는 x: 3.0, y: 4.0 입니다.",
        {"type": "location", "location_name": "거실", "x": 3.0, "y": 4.0},
    )

    assert demote_object_locations(store) == 1

    assert [
        entry["metadata"]["type"]
        for entry in store.records()
        if entry["metadata"]["location_name"] == "person"
    ] == ["object_location"]
    assert sorted(o["name"] for o in store.locations()) == ["거실"]
    assert store.location("person") is None
    assert store.location("person", include_objects=True) is not None


def test_demote_object_locations_is_idempotent(tmp_path):
    store = StructuredMemoryStore(tmp_path / "places.structured.db")
    store.add(
        "chair의 위치는 x: 1.0, y: 1.0 입니다.",
        {"type": "location", "location_name": "chair", "x": 1.0, "y": 1.0, "kind": "object"},
    )

    assert demote_object_locations(store) == 1
    assert demote_object_locations(store) == 0
