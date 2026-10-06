"""스킬 자가 학습(경험 수집) 단위 테스트"""

import robo_claw_agent.memory_manager.skill_learning as sl
from robo_claw_agent.memory_manager import MemoryManager


class DummyEmbedder:
    def embed(self, text: str):
        return [float(len(text) % 7), 1.0, 0.0]


class KeywordEmbedder:
    """키워드 등장 여부로 결정적 임베딩을 만드는 테스트용 임베더."""

    def embed(self, text: str):
        v = [0.0, 0.0, 0.0]
        if "이동" in text:
            v[0] = 1.0
        if "배터리" in text:
            v[1] = 1.0
        if v == [0.0, 0.0, 0.0]:
            v[2] = 1.0
        return v


_DEFAULT = object()


def _make_manager(tmp_path, *, embedder=_DEFAULT, sample_rate=0.1):
    if embedder is _DEFAULT:
        embedder = DummyEmbedder()
    return MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=embedder,
        rag_score_threshold=0.0,
        skill_learning_success_sample_rate=sample_rate,
    )


def _episodes(mgr):
    return [
        e
        for e in mgr._vector_store.list_entries()
        if e.get("metadata", {}).get("type") == "skill_episode"
    ]


def test_failure_always_recorded(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=0.0)
    stored = mgr.record_skill_episode(
        "navigate_to",
        success=False,
        duration_sec=1.2,
        error="NAVIGATION_TIMEOUT",
        params={"target_name": "주방"},
        instruction="주방으로 가줘",
        replanned=True,
    )
    assert stored is True

    eps = _episodes(mgr)
    assert len(eps) == 1
    meta = eps[0]["metadata"]
    assert meta["skill_name"] == "navigate_to"
    assert meta["success"] is False
    assert meta["replanned"] is True
    assert "NAVIGATION_TIMEOUT" in meta["error"]
    assert meta["instruction"] == "주방으로 가줘"


def test_success_sampling_zero_skips(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=0.0)
    stored = mgr.record_skill_episode("navigate_to", success=True)
    assert stored is False
    assert _episodes(mgr) == []


