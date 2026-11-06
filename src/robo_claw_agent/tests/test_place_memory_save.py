"""장소 기억 저장 경로 회귀 테스트 — 실제 로봇에서 실패한 문형을 고정한다.

배경: 사용자는 "현재 좌표를 확인해서 안방으로 기억해줘"처럼 요청했고 LLM은
``rag_add(text=...)``만 호출했다. 기존 이름 추출기는 ``…의 위치``, ``위치는 X입니다``,
``X로 기억``, ``X (…)`` 네 문형만 알아서, 실제 발화(``현재 위치(x,y)는 안방입니다``,
``안방의 좌표는 x: …``, ``데스크: x=…``)에서는 이름을 못 뽑았고, 그 결과 장소로
승격되지 않은 채 메타데이터 없는 일반 지식으로 저장돼 조용히 유실됐다.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from robo_claw_agent.memory_manager import MemoryManager
from robo_claw_agent.memory_manager.semantic import _location_name_from_text

_RAG_MODULE_PATH = (
    Path(__file__).parents[1] / "robo_claw_agent" / "skills" / "system_skill" / "rag.py"
)
_RAG_SPEC = importlib.util.spec_from_file_location("test_system_skill_rag", _RAG_MODULE_PATH)
assert _RAG_SPEC and _RAG_SPEC.loader
_RAG_MODULE = importlib.util.module_from_spec(_RAG_SPEC)
_RAG_SPEC.loader.exec_module(_RAG_MODULE)
RAGAddSkill = _RAG_MODULE.RAGAddSkill
_normalize_location_metadata = _RAG_MODULE._normalize_location_metadata

pytestmark = pytest.mark.unit


class DummyEmbedder:
    def embed(self, text: str):
        return [1.0, 0.0, 0.0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 실기(cloid2/former)에서 실제로 실패한 문형
        ("현재 위치(-0.46, 1.87)는 신발장입니다.", "신발장"),
        ("현재 위치(x: 6.37, y: -1.99)는 안방입니다.", "안방"),
        ("현재 좌표 (x: 6.46, y: -1.9)는 안방입니다.", "안방"),
        ("안방의 좌표는 x: 6.46, y: -1.9 입니다.", "안방"),
        ("데스크: x=6.43, y=-3.71", "데스크"),
        ("충전대: x=-0.78, y=-0.28", "충전대"),
        ("거실 좌표: x=2.93, y=-0.38", "거실"),
        # 기존 문형은 그대로 유지
        ("안방의 위치는 x: 6.55, y: -1.69 입니다.", "안방"),
        ("현재 위치를 '데스크'로 기억합니다.", "데스크"),
        ("안방 위치: x=3.0, y=1.0", "안방"),
    ],
)
def test_location_name_from_text_covers_real_utterances(text, expected):
    assert _location_name_from_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "현재 위치: x=1.0, y=2.0",
        "현재 좌표: x=1.0, y=2.0",
        "여기 위치 x=0.5 y=0.5",
        "이동 성공 좌표: x=4.00, y=1.00",
        "이동 실패 좌표: x=4.00, y=1.00",
        "사용자가 '신발 정리해줘'라고 요청하면 '신발장' 위치로 이동하여 작업을 수행해야 함.",
    ],
)
def test_location_name_from_text_rejects_non_place_names(text):
    assert _location_name_from_text(text) == ""


@pytest.mark.parametrize(
    ("text", "name", "xy"),
    [
        ("현재 위치(x: 6.37, y: -1.99)는 안방입니다.", "안방", (6.37, -1.99)),
        ("안방의 좌표는 x: 6.46, y: -1.9 입니다.", "안방", (6.46, -1.9)),
        ("데스크: x=6.43, y=-3.71", "데스크", (6.43, -3.71)),
    ],
)
def test_normalize_promotes_real_utterances_to_location(text, name, xy):
    meta = _normalize_location_metadata(text, {})

    assert meta["type"] == "location"
    assert meta["location_name"] == name
    assert (meta["x"], meta["y"]) == xy


def _make_memory(tmp_path):
    return MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.1,
    )


def _make_skill(memory, pose=None):
    skill = RAGAddSkill()
    skill.node = SimpleNamespace(_memory=memory)
    skill.get_map_pose = lambda: pose
    return skill


def test_rag_add_text_only_place_request_is_saved_as_location(tmp_path):
    """metadata 없이 text 만 와도 장소로 저장되어야 한다(실기 실패 케이스)."""
    memory = _make_memory(tmp_path)
    skill = _make_skill(memory, pose={"x": 6.46, "y": -1.9, "frame": "map"})

    result = skill.execute({"text": "현재 좌표 (x: 6.46, y: -1.9)는 안방입니다."})

    assert result["success"] is True
    assert result["saved_as"] == "location"
    saved = memory.get_object_location("안방")
    assert saved is not None
    assert saved["position"] == {"x": 6.46, "y": -1.9}
    assert memory.find_location_entries({"안방"})[0]["metadata"]["type"] == "location"


def test_rag_add_fills_coordinates_from_current_pose_for_name_only_text(tmp_path):
    """이름만 있고 좌표가 없으면 현재 pose 로 좌표를 채운다(프레임도 함께 기록)."""
    memory = _make_memory(tmp_path)
    skill = _make_skill(memory, pose={"x": 1.25, "y": 2.5, "frame": "map"})

    result = skill.execute({"text": "현재 위치를 '신발장'으로 기억합니다."})

    assert result["success"] is True
    assert result["saved_as"] == "location"
    saved = memory.get_object_location("신발장")
    assert saved["position"] == {"x": 1.25, "y": 2.5}
    assert saved["metadata"]["frame_id"] == "map"


def test_rag_add_place_request_without_coordinates_fails_loudly(tmp_path):
    """장소 의도인데 좌표를 확보하지 못하면 일반 지식으로 조용히 저장하지 않는다."""
    memory = _make_memory(tmp_path)
    skill = _make_skill(memory, pose=None)

    result = skill.execute({"text": "현재 위치를 '충전대'로 기억해줘"})

    assert result["success"] is False
    assert result["failure_reason"] == "no_coordinates"
    assert "충전대" in result["message"]
    assert memory.get_object_location("충전대") is None
    assert memory.list_knowledge_entries() == []


def test_rag_add_plain_knowledge_is_unchanged(tmp_path):
    """장소 의도가 아닌 일반 지식은 기존처럼 벡터 지식으로 저장한다."""
    memory = _make_memory(tmp_path)
    skill = _make_skill(memory, pose=None)

    result = skill.execute({"text": "사용자는 매주 화요일에 분리수거를 요청한다."})

    assert result["success"] is True
    assert result["saved_as"] == "knowledge"
