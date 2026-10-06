import logging
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

logger = logging.getLogger(__name__)


class ReflectSkillsSkill(BaseSkill):
    """수집된 스킬 경험을 분석해 교훈을 추출·갱신하는 스킬"""

    name = "reflect_skills"
    input_schema = {"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False}
    risk_level = "action"
    allow_with_others = False
    description = (
        "수집된 스킬 실행 경험(성공/실패)을 LLM으로 분석하여 스킬별 교훈을 추출·갱신합니다. "
        "교훈은 이후 시스템 프롬프트에 자동 반영됩니다. "
        "파라미터: skill_name(str, 선택 — 지정 시 해당 스킬만), "
        "min_episodes(int, 선택, 기본 4)."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None or not hasattr(self.node, "_memory"):
            return {"success": False, "message": "메모리 시스템에 접근할 수 없습니다."}

        memory = self.node._memory
        if not memory.rag_status().get("enabled"):
            return {"success": False, "message": "RAG가 비활성화 상태입니다."}

        llm = getattr(self.node, "_llm", None)
        if llm is None:
            return {"success": False, "message": "LLM이 설정되지 않아 교훈을 추출할 수 없습니다."}

        try:
            min_episodes = int(params.get("min_episodes", 4))
        except (TypeError, ValueError):
            min_episodes = 4

        skill_name = str(params.get("skill_name", "")).strip()
        if skill_name:
            result = memory.reflect_skill(skill_name, llm, min_episodes=min_episodes)
            stored = bool(result.get("stored"))
            if stored and hasattr(self.node, "mark_skill_lessons_dirty"):
                self.node.mark_skill_lessons_dirty()
            return {
                "success": stored,
                "message": (
                    f"'{skill_name}' 교훈을 갱신했습니다."
                    if stored
                    else f"'{skill_name}' 교훈 갱신 안 됨: {result.get('reason', '사유 미상')}"
                ),
                "result": result,
            }

        summary = memory.reflect_all_skills(llm, min_episodes=min_episodes)
        if summary.get("reflected") and hasattr(self.node, "mark_skill_lessons_dirty"):
            self.node.mark_skill_lessons_dirty()
        return {
            "success": bool(summary.get("success")),
            "message": summary.get("message", "교훈 추출 완료"),
            "reflected": summary.get("reflected", []),
            "skipped": summary.get("skipped", []),
        }