def test_success_sampling_one_records(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    stored = mgr.record_skill_episode(
        "navigate_to", success=True, params={"verbose": True}
    )
    assert stored is True
    eps = _episodes(mgr)
    assert len(eps) == 1
    assert eps[0]["metadata"]["success"] is True
    assert eps[0]["metadata"]["error"] == ""


def test_params_summary_omits_bulky_and_truncates(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    big = "x" * 1000
    mgr.record_skill_episode(
        "analyze_scene",
        success=False,
        params={"image_base64": big, "camera_topic": "/cam"},
    )
    brief = _episodes(mgr)[0]["metadata"]["params_brief"]
    assert big not in brief
    assert "<omitted>" in brief
    assert "/cam" in brief
    assert len(brief) <= 203  # 200 + "..."


def test_no_embedder_is_noop(tmp_path):
    mgr = _make_manager(tmp_path, embedder=None, sample_rate=1.0)
    # 임베더 부재 시 예외 없이 False 반환
    assert mgr.record_skill_episode("rotate", success=False, error="boom") is False
    assert _episodes(mgr) == []


def test_empty_skill_name_skipped(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    assert mgr.record_skill_episode("", success=False) is False
    assert _episodes(mgr) == []


# ── 교훈 추출 (reflection) ────────────────────────────────


class FakeLLM:
    def __init__(self, reply="navigate_to는 상대 이동에 쓰지 말고 move_relative를 쓰라."):
        self.reply = reply
        self.calls = []

    def chat(self, messages, system_prompt=None):
        self.calls.append(messages)
        return self.reply


def _lessons(mgr, skill_name=""):
    return mgr.get_skill_lessons(skill_name)


def _seed_failures(mgr, skill_name, n):
    for i in range(n):
        mgr.record_skill_episode(
            skill_name,
            success=False,
            error=f"ERR_{i}",
            instruction=f"명령 {i}",
            replanned=True,
        )


def test_overview_aggregates(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_failures(mgr, "navigate_to", 3)
    mgr.record_skill_episode("navigate_to", success=True)
    ov = mgr.skill_learning_overview()
    assert ov["navigate_to"]["count"] == 4
    assert ov["navigate_to"]["failure"] == 3
    assert ov["navigate_to"]["success"] == 1
    assert ov["navigate_to"]["success_rate"] == 0.25


def test_reflect_skill_stores_lesson(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_failures(mgr, "navigate_to", 4)
    llm = FakeLLM()

    result = mgr.reflect_skill("navigate_to", llm, min_episodes=4)
    assert result["stored"] is True
    assert llm.calls, "LLM이 호출되어야 한다"

    lessons = _lessons(mgr, "navigate_to")
    assert len(lessons) == 1
    meta = lessons[0]["metadata"]
    assert meta["type"] == "skill_lesson"
    assert meta["skill_name"] == "navigate_to"
    assert meta["evidence_count"] == 4
    assert "move_relative" in meta["lesson"]


def test_reflect_skill_below_threshold_skips(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_failures(mgr, "rotate", 2)
    llm = FakeLLM()
    result = mgr.reflect_skill("rotate", llm, min_episodes=4)
    assert result["stored"] is False
    assert llm.calls == []
    assert _lessons(mgr, "rotate") == []


def test_upsert_replaces_existing_lesson(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_failures(mgr, "navigate_to", 4)

    mgr.reflect_skill("navigate_to", FakeLLM("교훈 v1"), min_episodes=4)
    mgr.reflect_skill("navigate_to", FakeLLM("교훈 v2"), min_episodes=4)

    lessons = _lessons(mgr, "navigate_to")
    assert len(lessons) == 1  # upsert: 교훈은 1건만 유지
    assert lessons[0]["metadata"]["lesson"] == "교훈 v2"


def test_reflect_all_only_threshold_met(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_failures(mgr, "navigate_to", 4)
    _seed_failures(mgr, "rotate", 1)
    llm = FakeLLM()

    summary = mgr.reflect_all_skills(llm, min_episodes=4)
    reflected_names = [r["skill_name"] for r in summary["reflected"]]
    assert "navigate_to" in reflected_names
    assert "rotate" not in reflected_names
    assert "rotate" in summary["skipped"]


def test_reflect_no_llm(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_failures(mgr, "navigate_to", 4)
    result = mgr.reflect_skill("navigate_to", None, min_episodes=4)
    assert result["stored"] is False


# ── 교훈 프롬프트 렌더링 (3단계) ──────────────────────────


def test_render_lessons_empty(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    assert mgr.render_skill_lessons() == ""


def test_render_lessons_includes_stored(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_failures(mgr, "navigate_to", 4)
    mgr.reflect_skill(
        "navigate_to", FakeLLM("상대 이동에는 move_relative를 쓰라."), min_episodes=4
    )

    section = mgr.render_skill_lessons()
    assert "[학습된 스킬 교훈]" in section
    assert "[navigate_to]" in section
    assert "move_relative" in section


def test_render_lessons_excludes_episodes(tmp_path):
    """episode만 있고 lesson이 없으면 빈 섹션이어야 한다."""
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_failures(mgr, "rotate", 3)
    assert mgr.render_skill_lessons() == ""


# ── ⑤ 가치 기반 에피소드 필터링 ──────────────────────────


def _seed_successes(mgr, skill_name, n, duration=10.0):
    """샘플링률 1.0로 성공 에피소드 n건을 강제 수집한다."""
    mgr._skill_learning_success_sample_rate = 1.0
    for _ in range(n):
        mgr.record_skill_episode(skill_name, success=True, duration_sec=duration)


def test_sparse_success_always_stored(tmp_path):
    """첫 성공(희소)은 낮은 샘플링률에서도 무조건 저장된다."""
    mgr = _make_manager(tmp_path, sample_rate=0.0001)
    assert mgr.record_skill_episode("scan_room", success=True) is True
    eps = _episodes(mgr)
    assert len(eps) == 1
    assert eps[0]["metadata"]["value_reason"] == "sparse"


def test_recovered_success_always_stored(tmp_path, monkeypatch):
    """위기 극복(recovered) 성공은 샘플링에서 탈락할 상황에도 저장된다."""
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_successes(mgr, "navigate_to", 4)  # 희소 구간 벗어남
    mgr._skill_learning_success_sample_rate = 0.0001
    monkeypatch.setattr(sl.random, "random", lambda: 0.99)  # 샘플 탈락 고정

    # 평범한 성공은 탈락
    assert (
        mgr.record_skill_episode("navigate_to", success=True, duration_sec=10.0)
        is False
    )
    # 위기 극복 성공은 저장
    assert (
        mgr.record_skill_episode(
            "navigate_to", success=True, duration_sec=10.0, recovered=True
        )
        is True
    )
    rec = [e for e in _episodes(mgr) if e["metadata"].get("recovered")]
    assert len(rec) == 1
    assert rec[0]["metadata"]["value_reason"] == "recovered"


def test_fast_success_always_stored(tmp_path, monkeypatch):
    """평균 대비 현저히 빠른 수행은 무조건 저장된다."""
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_successes(mgr, "navigate_to", 4, duration=10.0)  # 평균 10초
    mgr._skill_learning_success_sample_rate = 0.0001
    monkeypatch.setattr(sl.random, "random", lambda: 0.99)

    # 평균(10초)의 0.7배 미만 → 빠른 수행으로 저장
    assert (
        mgr.record_skill_episode("navigate_to", success=True, duration_sec=1.0)
        is True
    )
    fast = [
        e for e in _episodes(mgr) if e["metadata"].get("value_reason") == "fast"
    ]
    assert len(fast) == 1


def test_ordinary_success_sampled_out(tmp_path, monkeypatch):
    """평범하고 느린 성공은 잔여 확률 샘플링에서 탈락한다."""
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    _seed_successes(mgr, "navigate_to", 4, duration=10.0)
    before = len(mgr.get_skill_episodes("navigate_to"))
    mgr._skill_learning_success_sample_rate = 0.0001
    monkeypatch.setattr(sl.random, "random", lambda: 0.99)

    assert (
        mgr.record_skill_episode("navigate_to", success=True, duration_sec=10.0)
        is False
    )
    assert len(mgr.get_skill_episodes("navigate_to")) == before


def test_rate_zero_disables_success_learning(tmp_path):
    """rate<=0은 성공 학습 비활성으로 보고 고가치(recovered)도 저장하지 않는다."""
    mgr = _make_manager(tmp_path, sample_rate=0.0)
    assert (
        mgr.record_skill_episode("navigate_to", success=True, recovered=True) is False
    )
    assert _episodes(mgr) == []


# ── ③ 상황 인지형 교훈 인출 ──────────────────────────────


def _make_keyword_manager(tmp_path, *, threshold=0.5):
    return MemoryManager(
        storage_path=str(tmp_path / "mem.json"),
        embedder=KeywordEmbedder(),
        rag_score_threshold=threshold,
        skill_learning_success_sample_rate=1.0,
    )


def test_relevant_lessons_filters_by_query(tmp_path):
    """명령과 의미적으로 가까운 교훈만 인출하고 무관 교훈은 제외한다."""
    mgr = _make_keyword_manager(tmp_path, threshold=0.5)
    mgr.upsert_skill_lesson("navigate_to", "이동 시 속도를 낮춰라")
    mgr.upsert_skill_lesson("get_status", "배터리가 낮으면 충전하라")

    section = mgr.render_relevant_skill_lessons("이동 명령", top_k=5)
    assert "[학습된 스킬 교훈]" in section
    assert "[navigate_to]" in section
    assert "get_status" not in section


def test_relevant_lessons_excludes_episodes(tmp_path):
    """skill_lesson 타입만 인출하므로 episode는 결과에 섞이지 않는다."""
    mgr = _make_keyword_manager(tmp_path, threshold=0.0)
    mgr.record_skill_episode(
        "navigate_to", success=False, error="이동 실패", instruction="이동"
    )
    assert mgr.render_relevant_skill_lessons("이동", top_k=5) == ""


def test_relevant_lessons_empty_query(tmp_path):
    mgr = _make_manager(tmp_path, sample_rate=1.0)
    assert mgr.render_relevant_skill_lessons("") == ""


def test_relevant_lessons_no_embedder(tmp_path):
    """임베더 부재 시 빈 문자열 (호출 측이 전량 폴백)."""
    mgr = _make_manager(tmp_path, embedder=None, sample_rate=1.0)
    assert mgr.render_relevant_skill_lessons("이동") == ""
