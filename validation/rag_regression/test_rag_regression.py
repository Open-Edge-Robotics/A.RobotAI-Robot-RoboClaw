"""RAG 위치 지식 개선(1~5순위) 회귀 테스트 — 로봇 없이 실행 가능.

first-use-scenario-test-gemma4_31b.md 의 실패 케이스를 유발한 근본 원인들을
코드 레벨에서 재현·검증한다. ROS 런타임/실로봇 없이 순수 파이썬으로만 동작하도록
설계했다(임베더는 결정론적 더미, 벡터스토어는 in-memory Qdrant/로컬 JSON).

각 테스트는 개선 순위(P1~P5)와 시나리오 실패 번호(Fail 12/13/15/17/18/20)에 매핑된다.
"""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import robo_claw_agent
from robo_claw_agent.memory_manager import MemoryManager
import robo_claw_agent.vector_store.qdrant as _vector_store_qdrant
from robo_claw_agent.vector_store import QdrantVectorStore
from qdrant_client import QdrantClient


# ── system_skill/rag.py 를 직접 로드 ────────────────────────────────
# (패키지 import 는 status.py 의 ROS 의존성(sensor_msgs)을 끌어와 실패하므로,
#  ROS 비의존 모듈인 rag.py 만 파일 경로로 직접 로드한다.)
_RAG_PATH = (
    Path(robo_claw_agent.__file__).parent / "skills" / "system_skill" / "rag.py"
)
_SPEC = importlib.util.spec_from_file_location("rag_regression_rag", _RAG_PATH)
assert _SPEC and _SPEC.loader
_RAG = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_RAG)

RAGAddSkill = _RAG.RAGAddSkill
RAGSearchSkill = _RAG.RAGSearchSkill
IdentifyLocationSkill = _RAG.IdentifyLocationSkill


class DummyEmbedder:
    """키워드 기반 결정론적 임베더 (실제 임베딩 서버 불필요)."""

    def embed(self, text: str):
        if "창고" in text:
            return [1.0, 0.0, 0.0]
        if "충전대" in text:
            return [0.0, 1.0, 0.0]
        if "거실" in text:
            return [0.0, 0.0, 1.0]
        return [0.5, 0.5, 0.5]


def _make_memory(tmp_path, sample_rate=0.1):
    return MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=DummyEmbedder(),
        rag_score_threshold=0.0,
        skill_learning_success_sample_rate=sample_rate,
    )


def _episodes(memory):
    return [
        e
        for e in memory._vector_store.list_entries()
        if e.get("metadata", {}).get("type") == "skill_episode"
    ]


# ══════════════════════════════════════════════════════════════════
# P1 — 스킬 실행 로그가 사실 검색을 오염시키는 문제 (Fail 12/13/15/20)
# ══════════════════════════════════════════════════════════════════


def test_p1_query_skills_not_recorded_as_episode(tmp_path):
    """조회·자기참조 스킬(rag_search/rag_add/get_status 등)은 학습 에피소드로
    저장되지 않는다 → 좌표 없는 노이즈가 애초에 쌓이지 않음."""
    memory = _make_memory(tmp_path, sample_rate=1.0)
    # identify_location 포함: 읽기/조회성 스킬은 학습 기록 안 함
    for skill in ("rag_search", "rag_add", "get_status", "rag_list", "identify_location"):
        assert memory.record_skill_episode(skill, success=True) is False
        assert memory.record_skill_episode(skill, success=False, error="x") is False
    assert _episodes(memory) == []
    # 일반 스킬은 정상 기록됨 (대조군)
    assert memory.record_skill_episode("navigate_to", success=True) is True


