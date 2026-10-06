"""스킬 자가 학습 — 스킬 실행 경험(성공/실패)을 RAG에 영속 수집한다.

1단계: 스킬 실행 1건을 metadata.type="skill_episode"로 저장.
- 실패는 항상 저장, 성공은 success_sample_rate 확률로 샘플링 저장.
- 저장 자체는 기존 RAGMixin.add_knowledge를 재사용한다 (벡터스토어/임베더 공유).
"""

import json
import logging
import random
from datetime import datetime
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# 파라미터 직렬화 시 제외할 대용량 키 (토큰/저장 효율)
_BULKY_PARAM_KEYS = ("image_base64", "image", "frame", "raw_data")
# params_brief / text 길이 상한
_PARAMS_BRIEF_LIMIT = 200
_ERROR_BRIEF_LIMIT = 300

# 교훈 추출 기본값
_DEFAULT_MIN_EPISODES = 4
_DEFAULT_MAX_EXAMPLES = 12
_LESSON_LIMIT = 600

# 성공 에피소드 가치 평가 (무작위 샘플링 대신 고가치 경험 우선 보존)
# 스킬 평균 수행시간의 이 배율 미만이면 '모범(빠른 수행)'으로 우대한다.
_FAST_DURATION_RATIO = 0.7

# 조회·자기참조 스킬은 학습 경험으로 저장하지 않는다.
# 이 스킬들의 실행 기록(예: "[스킬 rag_search 성공] ...")은 정작 좌표/사실 같은
# 답이 없어, 이후 사실 검색에서 노이즈로 상위에 잡혀 실제 지식을 밀어낸다.
_NON_LEARNABLE_SKILLS = frozenset(
    {
        "rag_search",
        "rag_add",
        "rag_add_file",
        "rag_delete",
        "rag_list",
        "rag_status",
        "rag_reindex",
        "get_status",
        "identify_location",
    }
)


class _ChatLLM(Protocol):
    def chat(
        self, messages: list[dict[str, str]], system_prompt: str | None = None
    ) -> str: ...


