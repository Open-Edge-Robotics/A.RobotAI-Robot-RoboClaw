import pytest

pytest.importorskip("rclpy")

"""번호 목적지 별칭 관련 단위 테스트"""

from robo_claw_agent.memory_manager import MemoryManager
from robo_claw_agent.skills.navigation_skill.core import _resolve_target_coordinates


def test_memory_manager_stores_numeric_aliases(tmp_path):
    memory = MemoryManager(storage_path=str(tmp_path / "mem.json"))

    memory.add_object_location(
        "Hallway End",
        1.2,
        -0.3,
        metadata={"source": "annotate_map", "selection_index": 1},
        aliases=["1", "Place 1", "Location 1", "Point 1"],
    )

    assert memory.get_object_location("1")["position"] == {"x": 1.2, "y": -0.3}
    assert memory.get_object_location("Place 1")["metadata"]["alias_of"] == "Hallway End"


def test_resolve_target_coordinates_supports_numbered_names(tmp_path):
    memory = MemoryManager(storage_path=str(tmp_path / "mem.json"))
    memory.add_object_location(
        "Open Space",
        2.5,
        4.0,
        aliases=["2", "Place 2", "Location 2", "Point 2"],
    )

    for target_name in ["2", "Place 2", "Location 2", "Point 2"]:
        result = _resolve_target_coordinates(memory, target_name)
        assert result is not None
        assert result["position"] == {"x": 2.5, "y": 4.0}