def test_p1_search_excludes_skill_episode(tmp_path):
    """사용자 지식 검색(rag_search)은 skill_episode 를 제외한다 → 이전 검색
    기록이 실제 위치 항목을 밀어내지 못함."""
    client = QdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "p1", timeout_sec=5.0)
    store.add(
        text="충전대의 위치 좌표는 x: -5.26, y: -1.26입니다.",
        metadata={"type": "location", "location_name": "충전대"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    store.add(
        text="[스킬 rag_search 성공] 상황: '충전대 위치를 알려줘'.",
        metadata={"type": "skill_episode", "skill_name": "rag_search"},
        vector=[0.999, 0.045, 0.0],
        timestamp="2026-01-02T00:00:00",  # 더 최신이지만 제외되어야 함
    )
    results = store.search(
        query_vector=[1.0, 0.0, 0.0],
        top_k=5,
        score_threshold=0.1,
        filter_metadata={"type_exclude": ["skill_episode"]},
    )
    assert len(results) == 1
    assert results[0]["metadata"]["type"] == "location"


# ══════════════════════════════════════════════════════════════════
# P2 — 위치를 구조화 저장 + 이름 조회 견고화 (Fail 15/20 견고화)
# ══════════════════════════════════════════════════════════════════


def test_p1_skill_lesson_excluded_from_search(tmp_path):
    """자가학습 교훈(skill_lesson)은 사용자 위치 질의 결과에서 제외된다.
    (실기 프로브에서 교훈이 실제 type=location 사실보다 상위로 잡히던 문제)"""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)
    add.execute(
        {
            "text": "충전대의 위치 좌표는 x: 3.96, y: 0.83입니다.",
            "metadata": {"location_name": "충전대", "x": 3.96, "y": 0.83},
        }
    )
    memory.add_knowledge(
        "[스킬 교훈: identify_location] 충전대 관련 교훈 ...",
        {"type": "skill_lesson", "skill_name": "identify_location"},
    )

    search = RAGSearchSkill()
    search.node = SimpleNamespace(_memory=memory)
    res = search.execute({"query": "충전대"})
    assert res["success"] is True
    assert "교훈" not in res["message"]
    assert "'충전대'의 위치는 x: 3.96, y: 0.83" in res["message"]


def test_p2_name_lookup_normalized(tmp_path):
    """이름 조회는 대소문자·공백에 무관하게 매칭된다."""
    memory = _make_memory(tmp_path)
    memory.add_object_location("거실", 0.56, 0.75)
    memory.add_object_location("Kitchen", 4.14, 0.88)

    assert memory.get_object_location("거실")["position"] == {"x": 0.56, "y": 0.75}
    assert memory.get_object_location("거 실")["position"] == {"x": 0.56, "y": 0.75}
    assert memory.get_object_location("kitchen")["position"] == {"x": 4.14, "y": 0.88}
    assert memory.get_object_location("충전대") is None


def test_p2_rag_add_stamps_location_type(tmp_path):
    """이름+좌표 메타데이터는 type='location'으로 태깅되어 사실로 저장된다."""
    memory = _make_memory(tmp_path)
    skill = RAGAddSkill()
    skill.node = SimpleNamespace(_memory=memory)

    result = skill.execute(
        {
            "text": "충전대의 위치 좌표는 x: -5.26, y: -1.26입니다.",
            "metadata": {"location_name": "충전대", "x": -5.26, "y": -1.26},
        }
    )
    assert result["success"] is True
    entries = memory._vector_store.list_entries()
    assert len(entries) == 1
    assert entries[0]["metadata"]["type"] == "location"

    # 좌표 없는 일반 지식은 태깅하지 않음
    skill.execute({"text": "오늘은 맑음", "metadata": {"note": "weather"}})
    others = [e for e in memory._vector_store.list_entries() if "note" in e["metadata"]]
    assert others and "type" not in others[0]["metadata"]


# ══════════════════════════════════════════════════════════════════
# P3 — 사실 우선 랭킹: 최신 관찰이 위치 사실을 밀어내지 못함 (Fail 12/13 재발 방지)
# ══════════════════════════════════════════════════════════════════


def test_p3_stable_fact_outranks_recent_observation():
    """점수가 비슷하면 더 최신 관찰이라도 안정적 위치 사실이 상위에 온다."""
    client = QdrantClient(location=":memory:")
    store = QdrantVectorStore(client, "p3", timeout_sec=5.0)
    store.add(
        text="충전대의 위치 좌표는 x: -5.26, y: -1.26입니다.",
        metadata={"type": "location", "location_name": "충전대"},
        vector=[1.0, 0.0, 0.0],
        timestamp="2026-01-01T00:00:00",
    )
    store.add(
        text="충전대 근처에서 사람을 봤다",
        metadata={"type": "observation"},
        vector=[0.999, 0.045, 0.0],
        timestamp="2026-01-05T00:00:00",  # 더 최신
    )
    results = store.search(
        query_vector=[1.0, 0.0, 0.0], top_k=2, score_threshold=0.1
    )
    assert len(results) == 2
    assert results[0]["metadata"]["type"] == "location"


# ══════════════════════════════════════════════════════════════════
# P4 — 좌표→이름 역방향 조회 (Fail 17/18)
# ══════════════════════════════════════════════════════════════════


def test_p4_reverse_lookup_nearest(tmp_path):
    """좌표에서 가장 가까운 명명 위치를 반경 내에서 찾는다."""
    memory = _make_memory(tmp_path)
    memory.add_object_location("거실", 0.56, 0.75)
    memory.add_object_location("충전대", -5.26, -1.26, aliases=["charger"])

    near = memory.get_nearest_object(0.6, 0.7, radius_m=2.0)
    assert near is not None and near["name"] == "거실"

    assert memory.get_nearest_object(100.0, 100.0, radius_m=2.0) is None

    far = memory.get_nearest_object(-5.0, -1.0)  # 반경 미지정
    assert far is not None and far["name"] == "충전대"


def test_p4_identify_location_skill(tmp_path):
    """지정 좌표가 기억된 장소명으로 해석되는지 확인 (Fail 17/18 대응)."""
    memory = _make_memory(tmp_path)
    memory.add_object_location("충전대", -5.26, -1.26)

    skill = IdentifyLocationSkill()
    skill.node = SimpleNamespace(_memory=memory)

    hit = skill.execute({"x": -5.2, "y": -1.3, "radius_m": 2.0})
    assert hit["success"] is True and hit["location_name"] == "충전대"

    miss = skill.execute({"x": 50.0, "y": 50.0, "radius_m": 2.0})
    assert miss["success"] is True and miss["location_name"] is None


# ══════════════════════════════════════════════════════════════════
# P5 — 응답 포맷팅: 내부 스킬 로그 노출 제거 (Success 3/7/11 UX)
# ══════════════════════════════════════════════════════════════════


def test_p5_search_formats_location_from_metadata(tmp_path):
    """위치 결과는 metadata 좌표로 깔끔히 요약하고 내부 로그를 노출하지 않는다."""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)
    add.execute(
        {
            "text": "충전대의 위치 좌표는 x: -5.26, y: -1.26입니다.",
            "metadata": {"location_name": "충전대", "x": -5.26, "y": -1.26},
        }
    )

    search = RAGSearchSkill()
    search.node = SimpleNamespace(_memory=memory)
    result = search.execute({"query": "충전대"})

    assert result["success"] is True
    assert "'충전대'의 위치는 x: -5.26, y: -1.26" in result["message"]
    assert "[스킬" not in result["message"]


def test_p5_sanitize_strips_skill_log_prefix():
    """레거시 스킬 로그 접두어는 사용자 노출 전 제거된다."""
    sanitize = _RAG._sanitize_knowledge_text
    raw = "[스킬 rag_add 성공] 상황: '그 위치를 키친으로 기억해'. 파라미터: {...}"
    cleaned = sanitize(raw)
    assert not cleaned.startswith("[스킬")
    assert "상황:" in cleaned
    assert sanitize("키친 위치는 x: 4.14") == "키친 위치는 x: 4.14"


# ══════════════════════════════════════════════════════════════════
# 실기 결과(2026-08-07) 반영 — 추가 오염원/동기화 버그 회귀
# ══════════════════════════════════════════════════════════════════


