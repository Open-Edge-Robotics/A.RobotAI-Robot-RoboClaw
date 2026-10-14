"""플래너의 결정론적 답변 합성 헬퍼."""

from typing import Any

from ...answer import render_result_data

# 정보성 스킬 성공 후 결과를 사용자에게 정리해 답하는 LLM 라운드 수 상한.
# 상한을 두어 모델이 조회 스킬을 반복 실행하려는 경우에도 지연/토큰이 무한히
# 늘지 않게 하고, 초과 시에는 실행 결과로 만든 결정론적 폴백으로 종료한다.
_MAX_ANSWER_ROUNDS = 1


def _compose_result_answer(skill_results: list[Any]) -> str:
    """실행된 스킬 결과로 결정론적 답변 텍스트를 만든다.

    요약 라운드가 답변을 내지 못했거나 스킬을 다시 계획한 경우의 폴백이다.
    운영 필드는 제외하고 실제 결과 데이터(result_data)를 사람이 읽을 수 있게
    풀어서 사용자에게 최소한의 정보를 보장한다.
    """
    parts: list[str] = []
    for res in skill_results:
        message = str(getattr(res, "message", "") or "").strip()
        rendered = render_result_data(getattr(res, "result_data", None))
        if rendered:
            parts.append(f"{message} ({rendered})" if message else rendered)
        elif message:
            parts.append(message)
    from ...answer import _clip_text_length

    return _clip_text_length("\n".join(parts))
