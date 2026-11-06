import math
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from robo_claw_agent.memory_manager import MemoryManager

pytestmark = pytest.mark.unit


def memory(tmp_path, namespace=""):
    return MemoryManager(
        storage_path=str(tmp_path / "memory.json"),
        local_rag_namespace=namespace,
        skill_learning_success_sample_rate=1.0,
    )


def test_places_and_aliases_persist_without_embedder(tmp_path):
    first = memory(tmp_path)
    first.add_object_location("거실", 1.79, -0.7, aliases=["Living Room"])
    restored = memory(tmp_path)
    assert restored.get_object_location("livingroom")["position"] == {"x": 1.79, "y": -0.7}
    assert restored.get_nearest_object(1.8, -0.7, 1)["name"] == "거실"
    assert restored.get_object_location("' OR 1=1 --") is None
    assert restored._vector_store.count() == 0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, "invalid", True, False])
def test_invalid_location_never_changes_existing_position(tmp_path, bad):
    mgr = memory(tmp_path)
    mgr.add_object_location("거실", 1, 2)
    with pytest.raises(ValueError):
        mgr.add_object_location("거실", bad, 0)
    assert mgr.get_object_location("거실")["position"] == {"x": 1.0, "y": 2.0}


def test_alias_update_is_atomic_and_removes_stale_aliases(tmp_path):
    mgr = memory(tmp_path)
    mgr.add_object_location("거실", 1, 2, aliases=["old"])
    mgr.add_object_location("거실", 3, 4, aliases=["new"])
    assert mgr.get_object_location("old") is None
    assert mgr.get_object_location("new")["position"]["x"] == 3


def test_namespaces_do_not_share_places_or_episodes(tmp_path):
    a, b = memory(tmp_path, "robot-a"), memory(tmp_path, "robot-b")
    a.add_object_location("거실", 1, 2)
    a.record_skill_episode("rotate", False, error="timeout")
    assert b.get_all_objects() == []
    assert b.get_skill_episodes() == []


def test_episodes_use_sql_without_embedding_and_can_filter(tmp_path):
    mgr = memory(tmp_path)
    mgr.record_skill_episode("rotate", False, error="timeout")
    mgr.record_skill_episode("rotate", True)
    mgr.record_skill_episode("navigate_to", False)
    restored = memory(tmp_path)
    assert len(restored.get_skill_episodes("rotate")) == 2
    assert len(restored.get_skill_episodes("rotate", success=False, limit=1)) == 1
    assert restored._vector_store.count() == 0


def test_mock_experience_cannot_be_stored_even_when_called_directly(tmp_path):
    mgr = memory(tmp_path)
    assert not mgr.record_skill_episode("rotate", False, mocked=True)
    assert mgr.get_skill_episodes() == []


def test_location_knowledge_uses_sql_and_deletion_removes_aliases(tmp_path):
    mgr = memory(tmp_path)
    assert mgr.add_knowledge(
        "이곳은 거실입니다.",
        {"type": "location", "location_name": "거실", "x": 1, "y": 2, "aliases": ["living"]},
    )
    assert mgr.get_object_location("living")["position"]["x"] == 1
    records = mgr.list_knowledge_entries(entry_type="location")
    assert len(records) == 1
    assert mgr._vector_store.count() == 0
    assert mgr.delete_knowledge(record_id=records[0]["id"])["deleted_count"] == 1
    assert mgr.get_object_location("living") is None


def test_clear_all_does_not_resurrect_legacy_files(tmp_path):
    mgr = memory(tmp_path)
    mgr.add_object_location("거실", 1, 2)
    mgr.record_skill_episode("rotate", False)
    mgr.clear_all()
    restored = memory(tmp_path)
    assert restored.get_all_objects() == []
    assert restored.get_skill_episodes() == []


def test_concurrent_episode_writes_are_not_lost(tmp_path):
    mgr = memory(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda i: mgr.record_skill_episode("rotate", False, instruction=str(i)), range(20)
            )
        )
    assert len(mgr.get_skill_episodes("rotate")) == 20


def test_conflicting_alias_rejects_entire_update(tmp_path):
    mgr = memory(tmp_path)
    mgr.add_object_location("거실", 1, 2, aliases=["living"])
    mgr.add_object_location("주방", 3, 4)
    with pytest.raises(ValueError):
        mgr.add_object_location("주방", 99, 99, aliases=["living"])
    assert mgr.get_object_location("주방")["position"] == {"x": 3, "y": 4}
    assert mgr.get_object_location("living")["position"] == {"x": 1, "y": 2}


def test_exact_sql_delete_does_not_require_vector_backend(tmp_path):
    mgr = memory(tmp_path)
    mgr.add_object_location("거실", 1, 2)
    record_id = mgr.list_knowledge_entries(entry_type="location")[0]["id"]
    mgr._vector_store = Mock()
    mgr._vector_store.delete_entries.side_effect = ConnectionError("offline")
    assert mgr.delete_knowledge(record_id=record_id)["success"]
    assert mgr.get_object_location("거실") is None
    mgr._vector_store.delete_entries.assert_not_called()


def test_legacy_structured_vector_data_is_not_authoritative(tmp_path):
    mgr = memory(tmp_path)
    mgr._vector_store.add(
        text="이곳은 거실입니다.",
        metadata={"type": "location", "location_name": "거실", "x": 99, "y": 99},
        vector=[1.0, 0.0],
        timestamp="old",
    )
    mgr._vector_store.add(
        text="[스킬 rotate 실패]",
        metadata={"type": "skill_episode", "skill_name": "rotate"},
        vector=[1.0, 0.0],
        timestamp="old",
    )
    assert mgr.get_object_location("거실") is None
    assert mgr.get_skill_episodes() == []
    assert mgr.list_knowledge_entries() == []


def test_navigation_never_uses_old_vector_coordinates_after_sql_miss(tmp_path):
    from robo_claw_agent.skills.navigation_skill.core import _resolve_target_coordinates

    mgr = memory(tmp_path)
    mgr._vector_store.add(
        text="거실의 위치는 x:99, y:99 입니다.",
        metadata={"type": "location", "location_name": "거실", "x": 99, "y": 99},
        vector=[1.0, 0.0],
        timestamp="old",
    )
    assert _resolve_target_coordinates(mgr, "거실") is None
    mgr.add_object_location("거실", 1, 2)
    assert _resolve_target_coordinates(mgr, "거실")["position"] == {"x": 1, "y": 2}


def test_sql_navigation_history_is_secondary_to_named_place(tmp_path):
    from robo_claw_agent.skills.navigation_skill.core import _resolve_target_coordinates

    mgr = memory(tmp_path)
    assert mgr.add_knowledge(
        "이동 성공", {"type": "navigated_coordinate", "target_name": "거실", "x": 3, "y": 4}
    )
    assert _resolve_target_coordinates(mgr, "거실")["position"] == {"x": 3, "y": 4}
    mgr.add_object_location("거실", 1, 2)
    assert _resolve_target_coordinates(mgr, "거실")["position"] == {"x": 1, "y": 2}
