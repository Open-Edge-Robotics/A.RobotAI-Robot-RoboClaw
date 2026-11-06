import importlib.util
from pathlib import Path
from types import SimpleNamespace

from robo_claw_agent.memory_manager import MemoryManager

_RAG_MODULE_PATH = (
    Path(__file__).parents[1] / "robo_claw_agent" / "skills" / "system_skill" / "rag.py"
)
_RAG_SPEC = importlib.util.spec_from_file_location("test_system_skill_rag", _RAG_MODULE_PATH)
assert _RAG_SPEC and _RAG_SPEC.loader
_RAG_MODULE = importlib.util.module_from_spec(_RAG_SPEC)
_RAG_SPEC.loader.exec_module(_RAG_MODULE)
GetLocationSkill = _RAG_MODULE.GetLocationSkill


class DummyEmbedder:
    def embed(self, text: str):
        if "충전대" in text:
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]


def _make_skill(memory):
    skill = GetLocationSkill()
    skill.node = SimpleNamespace(_memory=memory)
    return skill


def test_get_location_from_semantic_map(tmp_path):
    memory = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    memory.add_object_location("충전대", -0.14, -0.1)
    skill = _make_skill(memory)

    result = skill.execute({"location_name": "충전대"})

    assert result["success"] is True
    assert result["position"] == {"x": -0.14, "y": -0.1}
    assert result["source"] == "sql"


def test_get_location_from_structured_knowledge(tmp_path):
    memory = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    # 지식 등록 API로 저장해도 구조화 위치는 SQL에서 정확히 조회합니다.
    memory.add_knowledge(
        "충전대의 위치는 x: -0.14, y: -0.1 입니다.",
        {"location_name": "충전대", "x": -0.14, "y": -0.1, "type": "location"},
    )
    skill = _make_skill(memory)

    result = skill.execute({"location_name": "충전대"})

    assert result["success"] is True
    assert result["position"] == {"x": -0.14, "y": -0.1}
    assert result["source"] == "sql"


def test_get_location_not_found(tmp_path):
    memory = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    skill = _make_skill(memory)

    result = skill.execute({"location_name": "없는장소"})

    assert result["success"] is True
    assert result["position"] is None


def test_get_location_requires_param(tmp_path):
    memory = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    skill = _make_skill(memory)

    assert skill.validate_params({}) is False
    assert skill.validate_params({"location_name": "충전대"}) is True