def test_navigated_coordinate_excluded_from_search(tmp_path):
    """navigate_to가 남기는 'navigated_coordinate' 로그가 위치 질의에서 제외되어,
    큐레이팅된 type=location 사실이 상위에 온다 (실기 tc24/25/20 회귀)."""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)
    add.execute(
        {
            "text": "충전대의 위치 좌표는 x: 3.86, y: 0.83입니다.",
            "metadata": {"location_name": "충전대", "x": 3.86, "y": 0.83},
        }
    )
    # navigate_to가 매 이동마다 남기는 좌표 로그(오염원)
    memory.add_knowledge(
        "이동 성공 좌표: x=3.86, y=0.83 (충전대 (3.86, 0.83))",
        {"type": "navigated_coordinate", "kind": "place", "x": 3.86, "y": 0.83, "source": "navigate_to"},
    )

    search = RAGSearchSkill()
    search.node = SimpleNamespace(_memory=memory)
    result = search.execute({"query": "충전대"})

    assert result["success"] is True
    assert "이동 성공 좌표" not in result["message"]
    assert "'충전대'의 위치는 x: 3.86, y: 0.83" in result["message"]


def test_free_form_location_key_syncs_to_semantic_map(tmp_path):
    """자유 형식 저장(metadata 키가 location, 값이 영어 등)도 시맨틱 맵에 동기화되어
    좌표→이름 역방향 조회가 동작한다 (실기 tc10→tc17 identify_location 실패 회귀)."""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)
    # 실기에서 LLM이 실제로 저장한 형태: 키가 location, 값이 영어, heading 포함
    add.execute(
        {
            "text": "키친의 위치는 x: -5.23, y: -1.24이며, 방향은 97.1도입니다.",
            "metadata": {"location": "kitchen", "x": -5.23, "y": -1.24, "heading": 97.1},
        }
    )
    # 시맨틱 맵 동기화 + type=location 태깅
    assert memory.get_object_location("kitchen") is not None
    entries = memory._vector_store.list_entries()
    assert entries[0]["metadata"].get("type") == "location"

    # 0.17m 옆에서 역방향 조회 → 장소를 찾아야 함 (실기 tc17은 여기서 실패했었음)
    near = memory.get_nearest_object(-5.09, -1.14, radius_m=2.0)
    assert near is not None and near["name"] == "kitchen"


def test_identify_location_falls_back_to_rag_scan(tmp_path):
    """저장 metadata에 x/y가 없어 시맨틱 맵 동기화가 누락돼도(실기 20260807 tc16/17),
    identify_location이 RAG 위치 항목(텍스트의 x=/y=)을 스캔해 좌표→이름을 찾는다."""
    memory = _make_memory(tmp_path)
    # 시맨틱 맵 동기화가 안 되는 저장: 이름은 있으나 metadata에 x/y 없음(좌표는 텍스트에만)
    memory.add_knowledge("키친 위치: x=-5.36, y=-1.25", {"location": "키친"})
    # 전제 재현: 시맨틱 맵엔 키친이 없음
    assert memory.get_object_location("키친") is None
    assert memory.get_nearest_object(-5.15, -1.20, radius_m=2.0) is None

    skill = IdentifyLocationSkill()
    skill.node = SimpleNamespace(_memory=memory)
    hit = skill.execute({"x": -5.15, "y": -1.20, "radius_m": 2.0})
    assert hit["success"] is True
    assert hit["location_name"] == "키친"  # RAG 스캔 폴백으로 발견

    # 반경 밖이면 여전히 없음
    miss = skill.execute({"x": 50.0, "y": 50.0, "radius_m": 2.0})
    assert miss["success"] is True and miss["location_name"] is None


def test_identify_location_name_from_text_only(tmp_path):
    """이름이 metadata에 없고 텍스트에만 있어도(실기 스크린샷 '거실'), 역방향 조회가
    텍스트에서 이름을 파싱해 찾는다."""
    memory = _make_memory(tmp_path)
    # metadata에 이름 키 없음(heading만) + 좌표/이름 모두 텍스트에만
    memory.add_knowledge(
        "거실의 위치는 x: 0.66, y: 0.73이며, 방향은 -97.8도입니다.",
        {"heading_deg": -97.8},
    )
    assert memory.get_object_location("거실") is None  # 시맨틱 맵엔 없음

    skill = IdentifyLocationSkill()
    skill.node = SimpleNamespace(_memory=memory)
    hit = skill.execute({"x": 0.67, "y": 0.53, "radius_m": 2.0})  # 0.2m 옆
    assert hit["success"] is True
    assert hit["location_name"] == "거실"


def test_location_save_without_coords_filled_from_pose(tmp_path):
    """약한 LLM이 위치를 좌표 없이 저장해도(실기 예: {"location_name":"충전대"}),
    로봇 현재 pose로 좌표를 채워 시맨틱 맵 동기화·역방향 조회가 동작한다."""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)
    # 현재 pose를 (3.96, 0.83)으로 주입(테스트용)
    add.get_map_pose = lambda: {"x": 3.96, "y": 0.83, "frame": "map"}

    r = add.execute({"text": "현재 위치는 충전대입니다.", "metadata": {"location_name": "충전대"}})
    assert r["success"] is True

    ents = [e for e in memory._vector_store.list_entries() if e["metadata"].get("location_name") == "충전대"]
    assert ents and ents[0]["metadata"].get("x") == 3.96 and ents[0]["metadata"].get("y") == 0.83
    assert ents[0]["metadata"].get("type") == "location"
    # 시맨틱 맵 동기화 → 역방향 조회 동작
    assert memory.get_object_location("충전대") is not None
    near = memory.get_nearest_object(3.96, 0.83, radius_m=1.0)
    assert near is not None and near["name"] == "충전대"


