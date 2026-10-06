import json
import logging
import re
from typing import TYPE_CHECKING, Any

from ..answer import render_structured_answer
from ..tracing import trace_process_inputs, trace_process_outputs, traceable

if TYPE_CHECKING:  # pragma: no cover - 타입 힌트 전용
    from robo_claw_agent.types import LLMPlanResult

logger = logging.getLogger(__name__)


def _omit_base64_data(data: Any) -> Any:
    """딕셔너리/리스트 구조를 순회하며 키에 'base64'가 포함된 대용량 데이터를 생략합니다.

    LLM 컨텍스트에 스킬 결과를 실어 보내는 모든 경로(planner의 라운드 간 피드백,
    task_planner의 단계 간 요약)가 공유하므로 rclpy 의존이 없는 이 모듈에 둔다.
    """
    if isinstance(data, dict):
        return {
            k: "<IMAGE_DATA_OMITTED_FOR_TOKEN_EFFICIENCY>"
            if isinstance(k, str) and "base64" in k.lower()
            else _omit_base64_data(v)
            for k, v in data.items()
        }
    elif isinstance(data, list):
        return [_omit_base64_data(item) for item in data]
    return data


_SKILL_NAME_ALIASES = {
    "navigate": "navigate_to",
    "get_current_location": "get_status",
    "get_current_date": "get_datetime",
    "get_date": "get_datetime",
    "current_date": "get_datetime",
    "get_current_time": "get_datetime",
    "get_time": "get_datetime",
    "current_time": "get_datetime",
    # 이름→좌표 조회 별칭은 실제 등록된 get_location 스킬로 정규화한다.
    # (과거에는 rag_search로 강제 변환해 시맨틱 맵 정확 조회를 우회했음)
    "lookup_location": "get_location",
    "find_location": "get_location",
    "get_coordinates": "get_location",
    "lookup_coordinates": "get_location",
    "find_coordinates": "get_location",
}


_SKILL_PARAM_ALIASES = {
    "navigate_to": {
        "destination": "target_name",
        "destination_name": "target_name",
        "location": "target_name",
        "place": "target_name",
        "target": "target_name",
        "target_location": "target_name",
    },
}


_LOCATION_QUERY_KEYS = (
    "query",
    "location",
    "place",
    "target",
    "target_name",
    "destination",
    "name",
)


