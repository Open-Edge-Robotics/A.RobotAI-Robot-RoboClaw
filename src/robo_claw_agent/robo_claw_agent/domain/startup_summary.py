"""외부 시스템에 의존하지 않는 시작 상태 요약을 제공합니다."""


def startup_summary(agent_id, mock_robot, llm_ready, rag_requested, rag_ready, skill_count):
    """초기화가 끝난 실제 상태와 로그 레벨을 반환합니다."""
    degraded = not llm_ready or (rag_requested and not rag_ready)
    rag = "정상" if rag_ready else ("초기화 실패" if rag_requested else "비활성(설정)")
    message = (
        f"AgentNode {'제한된 상태로 준비 완료' if degraded else '준비 완료'} "
        f"[id={agent_id}] · 로봇={'MOCK' if mock_robot else '실기기'} · "
        f"LLM={'정상' if llm_ready else '사용 불가'} · RAG={rag} · 스킬={skill_count}개"
    )
    return ("warning" if degraded else "info"), message