def test_ollama_embed_query_asymmetric_instruction():
    """비대칭 임베딩: qwen3-embedding 쿼리에는 지시문을 부착하고(문서는 원문),
    비-qwen 임베더에는 원문을 그대로 임베딩한다. (검색 분리도 개선의 핵심)"""
    from robo_claw_agent.llm_bridge.ollama import OllamaBridge

    b = OllamaBridge(embedding_model="qwen3-embedding:0.6b")
    cap = {}
    b.embed = lambda t: (cap.__setitem__("t", t) or [0.0])
    b.embed_query("충전대 좌표")
    assert cap["t"].startswith("Instruct:") and "Query: 충전대 좌표" in cap["t"]

    b2 = OllamaBridge(embedding_model="nomic-embed-text")
    cap2 = {}
    b2.embed = lambda t: (cap2.__setitem__("t", t) or [0.0])
    b2.embed_query("충전대 좌표")
    assert cap2["t"] == "충전대 좌표"  # 비 qwen3 임베더는 원문 그대로


def test_location_save_nonstandard_key_and_text_name(tmp_path):
    """실기 회귀(충전대 유실): 약한 LLM이 "현재 위치를 충전대로 기억"을
    {"location_type":"Charging Station"} 처럼 이름키·좌표 없이 저장해도,
    텍스트("현재 위치는 충전대입니다")에서 이름을 뽑고 현재 pose로 좌표를 채워
    시맨틱 맵 동기화·좌표→이름 역조회가 동작해야 한다."""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)
    add.get_map_pose = lambda: {"x": 3.96, "y": 0.83, "frame": "map"}

    r = add.execute(
        {"text": "현재 위치는 충전대입니다.", "metadata": {"location_type": "Charging Station"}}
    )
    assert r["success"] is True

    # 좌표가 채워지고 표준 위치로 저장됨
    locs = [
        e for e in memory._vector_store.list_entries()
        if e["metadata"].get("type") == "location"
        and e["metadata"].get("location_name") == "충전대"
    ]
    assert locs and locs[0]["metadata"]["x"] == 3.96 and locs[0]["metadata"]["y"] == 0.83
    # 시맨틱 맵 동기화 → 이름/좌표 양방향 조회 동작
    assert memory.get_object_location("충전대") is not None
    near = memory.get_nearest_object(3.96, 0.83, radius_m=1.0)
    assert near is not None and near["name"] == "충전대"


def test_location_resave_dedups_prior_rag_entries(tmp_path):
    """같은 이름 위치를 다시 저장하면 이전 RAG 위치 항목이 제거되어, 좌표 충돌·중복이
    쌓이지 않는다 (실기 프로브에서 충전대가 3.96/3.72 두 좌표로 공존하던 문제)."""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)

    add.execute(
        {"text": "충전대의 위치는 x: 3.72, y: 0.79 입니다.",
         "metadata": {"location_name": "충전대", "x": 3.72, "y": 0.79}}
    )
    r2 = add.execute(
        {"text": "충전대의 위치는 x: 3.96, y: 0.83 입니다.",
         "metadata": {"location_name": "충전대", "x": 3.96, "y": 0.83}}
    )
    assert r2.get("deduped", 0) >= 1  # 이전 충전대 항목 제거됨

    locs = [
        e for e in memory._vector_store.list_entries()
        if e["metadata"].get("type") == "location"
        and e["metadata"].get("location_name") == "충전대"
    ]
    assert len(locs) == 1  # 충전대 위치 사실은 단 1건
    assert locs[0]["metadata"]["x"] == 3.96  # 최신 좌표만 남음


def test_rag_search_returns_semantic_map_coords(tmp_path):
    """정방향 위치질의(A): 등록된 장소면 rag_search가 시맨틱 맵 좌표를 확정 반환한다
    (RAG 점수/오염과 무관). planner가 '<장소> 좌표 알려줘'를 rag_search로 라우팅한 뒤
    이 경로로 좌표가 나온다."""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)
    add.get_map_pose = lambda: {"x": 3.96, "y": 0.83, "frame": "map"}
    add.execute({"text": "현재 위치는 충전대입니다.", "metadata": {"location_name": "충전대"}})

    search = RAGSearchSkill()
    search.node = SimpleNamespace(_memory=memory)
    r = search.execute({"query": "충전대 위치 좌표를 알려줘"})
    assert r["success"] and "x: 3.96" in r["message"] and "y: 0.83" in r["message"]
    assert r["results"][0]["metadata"]["location_name"] == "충전대"

    # A2 게이팅: 위치의도 없는 일반 쿼리는 시맨틱 단락 없이 None(일반 RAG로 폴백),
    #           등록 장소 + 위치의도면 확정 좌표 답변을 만든다.
    assert search._semantic_location_answer("오늘 날씨 어때") is None
    assert search._semantic_location_answer("충전대 좌표 알려줘") is not None


def test_entries_are_time_sortable_and_listed_in_time_order(tmp_path):
    """Qdrant 는 삽입 순서를 보존하지 않고 scroll 이 point ID 순으로 반환한다.
    → ID를 시간 정렬 가능한 UUIDv7로 발급하고, list_entries 는 시간순으로 돌려준다."""
    from uuid import UUID

    from robo_claw_agent.vector_store.base import new_entry_id

    ids = [new_entry_id() for _ in range(5)]
    assert {UUID(i).version for i in ids} == {7}   # 표준 UUID(v7)
    assert sorted(ids) == ids                       # ID 정렬 = 생성(시간) 순

    memory = _make_memory(tmp_path)
    memory.add_knowledge("첫 번째", {"type": "note"})
    memory.add_knowledge("두 번째", {"type": "note"})
    memory.add_knowledge("세 번째", {"type": "note"})

    entries = memory._vector_store.list_entries()
    texts = [e["text"] for e in entries]
    assert texts == ["첫 번째", "두 번째", "세 번째"]          # 시간순(오래된 것 → 최신)
    stamps = [e.get("timestamp", "") for e in entries]
    assert stamps == sorted(stamps)