def _normalize_skill_call(skill: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """LLM이 만든 명백한 스킬/파라미터 별칭을 실제 카탈로그 이름으로 정규화한다."""
    normalized_skill = _SKILL_NAME_ALIASES.get(skill, skill)

    # get_location(이름→좌표 조회)은 시맨틱 맵 정확 조회를 우선하므로,
    # LLM이 흔히 쓰는 파라미터 키를 location_name으로 정규화한다.
    if normalized_skill == "get_location":
        query_parts = [str(params.get(key, "")).strip() for key in _LOCATION_QUERY_KEYS]
        query = next((part for part in query_parts if part), "")
        if query:
            return "get_location", {"location_name": query}
        return "get_status", {}

    aliases = _SKILL_PARAM_ALIASES.get(normalized_skill, {})
    if not aliases:
        return normalized_skill, params

    normalized_params = dict(params)
    for old_key, new_key in aliases.items():
        if old_key in normalized_params and new_key not in normalized_params:
            normalized_params[new_key] = normalized_params.pop(old_key)
    return normalized_skill, normalized_params


def _coerce_tool_call(tc: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """OpenAI 스타일 및 단순 tool_calls 항목을 (function_name, args)로 변환한다."""
    fn: Any = tc.get("function") or tc.get("name")
    args: Any = tc.get("args") or tc.get("arguments") or {}

    if isinstance(fn, dict):
        args = fn.get("args") or fn.get("arguments") or args
        fn = fn.get("name") or fn.get("function")

    if isinstance(args, str):
        try:
            parsed_args = json.loads(args)
            args = parsed_args if isinstance(parsed_args, dict) else {}
        except json.JSONDecodeError:
            args = {}

    return (str(fn).strip() if fn else None), args if isinstance(args, dict) else {}


def _scan_one_json_block(text: str, start: int) -> tuple[str, int]:
    """``start`` 위치의 여는 괄호부터 완결/불완전 JSON 조각과 스캔이 끝난 위치를 반환한다.

    완결된 블록을 찾으면 (조각, 그 블록 바로 다음 인덱스)를 반환한다.
    끝까지 닫히지 않으면 절단-복구를 적용한 (조각, len(text))를 반환한다
    (``_extract_first_json_block``의 기존 절단 복구 로직과 동일).
    """
    closer = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_string = False
    escape_next = False

    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(closer[ch])
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
                if not stack:
                    return text[start : i + 1], i + 1

    # 불완전한 JSON 보정: 미완 문자열을 닫고 잔여 쉼표 제거 후 남은 닫는 괄호를 역순으로 추가
    snippet = text[start:]
    if in_string:
        snippet += '"'
    if stack:
        snippet = snippet.rstrip().rstrip(",")
        snippet += "".join(reversed(stack))
    return snippet, len(text)


def _extract_first_json_block(text: str) -> str:
    """검증 가능한 첫 번째 완전한 JSON 객체({...}) 또는 배열([...])을 추출한다.

    닫는 괄호가 누락된 불완전한 JSON(VLM이 응답을 중간에 끊거나 ']'/'}'를 빠뜨린 경우)은
    중첩 스택을 추적해 누락된 닫는 괄호를 보정한 뒤 반환한다.

    프롬프트가 응답 본문에 ``[도구명]`` 같은 대괄호 표기를 요구하는 경우, 그 대괄호가
    우연히 완결된 배열처럼 보여 실제 JSON 페이로드보다 먼저 매칭될 수 있다
    (예: ``"[rag_search]를 사용합니다.\\n{...}"``). 이를 막기 위해 완결된 블록을 찾을
    때마다 ``json.loads``로 실제 유효성을 검증하고, 실패하면 다음 ``{``/``[`` 후보로
    넘어간다. 유효한 후보를 끝내 찾지 못하면 첫 후보의 절단-복구 결과를 반환한다
    (기존 동작과 동일한 폴백).
    """
    search_from = 0
    fallback: str | None = None

    while True:
        start_obj = text.find("{", search_from)
        start_arr = text.find("[", search_from)

        if start_obj == -1 and start_arr == -1:
            return fallback if fallback is not None else text

        if start_obj != -1 and (start_arr == -1 or start_obj < start_arr):
            start = start_obj
        else:
            start = start_arr

        snippet, next_pos = _scan_one_json_block(text, start)

        if fallback is None:
            fallback = snippet

        try:
            json.loads(snippet)
            return snippet
        except json.JSONDecodeError:
            if next_pos <= start:
                # 안전장치: 진행이 없으면 무한 루프를 피하고 폴백으로 종료.
                return fallback
            search_from = next_pos
            continue


def _extract_first_json_object(text: str) -> str:
    """첫 번째 완전한 JSON 객체({...})만 추출한다. (하위 호환성 유지)"""
    # { 가 먼저 나오도록 강제하거나, 단순히 범용 함수 호출
    return _extract_first_json_block(text)


def extract_objects(text: str) -> list:
    """VLM 응답 텍스트에서 'OBJECTS:' 뒤의 객체 리스트를 견고하게 추출한다.

    1차: OBJECTS 키워드 이후 JSON 블록을 파싱(불완전 괄호 자동 보정).
    2차(백업): json.loads 실패 시 'name'/'label' 키를 가진 개별 {...} 조각을 정규식으로 복구.
    어떤 경우든 dict 리스트를 반환하며, 완전 실패 시 빈 리스트.
    """
    if not text:
        return []

    start_idx = text.find("OBJECTS:")
    search = text[start_idx + 8 :] if start_idx != -1 else text

    block = _extract_first_json_block(search)
    if "{" in block or "[" in block:
        try:
            parsed = json.loads(block)
            if isinstance(parsed, dict):
                return [parsed]
            if isinstance(parsed, list):
                return [o for o in parsed if isinstance(o, dict)]
        except Exception as e:
            logger.warning("Failed to parse OBJECTS JSON, trying regex fallback: %s", e)

    objs = []
    for m in re.finditer(r"\{[^{}]*\}", search):
        try:
            o = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(o, dict) and (o.get("name") or o.get("label")):
            objs.append(o)
    if not objs:
        logger.warning("Failed to extract OBJECTS (source excerpt: %s)", search[:120])
    return objs


def parse_llm_response(llm_response: str) -> dict[str, Any]:
    text = llm_response.strip()

    # think=true 모드에서 LLM이 <think>...</think> 태그를 생성하는 경우,
    # 태그 내부의 JSON 조각이 잘못 추출되는 것을 방지하기 위해 think 블록을
    # 먼저 제거한다. (Ollama 브릿지에서도 제거하지만, 다른 LLM 백엔드나
    # 직접 호출 시에도 안전하도록 여기서도 처리한다.)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    # 토큰 한도로 닫는 태그가 잘린 경우, 이후는 전부 사고 과정이므로 제거한다.
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL).strip()

    # 코드 블록이 있으면 내부 텍스트만 먼저 꺼낸다.
    # \{.*?\} 비탐욕 패턴은 중첩 JSON에서 첫 번째 '}'에서 끊기므로 사용하지 않는다.
    code_block_pattern = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
    match = code_block_pattern.search(text)
    if match:
        text = match.group(1)

    # 중첩 깊이를 추적해 완전한 JSON 객체만 추출한다.
    text = _extract_first_json_object(text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # JSON 파싱에 실패한 모든 경우(잘못된 형식, 기호만 있는 경우 등)
        # LLM의 원래 응답 전체를 response 필드로 래핑하여 에이전트가 답변하게 함.
        # 내부 신호 키 _parse_failed는 parse_llm_plan이 소비해 LLMPlanResult.parse_failed로
        # 전달한다 — planner가 "의도된 최종 답변"과 "형식 오류로 강등된 응답"을 구분해
        # 후자만 형식 교정 재시도 대상에 포함시킬 수 있게 하기 위함이다.
        # 원문 로깅은 여기서 하지 않는다: 유일한 호출부인 planner.py가 재시도 횟수/
        # 라운드 정보와 함께 300자 길이로 일관되게 로깅한다(중복 로깅 방지).
        original_text = llm_response.strip()
        if original_text:
            return {"skill": None, "response": original_text, "_parse_failed": True}

        # 텍스트 자체가 없는 경우에만 에러 발생
        logger.error("JSON parsing failed and response is empty! Target text:\n%s", text)
        raise ValueError("Empty or invalid LLM response") from None

    if not isinstance(parsed, dict):
        if isinstance(parsed, list):
            # 최상위가 배열인 경우 skills 체이닝을 그대로 낸 것일 수 있다
            # (예: [{"skill": "a", ...}, {"skill": "b", ...}]). 새 파싱 경로를
            # 만들지 않고 기존 "skills" 분기가 그대로 소화하도록 감싼다.
            return {"skills": parsed}
        logger.warning(
            "LLM response JSON top-level type is %s, not object — wrapping as response",
            type(parsed).__name__,
        )
        return {"skill": None, "response": text}

    if not any(k in parsed for k in ["skill", "skills", "response", "tool_calls"]):
        # 모델이 질문 내용을 그대로 JSON으로 답한 경우(예: {"topics": [...]})가
        # 여기로 온다. 원문 JSON을 response 로 승격하면 그대로 사용자 답변이 되므로,
        # 형식 오류로 표시해 형식 교정 재시도 대상에 넣고 response 에는 사람이 읽을 수
        # 있는 텍스트만 담는다(없으면 빈 문자열 → 호출부가 폴백 결정).
        return {
            "skill": None,
            "response": render_structured_answer(parsed),
            "_parse_failed": True,
        }
    return parsed


@traceable(
    run_type="chain",
    name="parse_llm_plan",
    process_inputs=trace_process_inputs,
    process_outputs=trace_process_outputs,
)
def parse_llm_plan(llm_response: str) -> "LLMPlanResult":
    """LLM 응답을 파싱하여 정형화된 LLMPlanResult 데이터 구조체로 반환한다."""
    from robo_claw_agent.types import LLMPlanResult, SkillChainItem

    parsed_dict = parse_llm_response(llm_response)
    parse_failed = bool(parsed_dict.pop("_parse_failed", False))
    skills: list[SkillChainItem] = []

    raw_skills = parsed_dict.get("skills")
    if isinstance(raw_skills, list) and raw_skills:
        skipped = 0
        for item in raw_skills:
            if isinstance(item, dict) and item.get("skill"):
                params = item.get("params")
                skill_name, skill_params = _normalize_skill_call(
                    str(item["skill"]).strip(), params if isinstance(params, dict) else {}
                )
                skills.append(
                    SkillChainItem(
                        skill=skill_name,
                        params=skill_params,
                    )
                )
            else:
                skipped += 1
        if skipped:
            logger.warning(
                "Skipped %d invalid entr%s in 'skills' array (missing 'skill' key or not an object)",
                skipped,
                "y" if skipped == 1 else "ies",
            )
    elif parsed_dict.get("skill"):
        params = parsed_dict.get("params")
        skill_name, skill_params = _normalize_skill_call(
            str(parsed_dict["skill"]).strip(), params if isinstance(params, dict) else {}
        )
        skills.append(
            SkillChainItem(
                skill=skill_name,
                params=skill_params,
            )
        )

    # Fallback: LLM이 OpenAI 스타일 tool_calls 형식으로 응답한 경우 매핑.
    # 소형 LLM이 시스템 프롬프트의 형식 지시를 무시하고 학습된 tool_calls
    # 포맷으로 회귀하는 경우가 있다. tool_calls[].function → skill,
    # tool_calls[].args → params 로 변환한다.
    if not skills:
        tool_calls = parsed_dict.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn, args = _coerce_tool_call(tc)
                    if fn:
                        skill_name, skill_params = _normalize_skill_call(fn, args)
                        skills.append(
                            SkillChainItem(
                                skill=skill_name,
                                params=skill_params,
                            )
                        )
            if skills:
                logger.warning(
                    "LLM returned tool_calls format instead of skill/skills — "
                    "mapped %d tool_call(s) to skill chain",
                    len(skills),
                )

    if not skills:
        resp_text = str(parsed_dict.get("response") or "")
        mentioned = re.findall(r"\[([a-z0-9_]{3,30})\]", resp_text, re.IGNORECASE)
        for s_name in mentioned:
            s_clean = s_name.strip().lower()
            if s_clean in (
                "capture_map",
                "get_map_visual",
                "annotate_map",
                "capture_camera_image",
                "describe_surroundings",
                "get_status",
                "find_reachable_places",
            ):
                logger.info(f"Rescuing skill [{s_clean}] mentioned in LLM text response")
                skills.append(SkillChainItem(skill=s_clean, params={}))
                break

    # response 는 항상 사람이 읽을 수 있는 텍스트로 정규화한다. 모델이 response 값에
    # 객체/배열을 넣으면 str() 이 파이썬 repr 로 노출되므로 구조체를 평탄화한다.
    response = render_structured_answer(parsed_dict.get("response"))
    explicit_final = parsed_dict.get("final")
    if isinstance(explicit_final, bool):
        final = explicit_final
    else:
        final = bool(not skills and response)

    return LLMPlanResult(
        skills=skills,
        response=response,
        reason=str(parsed_dict.get("reason") or ""),
        final=final,
        parse_failed=parse_failed,
    )
