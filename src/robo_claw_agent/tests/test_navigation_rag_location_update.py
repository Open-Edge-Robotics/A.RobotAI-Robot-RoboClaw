import importlib.util
from pathlib import Path
from types import SimpleNamespace

from robo_claw_agent.memory_manager import MemoryManager

_RAG_MODULE_PATH = (
    Path(__file__).parents[1]
    / "robo_claw_agent"
    / "skills"
    / "system_skill"
    / "rag.py"
)
_RAG_SPEC = importlib.util.spec_from_file_location("test_system_skill_rag", _RAG_MODULE_PATH)
assert _RAG_SPEC and _RAG_SPEC.loader
_RAG_MODULE = importlib.util.module_from_spec(_RAG_SPEC)
_RAG_SPEC.loader.exec_module(_RAG_MODULE)
RAGAddSkill = _RAG_MODULE.RAGAddSkill


class DummyEmbedder:
    def embed(self, text: str):
        if "창고" in text:
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]


def test_rag_add_location_updates_semantic_map(tmp_path):
    memory = MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )
    memory.add_object_location("창고", 2.0, 3.0)
    skill = RAGAddSkill()
    skill.node = SimpleNamespace(_memory=memory)

    result = skill.execute(
        {
            "text": "창고 위치는 (-1.0, -4.0)입니다.",
            "metadata": {"target_name": "창고", "x": -1.0, "y": -4.0},
        }
    )

    assert result["success"] is True
    assert result["semantic_updated"] is True
    assert memory.get_object_location("창고")["position"] == {"x": -1.0, "y": -4.0}