def test_forward_lookup_uses_navigated_coordinate_when_no_location(tmp_path):
    """문제3: 형식적 위치(type=location)가 없어도 이동 기록(navigated_coordinate)의 근거로
    정방향 좌표를 답한다. '이동 성공 좌표: … (키친 (-5.15, -1.18))' 괄호 안 이름을 인식."""
    memory = _make_memory(tmp_path)
    memory.add_knowledge(
        "이동 성공 좌표: x=-5.15, y=-1.18 (키친 (-5.15, -1.18))",
        {"type": "navigated_coordinate", "kind": "place", "x": -5.15, "y": -1.18,
         "source": "navigate_to"},
    )
    search = RAGSearchSkill()
    search.node = SimpleNamespace(_memory=memory)
    r = search.execute({"query": "키친의 위치를 알려줘"})
    assert r["success"] and "x: -5.15" in r["message"] and "y: -1.18" in r["message"]
    assert r.get("referenced")  # 참조 Qdrant 항목이 기록됨


def test_forward_lookup_finds_type_location_without_semantic_sync(tmp_path):
    """문제1: type=location 이 벡터스토어엔 있고 시맨틱 맵엔 없어도(동기화 어긋남)
    정방향 조회가 스토어를 스캔해 좌표를 찾는다."""
    memory = _make_memory(tmp_path)
    memory.add_knowledge(
        "이곳은 거실입니다.",
        {"location_name": "거실", "x": 0.63, "y": 0.55, "type": "location"},
    )
    assert memory.get_object_location("거실") is None  # 시맨틱 맵 미동기화 상태
    search = RAGSearchSkill()
    search.node = SimpleNamespace(_memory=memory)
    r = search.execute({"query": "거실 위치 알려줘"})
    assert r["success"] and "x: 0.63" in r["message"] and "y: 0.55" in r["message"]


def test_dedup_removes_stale_location_type_entry(tmp_path):
    """B: 좌표 없이 저장됐던 옛 항목({location_type:...} + 텍스트 '…충전대입니다')이
    같은 장소 재저장 시 정리되어, 좌표 있는 사실 1건만 남는다(실기 스냅샷의 충전대 오염 대응)."""
    memory = _make_memory(tmp_path)
    # 옛 버그 형태로 직접 적재: location_name·좌표 없음, type도 location 아님
    memory.add_knowledge("현재 위치는 충전대입니다.", {"location_type": "Charging Station"})

    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)
    add.get_map_pose = lambda: {"x": 3.96, "y": 0.83, "frame": "map"}
    r = add.execute({"text": "현재 위치는 충전대입니다.", "metadata": {"location_name": "충전대"}})
    assert r["deduped"] >= 1  # 옛 무좌표 항목 제거됨

    entries = memory._vector_store.list_entries()
    stale = [e for e in entries if e["metadata"].get("location_type") == "Charging Station"]
    coords = [e for e in entries
              if e["metadata"].get("location_name") == "충전대" and "x" in e["metadata"]]
    assert stale == []       # 옛 location_type 항목 소멸
    assert len(coords) == 1  # 좌표 있는 충전대 1건만 남음


def test_write_normalization_generalizes_across_sites(tmp_path):
    """저장 표준화: 이름/좌표가 metadata든 텍스트든, 한글/영어/다단어 등 어떤 장소명이든
    location_name/x/y/type=location 으로 정규화되어 시맨틱 맵 동기화·역방향 조회가
    사이트·장소명과 무관하게 일관 동작한다."""
    memory = _make_memory(tmp_path)
    add = RAGAddSkill()
    add.node = SimpleNamespace(_memory=memory)

    # 이름·좌표가 텍스트에만(거실) / 영어 다단어(living room) / 다단어 한글 + location 키
    add.execute({"text": "거실의 위치는 x: 0.66, y: 0.73입니다.", "metadata": {"heading_deg": -97.8}})
    add.execute({"text": "'living room'의 위치는 x: 1.2, y: 3.4", "metadata": {}})
    add.execute({"text": "회의실 2 위치: x=-2.0, y=5.5", "metadata": {"location": "회의실 2"}})

    # 세 건 모두 type=location + location_name 으로 표준화
    locs = [e for e in memory._vector_store.list_entries() if e["metadata"].get("type") == "location"]
    assert {e["metadata"]["location_name"] for e in locs} == {"거실", "living room", "회의실 2"}

    # 시맨틱 맵 동기화 → 각 장소에서 좌표→이름 역방향 조회 동작
    skill = IdentifyLocationSkill()
    skill.node = SimpleNamespace(_memory=memory)
    assert skill.execute({"x": 0.67, "y": 0.72})["location_name"] == "거실"
    assert skill.execute({"x": 1.19, "y": 3.41})["location_name"] == "living room"
    assert skill.execute({"x": -2.0, "y": 5.49})["location_name"] == "회의실 2"


# ── list_entries 스캔 비용/무손실 회귀 ──────────────────────────────
# 배경: list_entries() 가 항상 with_vectors=True 로 스크롤해 1024-dim 벡터를 전부
# 끌어왔다. 위치 조회는 매 질의마다 이 스캔을 타므로 컬렉션이 커지면 Qdrant timeout(5s)
# 을 넘기고, 호출부가 예외를 폴백 처리하면서 "데이터는 있는데 못 찾음"으로 열화된다.
# 더 나쁜 것은 벡터가 list 로 안 오면 항목을 조용히 버려 스캔 결과가 빈손이 되던 점.


class _FakeQdrantRecord:
    def __init__(self, point_id, payload, vector=None):
        self.id = point_id
        self.payload = payload
        self.vector = vector


class _FakeQdrantClient:
    """scroll 호출의 with_vectors 인자를 기록하는 최소 더블."""

    def __init__(self, records):
        self._records = records
        self.last_with_vectors = None

    def collection_exists(self, _name):
        return True

    def scroll(self, _name, limit=128, with_payload=True, with_vectors=False,
               offset=None, timeout=None):
        self.last_with_vectors = with_vectors
        records = []
        for rec in self._records:
            records.append(
                _FakeQdrantRecord(
                    rec.id, rec.payload, rec.vector if with_vectors else None
                )
            )
        return records, None