class SkillLearningMixin:
    """스킬 실행 경험 수집 및 교훈 추출 전담 (RAGMixin 위에서 동작)."""

    # RAGMixin에서 제공
    def add_knowledge(
        self, text: str, metadata: dict[str, Any] | None = None
    ) -> bool: ...

    def search_knowledge(
        self,
        query: str,
        top_k: int = 3,
        score_threshold: float | None = None,
        filter_metadata: dict[str, Any] | None = None,
        current_pose: tuple | None = None,
        radius_m: float | None = None,
    ) -> list[dict[str, Any]]: ...

    # MemoryManager 생성자에서 설정
    _skill_learning_success_sample_rate: float
    _vector_store: Any

    def record_skill_episode(
        self,
        skill_name: str,
        success: bool,
        *,
        duration_sec: float = 0.0,
        error: str = "",
        params: dict[str, Any] | None = None,
        instruction: str = "",
        replanned: bool = False,
        recovered: bool = False,
    ) -> bool:
        """스킬 실행 1건을 학습 경험으로 저장한다.

        실패는 항상 저장한다. 성공은 무작위 샘플링 대신 가치 기반으로 선별한다:
        위기 극복(recovered)·희소(첫 성공)·평균 대비 빠른 수행은 무조건 보존하고,
        그 외 평범한 성공만 success_sample_rate 확률로 샘플링한다.

        Returns:
            저장 여부 (샘플링 제외/임베더 부재 시 False)
        """
        skill_name = str(skill_name or "").strip()
        if not skill_name:
            return False

        # 조회·자기참조 스킬은 학습 경험으로 저장하지 않는다 (사실 검색 오염 방지).
        if skill_name in _NON_LEARNABLE_SKILLS:
            return False

        # 성공 선별: 실패는 항상 저장, 성공은 가치 기반 우선 + 잔여 확률 샘플링
        value_reason = ""
        if success:
            keep, value_reason = self._evaluate_success_value(
                skill_name, duration_sec, recovered
            )
            if not keep:
                return False

        params_brief = self._summarize_params(params)
        error_brief = str(error or "")[:_ERROR_BRIEF_LIMIT]
        situation = str(instruction or "").strip()

        outcome = "성공" if success else "실패"
        parts = [f"[스킬 {skill_name} {outcome}]"]
        if situation:
            parts.append(f"상황: '{situation}'.")
        if params_brief:
            parts.append(f"파라미터: {params_brief}.")
        if not success and error_brief:
            parts.append(f"사유: {error_brief}.")
        text = " ".join(parts)

        metadata: dict[str, Any] = {
            "type": "skill_episode",
            "skill_name": skill_name,
            "success": bool(success),
            "duration_sec": round(float(duration_sec or 0.0), 3),
            "error": error_brief,
            "params_brief": params_brief,
            "instruction": situation,
            "replanned": bool(replanned),
            "recovered": bool(recovered),
            "value_reason": value_reason,
        }

        stored = self.add_knowledge(text, metadata)
        if stored:
            logger.info(
                "Skill experience recorded: %s (%s%s%s)",
                skill_name,
                outcome,
                ", replanned" if replanned else "",
                f", value={value_reason}" if value_reason else "",
            )
        return stored

    def _evaluate_success_value(
        self, skill_name: str, duration_sec: float, recovered: bool
    ) -> tuple[bool, str]:
        """성공 에피소드의 저장 여부와 사유를 가치 기반으로 판단한다.

        rate<=0이면 성공 학습 비활성으로 보고 전부 스킵한다. rate>0이면
        고가치(위기 극복/희소/빠른 수행)는 무조건 보존, 그 외는 확률 샘플링한다.
        """
        rate = float(getattr(self, "_skill_learning_success_sample_rate", 0.0))
        if rate <= 0.0:
            return False, ""

        # 위기 극복: 실패 후 재플래닝으로 완수한 경험은 무조건 보존
        if recovered:
            return True, "recovered"

        # 희소(첫 성공 포함): 성공 표본이 부족하면 무조건 보존
        successes = [
            e
            for e in self.get_skill_episodes(skill_name)
            if e.get("metadata", {}).get("success")
        ]
        if len(successes) < _DEFAULT_MIN_EPISODES:
            return True, "sparse"

        # 모범(빠른 수행): 기존 성공 평균 대비 현저히 빠르면 무조건 보존
        durations = [
            float(e.get("metadata", {}).get("duration_sec") or 0.0) for e in successes
        ]
        durations = [d for d in durations if d > 0.0]
        if durations and duration_sec > 0.0:
            avg = sum(durations) / len(durations)
            if duration_sec < avg * _FAST_DURATION_RATIO:
                return True, "fast"

        # 그 외 평범한 성공: 잔여 확률 샘플링
        if random.random() < rate:
            return True, "sampled"
        return False, ""

    @staticmethod
    def _summarize_params(params: dict[str, Any] | None) -> str:
        """대용량 키를 제외하고 파라미터를 짧은 문자열로 요약한다."""
        if not isinstance(params, dict) or not params:
            return ""
        cleaned: dict[str, Any] = {}
        for key, value in params.items():
            if key in _BULKY_PARAM_KEYS:
                cleaned[key] = "<omitted>"
            else:
                cleaned[key] = value
        try:
            brief = json.dumps(cleaned, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            brief = str(cleaned)
        if len(brief) > _PARAMS_BRIEF_LIMIT:
            brief = brief[:_PARAMS_BRIEF_LIMIT] + "..."
        return brief

    # ── 조회 헬퍼 ──────────────────────────────────────────

    def _entries_by_type(self, type_value: str) -> list[dict[str, Any]]:
        try:
            entries = self._vector_store.list_entries()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to query vector store: %s", exc)
            return []
        return [
            e
            for e in entries
            if isinstance(e.get("metadata"), dict)
            and e["metadata"].get("type") == type_value
        ]

    def get_skill_episodes(
        self, skill_name: str = ""
    ) -> list[dict[str, Any]]:
        """수집된 스킬 경험(skill_episode) 항목을 반환한다."""
        episodes = self._entries_by_type("skill_episode")
        if skill_name:
            episodes = [
                e
                for e in episodes
                if e.get("metadata", {}).get("skill_name") == skill_name
            ]
        return episodes

    def get_skill_lessons(self, skill_name: str = "") -> list[dict[str, Any]]:
        """추출된 스킬 교훈(skill_lesson) 항목을 반환한다. (3단계 주입용)"""
        lessons = self._entries_by_type("skill_lesson")
        if skill_name:
            lessons = [
                e
                for e in lessons
                if e.get("metadata", {}).get("skill_name") == skill_name
            ]
        return lessons

    @staticmethod
    def _format_lessons_section(entries: list[dict[str, Any]]) -> str:
        """교훈 항목 목록을 시스템 프롬프트용 섹션 문자열로 포맷한다."""
        lines = []
        for e in entries:
            meta = e.get("metadata", {})
            name = str(meta.get("skill_name", "")).strip()
            lesson = str(meta.get("lesson", "")).strip()
            if name and lesson:
                lines.append(f"- [{name}] {lesson}")
        if not lines:
            return ""
        return (
            "[학습된 스킬 교훈]\n"
            "아래는 과거 스킬 실행 경험에서 스스로 도출한 교훈입니다. "
            "동일한 실수를 반복하지 말고 우선 참고하세요.\n"
            + "\n".join(lines)
        )

    def render_skill_lessons(self, max_items: int = 20) -> str:
        """학습된 교훈 전량을 최신 갱신순 상위 N건으로 렌더링한다. (폴백용)

        교훈이 없으면 빈 문자열을 반환한다. RAG 비활성 등으로 상황 인지형
        인출(render_relevant_skill_lessons)이 불가할 때의 폴백 경로다.
        """
        lessons = self.get_skill_lessons()
        if not lessons:
            return ""

        # 최신 갱신순 상위 N건만 (토큰 관리)
        lessons.sort(
            key=lambda e: e.get("metadata", {}).get("updated_at", ""),
            reverse=True,
        )
        return self._format_lessons_section(lessons[: max(1, max_items)])

    def render_relevant_skill_lessons(
        self,
        query: str,
        top_k: int = 5,
        current_pose: tuple | None = None,
    ) -> str:
        """현재 명령과 의미적으로 관련된 스킬 교훈만 RAG로 인출해 렌더링한다.

        임베더/벡터스토어가 없거나 관련 교훈이 없으면 빈 문자열을 반환한다.
        호출 측은 빈 문자열일 때 render_skill_lessons 전량 방식으로 폴백할 수 있다.
        """
        query = str(query or "").strip()
        if not query:
            return ""
        try:
            results = self.search_knowledge(
                query,
                top_k=top_k,
                filter_metadata={"type": "skill_lesson"},
                current_pose=current_pose,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to search relevant lessons: %s", exc)
            return ""
        return self._format_lessons_section(results)

    def skill_learning_overview(self) -> dict[str, dict[str, Any]]:
        """스킬별 경험 집계 (count/success/failure/success_rate)."""
        overview: dict[str, dict[str, Any]] = {}
        for ep in self.get_skill_episodes():
            meta = ep.get("metadata", {})
            name = str(meta.get("skill_name", "")) or "(unknown)"
            stat = overview.setdefault(
                name, {"count": 0, "success": 0, "failure": 0}
            )
            stat["count"] += 1
            if meta.get("success"):
                stat["success"] += 1
            else:
                stat["failure"] += 1
        for stat in overview.values():
            stat["success_rate"] = (
                round(stat["success"] / stat["count"], 3) if stat["count"] else 0.0
            )
        return overview

    # ── 교훈 저장 (upsert) ─────────────────────────────────

    def upsert_skill_lesson(
        self,
        skill_name: str,
        lesson: str,
        *,
        evidence_count: int = 0,
        success_rate: float = 0.0,
    ) -> bool:
        """스킬 교훈을 저장한다. 같은 스킬의 기존 교훈은 먼저 제거(upsert)."""
        skill_name = str(skill_name or "").strip()
        lesson = str(lesson or "").strip()
        if not skill_name or not lesson:
            return False
        lesson = lesson[:_LESSON_LIMIT]

        # 기존 동일 스킬 교훈 제거
        stale_ids = [
            str(e.get("id"))
            for e in self.get_skill_lessons(skill_name)
            if e.get("id") is not None
        ]
        if stale_ids:
            try:
                self._vector_store.delete_entries(ids=stale_ids)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to remove existing lesson (continuing): %s", exc)

        text = f"[스킬 교훈: {skill_name}] {lesson}"
        metadata: dict[str, Any] = {
            "type": "skill_lesson",
            "skill_name": skill_name,
            "lesson": lesson,
            "evidence_count": int(evidence_count),
            "success_rate": round(float(success_rate), 3),
            "updated_at": datetime.now().isoformat(),
        }
        return self.add_knowledge(text, metadata)

    # ── 교훈 추출 (reflection) ─────────────────────────────

    def reflect_skill(
        self,
        skill_name: str,
        llm: _ChatLLM,
        *,
        min_episodes: int = _DEFAULT_MIN_EPISODES,
        max_examples: int = _DEFAULT_MAX_EXAMPLES,
    ) -> dict[str, Any]:
        """한 스킬의 경험을 LLM으로 분석해 교훈을 추출·저장한다."""
        if llm is None:
            return {"skill_name": skill_name, "stored": False, "reason": "LLM 미설정"}

        episodes = self.get_skill_episodes(skill_name)
        if len(episodes) < min_episodes:
            return {
                "skill_name": skill_name,
                "stored": False,
                "reason": f"경험 부족 ({len(episodes)}/{min_episodes})",
            }

        total = len(episodes)
        success_n = sum(1 for e in episodes if e.get("metadata", {}).get("success"))
        success_rate = round(success_n / total, 3) if total else 0.0

        # 실패 사례 우선, 그다음 성공 사례를 표본으로 사용
        failures = [e for e in episodes if not e.get("metadata", {}).get("success")]
        successes = [e for e in episodes if e.get("metadata", {}).get("success")]
        sample = (failures + successes)[:max_examples]
        example_lines = "\n".join(
            f"- {str(e.get('text', '')).strip()}" for e in sample
        )

        prompt = (
            f"다음은 로봇 스킬 '{skill_name}'의 실행 경험 기록이다 "
            f"(총 {total}건, 성공률 {int(success_rate * 100)}%).\n"
            "이 경험들에서 같은 실수를 반복하지 않고 성공률을 높이기 위한 "
            "실행 지침(교훈)을 한국어로 1~3개의 짧은 항목으로 뽑아라.\n"
            "각 항목은 '~할 때는 ~하라' 형태의 행동 지침으로, 한 줄씩 작성한다.\n"
            "추측은 배제하고 기록에 드러난 패턴만 근거로 삼는다. 다른 설명은 출력하지 마라.\n\n"
            f"[경험 기록]\n{example_lines}\n\n[교훈]"
        )

        try:
            lesson = llm.chat([{"role": "user", "content": prompt}])
        except Exception as exc:  # noqa: BLE001
            logger.error("LLM call for lesson extraction failed (%s): %s", skill_name, exc)
            return {"skill_name": skill_name, "stored": False, "reason": str(exc)}

        lesson = str(lesson or "").strip()
        if not lesson:
            return {"skill_name": skill_name, "stored": False, "reason": "빈 교훈"}

        stored = self.upsert_skill_lesson(
            skill_name,
            lesson,
            evidence_count=total,
            success_rate=success_rate,
        )
        return {
            "skill_name": skill_name,
            "stored": stored,
            "lesson": lesson,
            "evidence_count": total,
            "success_rate": success_rate,
        }

    def reflect_all_skills(
        self,
        llm: _ChatLLM,
        *,
        min_episodes: int = _DEFAULT_MIN_EPISODES,
        max_examples: int = _DEFAULT_MAX_EXAMPLES,
    ) -> dict[str, Any]:
        """경험이 임계치를 넘은 모든 스킬에 대해 교훈을 추출한다."""
        if llm is None:
            return {"success": False, "message": "LLM 미설정", "reflected": []}

        overview = self.skill_learning_overview()
        reflected: list[dict[str, Any]] = []
        skipped: list[str] = []
        for name, stat in overview.items():
            if stat["count"] < min_episodes or name == "(unknown)":
                skipped.append(name)
                continue
            result = self.reflect_skill(
                name, llm, min_episodes=min_episodes, max_examples=max_examples
            )
            if result.get("stored"):
                reflected.append(result)
            else:
                skipped.append(name)

        return {
            "success": True,
            "message": f"교훈 {len(reflected)}건 갱신, {len(skipped)}건 건너뜀.",
            "reflected": reflected,
            "skipped": skipped,
        }