def _fake_store(records):
    client = _FakeQdrantClient(records)
    return QdrantVectorStore(client, "c"), client


def test_list_entries_does_not_fetch_vectors_by_default(tmp_path):
    """기본 스캔은 벡터를 요청하지 않는다(원격 페이로드 폭증·타임아웃 방지)."""
    recs = [
        _FakeQdrantRecord(
            "id-1",
            {"text": "현재 위치를 충전대로 기억합니다.",
             "metadata": {"location_name": "충전대", "x": 3.81, "y": 0.71,
                          "type": "location"},
             "timestamp": "2026-08-12T11:09:45"},
            vector=[0.0, 1.0, 0.0],
        )
    ]
    store, client = _fake_store(recs)

    entries = store.list_entries()

    assert client.last_with_vectors is False
    assert len(entries) == 1
    assert "vector" not in entries[0]
    assert entries[0]["metadata"]["location_name"] == "충전대"


def test_list_entries_keeps_points_when_vectors_absent(tmp_path):
    """벡터를 안 받아온 스캔에서도 항목을 버리지 않는다.

    이전 구현은 ``isinstance(vector, list)`` 가 아니면 continue 해서, 벡터 없는 스캔이
    항상 빈 목록을 돌려줬다 → 위치 조회 폴백 전멸.
    """
    recs = [
        _FakeQdrantRecord("a", {"text": "충전대", "metadata": {"type": "location"}}, None),
        _FakeQdrantRecord("b", {"text": "거실", "metadata": {"type": "location"}}, None),
    ]
    store, _ = _fake_store(recs)

    assert [e["id"] for e in store.list_entries()] == ["a", "b"]


def test_list_entries_with_vectors_returns_usable_vectors(tmp_path):
    """reconcile/재색인 경로는 with_vectors=True 로 실제 벡터를 받는다."""
    recs = [
        _FakeQdrantRecord("a", {"text": "t", "metadata": {}}, [1.0, 2.0, 3.0]),
    ]
    store, client = _fake_store(recs)

    entries = store.list_entries(with_vectors=True)

    assert client.last_with_vectors is True
    assert entries[0]["vector"] == [1.0, 2.0, 3.0]


def test_named_vector_dict_is_accepted():
    """클라이언트/서버 버전에 따라 단일 벡터가 dict 로 오는 경우도 받아준다."""
    assert _vector_store_qdrant._as_vector([1, 2]) == [1.0, 2.0]
    assert _vector_store_qdrant._as_vector({"": [1, 2]}) == [1.0, 2.0]
    assert _vector_store_qdrant._as_vector(None) is None
    assert _vector_store_qdrant._as_vector({"a": [1], "b": [2]}) is None


def test_local_store_list_entries_honours_with_vectors(tmp_path):
    """로컬 미러도 동일 규약을 따른다(DualVectorStore 가 그대로 위임하므로)."""
    memory = _make_memory(tmp_path)
    memory.add_knowledge("충전대 메모", {"type": "location", "location_name": "충전대",
                                     "x": 1.0, "y": 2.0})

    assert "vector" not in memory._vector_store.list_entries()[0]
    assert memory._vector_store.list_entries(with_vectors=True)[0]["vector"]


# ── 위치질의 결정론적 라우팅 회귀 (planner) ──────────────────────
# 배경(report-20260812-140113): 이름 기반 이동(tc13~15)은 100% 성공하는데 위치 조회
# (tc03/07/10/11/12)는 실패했다. 두 경로의 보장 수준이 달랐다.
#   - navigate_to : 시맨틱맵 → RAG(threshold 0.35) → **전수 스캔 문자열 매칭** (임베딩 무관)
#   - rag_search  : 라우팅이 발동하지 않으면 LLM이 쓴 query + 임베딩 점수에만 의존
# 라우팅이 발동하지 못한 이유 2가지를 여기서 고정한다.
#   (A) `_known_place_in_text` 가 시맨틱 맵만 봤다 → desync 시 조용히 미발동
#   (B) `len(chain) != 1` 이면 아무것도 안 했다 → 약한 LLM의 2스텝 계획에서 미발동
# 추가로 tc17(스킬 없이 산문만 응답)도 결정론적으로 처리되는지 검증한다.


def _load_planner():
    """planner.py 를 ROS 패키지 __init__ 없이 로드한다.

    `robo_claw_agent.agent_node.__init__` 이 execution.py → robo_claw_msgs 를 끌어오므로
    패키지 모듈을 스텁으로 등록한 뒤 planner 서브모듈만 실제 파일에서 실행한다.
    (planner 가 쓰는 tracing/types/nav_safety/utils 는 모두 ROS 비의존)
    """
    import sys
    import types

    agent_node_dir = Path(robo_claw_agent.__file__).parent / "agent_node"
    pkg_name = "robo_claw_agent.agent_node"
    if pkg_name not in sys.modules:
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = [str(agent_node_dir)]
        sys.modules[pkg_name] = pkg
    mod_name = pkg_name + ".planner"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(mod_name, agent_node_dir / "planner.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


class _NullLogger:
    def info(self, *a, **k): pass
    def warning(self, *a, **k): pass
    def error(self, *a, **k): pass


def _desynced_node(entries):
    """시맨틱 맵이 비어 있고(desync) 벡터스토어에만 위치 사실이 있는 노드."""
    class Store:
        def list_entries(self, **kw): return entries
    class Mem:
        def __init__(self): self._vector_store = Store()
        def get_all_objects(self): return []
    return SimpleNamespace(_memory=Mem())


_LOC_ENTRIES = [
    {"id": "1", "text": "현재 위치를 거실로 기억합니다.",
     "metadata": {"location_name": "거실", "x": 0.87, "y": 0.79, "type": "location"}},
    {"id": "2", "text": "현재 위치를 충전대로 기억합니다.",
     "metadata": {"location_name": "충전대", "x": 3.74, "y": 0.85, "type": "location"}},
]


def test_routing_fires_when_semantic_map_desynced():
    """(A) 시맨틱 맵이 비어도 벡터스토어의 위치 사실로 장소명을 알아내 query 를 고정한다."""
    P = _load_planner()
    node = _desynced_node(_LOC_ENTRIES)
    # LLM이 query 를 장소명 없이 재작성한 상황
    chain = [{"skill": "rag_search", "params": {"query": "위치"}}]
    out = P._route_location_query(chain, "거실의 위치를 알려줘", node, _NullLogger())
    assert out == [{"skill": "get_location", "params": {"location_name": "거실"}}]


def test_routing_fires_on_multi_step_lookup_chain():
    """(B) 약한 LLM의 2스텝 조회 계획도 교정한다(과거엔 len!=1 이라 미발동)."""
    P = _load_planner()
    node = _desynced_node(_LOC_ENTRIES)
    chain = [
        {"skill": "get_status", "params": {}},
        {"skill": "rag_search", "params": {"query": "charging station"}},
    ]
    out = P._route_location_query(chain, "충전대 위치를 알려줘", node, _NullLogger())
    assert out == [{"skill": "get_location", "params": {"location_name": "충전대"}}]


def test_routing_does_not_hijack_navigation():
    """물리 동작이 섞인 계획은 건드리지 않는다 — '거실 위치로 이동해'를 조회로 바꾸면 안 된다."""
    P = _load_planner()
    node = _desynced_node(_LOC_ENTRIES)
    for instruction in ("거실 위치로 이동해", "거실로 이동해"):
        chain = [{"skill": "navigate_to", "params": {"target_name": "거실"}}]
        out = P._route_location_query(list(chain), instruction, node, _NullLogger())
        assert out == chain, instruction


def test_no_skill_plan_still_answers_location_query():
    """tc17: 모델이 툴 호출 대신 산문을 내도 위치 질의는 결정론적으로 스킬이 정해진다."""
    P = _load_planner()
    node = _desynced_node(_LOC_ENTRIES)
    forced = P._location_chain_for(
        "현재 위치 x, y 좌표와 비교해서 현재 위치의 이름을 파악해봐", node, _NullLogger()
    )
    assert forced == [{"skill": "identify_location", "params": {}}]

    # 정방향도 동일하게 강제된다
    assert P._location_chain_for("거실의 위치를 알려줘", node, _NullLogger()) == [
        {"skill": "get_location", "params": {"location_name": "거실"}}
    ]
    # 위치와 무관한 지시문은 개입하지 않는다
    assert P._location_chain_for("배터리 얼마나 남았어?", node, _NullLogger()) is None


def test_save_routing_respects_matching_rag_add():
    """저장 라우팅: LLM이 rag_add + 장소명을 냈으면 **이름이 달라도** 그 값을 신뢰한다.

    규칙이 LLM 값을 덮어쓰던 탓에, 조사 파싱을 틀린 규칙이 올바른 "키친"을 "키친으"로
    망가뜨렸다(실측 2026-08-13/14). 개체명 추출은 LLM의 일이고 규칙은 폴백이어야 한다.
    """
    P = _load_planner()
    node = _desynced_node(_LOC_ENTRIES)
    planned = [{"skill": "rag_add",
                "params": {"text": "현재 위치를 거실로 기억합니다.",
                           "metadata": {"location_name": "거실"}}}]
    assert P._route_location_query(list(planned), "그 위치를 거실로 저장해", node,
                                   _NullLogger()) == planned

    # 규칙이 "키친으"를 뽑더라도 LLM의 "키친"이 살아남아야 한다.
    llm = [{"skill": "rag_add",
            "params": {"text": "현재 위치를 키친으로 기억합니다.",
                       "metadata": {"location_name": "키친"}}}]
    assert P._route_location_query(list(llm), "그 위치를 키친으로 기억해", node,
                                   _NullLogger()) == llm

    # LLM이 이름을 안 줬을 때만 규칙 폴백이 개입한다.
    no_name = [{"skill": "rag_add", "params": {"text": "저장", "metadata": {}}}]
    out = P._route_location_query(list(no_name), "그 위치를 키친으로 기억해", node,
                                  _NullLogger())
    assert out[0]["params"]["metadata"]["location_name"] == "키친"
    # 검색으로 오라우팅한 경우엔 rag_add 로 강제
    out = P._route_location_query([{"skill": "rag_search", "params": {"query": "거실"}}],
                                  "그 위치를 거실로 저장해", node, _NullLogger())
    assert out[0]["skill"] == "rag_add"
    assert out[0]["params"]["metadata"]["location_name"] == "거실"


def test_dual_store_uses_time_sortable_ids():
    """미러 ON 배포에서도 point ID 가 UUIDv7 이어야 한다.

    de321fa 가 qdrant/local 만 바꾸고 dual.py 의 uuid4 를 놓쳐, 미러가 켜진 실기
    컬렉션의 point ID 가 전부 v4 였다(UUIDv7 도입 효과 전무).
    """
    import uuid as _uuid

    from robo_claw_agent.vector_store import DualVectorStore

    captured = {}

    class Rec:
        def add(self, *, text, metadata, vector, timestamp, id=None):
            captured.setdefault("ids", []).append(id)
            return True

    DualVectorStore(Rec(), Rec()).add(
        text="t", metadata={}, vector=[1.0], timestamp="2026-01-01T00:00:00"
    )
    ids = captured["ids"]
    assert len(ids) == 2 and ids[0] == ids[1], "양쪽 스토어에 같은 id 를 써야 한다"
    assert _uuid.UUID(ids[0]).version == 7


# ── 조사 "으로" 오식별 + 이동 명령 오라우팅 회귀 ──────────────────
# 실측 리포트 2건(2026-08-13 31b allpass, 2026-08-14 e4b) 반영.


def test_save_place_extraction_handles_euro_particle():
    """"키친으로 기억해" 에서 조사 '으로' 의 '으' 를 이름에 붙이면 안 된다.

    탐욕 매칭이던 `_SAVE_PLACE_RE` 가 `키친으` 를 뽑아 그 이름으로 저장됐다(실측:
    리포트의 `'키친으'의 위치는 …`). 받침 있는 이름은 조사가 '으로' 라서 이 계열
    이름(키친·주방·현관 …)이 전부 오염되고, 시맨틱 단락도 이름 불일치로 깨진다.
    """
    P = _load_planner()
    assert P._extract_save_place("그 위치를 키친으로 기억해") == "키친"
    assert P._extract_save_place("현재 위치를 주방으로 저장해") == "주방"
    assert P._extract_save_place("여기를 현관으로 등록해") == "현관"
    # '로' 조사(ㄹ받침·모음 종결)도 그대로 동작해야 한다
    assert P._extract_save_place("현재 위치를 충전대로 기억해") == "충전대"
    assert P._extract_save_place("그 위치를 거실로 저장해") == "거실"


def test_misrouted_navigation_command_is_corrected():
    """"<장소>로 이동해" 를 조회 스킬로 계획하면 navigate_to 로 교정한다.

    실측(e4b): "키친으로 이동해" → `get_status` 단독 계획 → 로봇이 움직이지 않아
    이후 역방향 조회(tc16/17)가 옛 위치를 보고해 3건 연쇄 실패.
    """
    P = _load_planner()
    log = _NullLogger()
    for planned in (
        [{"skill": "get_status", "params": {}}],
        [{"skill": "identify_location", "params": {}}],
        [{"skill": "get_location", "params": {"location_name": "키친"}}],
        [],  # 스킬 없이 산문만 답한 경우
    ):
        out = P._force_navigation_if_misrouted(list(planned), "키친으로 이동해", log)
        assert out == [{"skill": "navigate_to", "params": {"target_name": "키친"}}], planned


def test_navigation_correction_preserves_existing_movement_plan():
    """이미 이동 스킬이 계획돼 있으면 손대지 않는다(복합 태스크 보존)."""
    P = _load_planner()
    log = _NullLogger()
    chain = [
        {"skill": "navigate_to", "params": {"target_name": "거실"}},
        {"skill": "capture_camera_image", "params": {}},
    ]
    assert P._force_navigation_if_misrouted(list(chain), "거실로 이동해서 사진 찍어", log) == chain


def test_navigation_correction_ignores_non_navigation_instruction():
    """이동 목적지가 없는 지시문에는 개입하지 않는다."""
    P = _load_planner()
    log = _NullLogger()
    chain = [{"skill": "get_status", "params": {}}]
    assert P._force_navigation_if_misrouted(list(chain), "배터리 얼마나 남았어?", log) == chain
    assert P._force_navigation_if_misrouted([], "배터리 얼마나 남았어?", log) == []


def test_save_routing_uses_clean_place_name():
    """저장 라우팅이 만드는 rag_add 의 location_name 에도 조사가 섞이지 않는다."""
    P = _load_planner()
    node = _desynced_node(_LOC_ENTRIES)
    forced = P._location_chain_for("그 위치를 키친으로 기억해", node, _NullLogger())
    assert forced[0]["skill"] == "rag_add"
    assert forced[0]["params"]["metadata"]["location_name"] == "키친"


# ── get_location 통합 회귀 (2026-08-14 origin/main 병합) ──────────
# main 이 이름→좌표 전용 스킬 get_location 을 신설했고, 우리 rag_search 정방향 단락과
# 기능이 중복됐다. 조회 본체를 `_best_location_for_targets` 로 통일하고 라우팅 타겟을
# get_location 으로 바꿨다. 아래는 그 통합이 유지되는지 고정한다.


def _get_location_skill(memory):
    skill = _RAG.GetLocationSkill()
    skill.node = SimpleNamespace(_memory=memory)
    return skill


def test_get_location_uses_shared_lookup_with_semantic_map(tmp_path):
    """시맨틱 맵이 1순위 근거로 쓰인다."""
    memory = _make_memory(tmp_path)
    memory.add_object_location("거실", 0.87, 0.80, metadata={"type": "location"})
    out = _get_location_skill(memory).execute({"location_name": "거실"})
    assert out["success"] is True
    assert out["position"] == {"x": 0.87, "y": 0.8}
    assert "0.87" in out["message"] and "거실" in out["message"]


def test_get_location_accepts_navigated_coordinate_as_last_resort(tmp_path):
    """`type=location` 이 없고 이동 기록만 있어도 좌표를 찾는다.

    main 의 원래 구현은 `_NON_LOCATION_TYPES` 로 navigated_coordinate 를 제외해서
    이 케이스(실측: 키친이 이동 기록만 남은 상태)에서 좌표를 못 찾았다.
    공용 `_best_location_for_targets` 는 최후 순위로 인정한다.
    """
    memory = _make_memory(tmp_path)
    memory.add_knowledge(
        "이동 성공 좌표: x=-5.16, y=-1.17 (키친 (-5.16, -1.17))",
        {"type": "navigated_coordinate", "kind": "place", "x": -5.16, "y": -1.17},
    )
    out = _get_location_skill(memory).execute({"location_name": "키친"})
    assert out["success"] is True
    assert out["position"] == {"x": -5.16, "y": -1.17}


def test_get_location_reports_not_found_without_evidence(tmp_path):
    memory = _make_memory(tmp_path)
    out = _get_location_skill(memory).execute({"location_name": "없는장소"})
    assert out["success"] is True
    assert out["position"] is None
    assert "찾지 못했습니다" in out["message"]


def test_get_location_routing_respects_matching_llm_params():
    """LLM이 이미 같은 location_name 을 넣었으면 계획을 그대로 존중한다."""
    P = _load_planner()
    node = _desynced_node(_LOC_ENTRIES)
    planned = [{"skill": "get_location", "params": {"location_name": "거실"}}]
    assert P._route_location_query(list(planned), "거실의 위치를 알려줘", node,
                                   _NullLogger()) == planned
