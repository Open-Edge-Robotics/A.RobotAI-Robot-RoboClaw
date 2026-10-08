"""사용자에게 전달되는 최종 답변 텍스트 정리.

LLM 응답이나 스킬 결과가 JSON 원문·파이썬 repr 상태로 사용자에게 노출되는 것을
막는다. 에이전트는 ``response_format={"type": "json_object"}`` 로 LLM을 호출하므로,
모델이 정해진 envelope 를 벗어나 질문 내용을 그대로 JSON으로 답하면 그 JSON이
사용자 답변이 되어버리는 실패 모드가 존재한다. 이 모듈은 그 경계에서 텍스트를
사람이 읽을 수 있는 형태로 정리한다.

rclpy·ROS generated message에 의존하지 않는 순수 모듈이다(AGENTS.md §3).
"""

from __future__ import annotations

import json
import re
from typing import Any

# 결과 데이터에서 사용자에게 보여줄 필요가 없는 운영 필드.
_EXCLUDED_RESULT_KEYS = frozenset(
    {
        "success",
        "status",
        "failure_reason",
        "recoverable",
        "error_type",
        "schema_error",
        "precondition_error",
        "preconditions",
        "postconditions",
        "duration_sec",
        "returncode",
        "internal",
        "raw",
    }
)


def _is_binary_key(key: Any) -> bool:
    """이미지 등 base64 인코딩 데이터 키(예: ``image_base64``)인지.

    사람이 읽을 수 없고 수 MB 에 이르므로 텍스트로 풀면 사용자 메시지와 다음 LLM 프롬프트가
    폭주한다(실측: 카메라 이미지 1장이 다음 단계 프롬프트를 267만 토큰으로 만들었다).
    ``utils._omit_base64_data`` 와 같은 기준(키 이름에 ``base64`` 포함)을 쓴다.
    """
    return isinstance(key, str) and "base64" in key.lower()


# 답변 본문으로 우선 사용할 텍스트 키(우선순위 순).
_TEXT_ANSWER_KEYS = (
    "response",
    "answer",
    "message",
    "text",
    "summary",
    "content",
    "result",
)

# 스킬 계획 envelope 식별 키. 이 키가 있고 본문이 없으면 사람이 읽을 내용이 없는
# 계획 JSON이므로 원문을 노출하지 않는다.
_PLAN_ENVELOPE_KEYS = ("skill", "skills", "tool_calls")

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*", re.DOTALL)
_FULL_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

# 구조체를 텍스트로 바꿀 수 없을 때 사용자에게 보여줄 안내 문구.
UNRENDERABLE_ANSWER = "요청한 정보를 정리하지 못했습니다. 다시 요청해 주세요."


def strip_think(text: str) -> str:
    """모델 사고 과정(think) 블록을 제거한다(닫는 태그가 잘린 경우 포함)."""
    if not text:
        return text
    stripped = _THINK_BLOCK_RE.sub("", text)
    return _THINK_OPEN_RE.sub("", stripped)


def _strip_outer_code_fence(text: str) -> str:
    """텍스트 전체가 코드 펜스로 감싸진 경우에만 내부를 꺼낸다.

    본문 중간에 코드 블록이 있는 정상 답변은 그대로 둔다.
    """
    match = _FULL_CODE_FENCE_RE.match(text)
    if match:
        return match.group(1)
    return text


def looks_like_json(text: str) -> bool:
    stripped = (text or "").strip()
    return stripped.startswith("{") or stripped.startswith("[")


def loads_lenient(text: str) -> Any | None:
    """후행 쉼표 정도의 사소한 오류를 보정해 JSON을 파싱한다. 실패 시 None."""
    candidate = (text or "").strip()
    if not candidate:
        return None
    for attempt in (candidate, _TRAILING_COMMA_RE.sub(r"\1", candidate)):
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def flatten_to_text(value: Any, *, exclude_keys: frozenset[str] = frozenset()) -> str:
    """dict/list/스칼라를 사람이 읽을 수 있는 한 줄 텍스트로 평탄화한다.

    파이썬 repr 로 노출되는 것을 막기 위한 마지막 수단이다. 노출이 무의미한
    운영 필드(``exclude_keys``)는 건너뛴다.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if looks_like_json(stripped):
            parsed = loads_lenient(stripped)
            if parsed is not None:
                return flatten_to_text(parsed, exclude_keys=exclude_keys)
        return stripped
    if isinstance(value, (list, tuple)):
        parts = [flatten_to_text(item, exclude_keys=exclude_keys) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            if str(key) in exclude_keys or _is_binary_key(key):
                continue
            rendered = flatten_to_text(item, exclude_keys=exclude_keys)
            if rendered:
                parts.append(f"{key}: {rendered}")
        return "; ".join(parts)
    return str(value)


def extract_text_answer(payload: Any) -> str:
    """payload 에서 사용자에게 보여줄 텍스트를 뽑는다. 없으면 빈 문자열.

    ``response``/``answer``/``message`` 등 본문 키를 우선 사용하고, 구조체만
    있으면 빈 문자열을 반환해 호출부가 평탄화 여부를 판단하게 한다.
    """
    if payload is None:
        return ""
    if isinstance(payload, str):
        stripped = payload.strip()
        if looks_like_json(stripped):
            parsed = loads_lenient(stripped)
            if parsed is not None:
                return extract_text_answer(parsed)
        return stripped
    if isinstance(payload, bool):
        return "true" if payload else "false"
    if isinstance(payload, (int, float)):
        return str(payload)
    if isinstance(payload, dict):
        for key in _TEXT_ANSWER_KEYS:
            if key in payload:
                text = extract_text_answer(payload[key])
                if text:
                    return text
        return ""
    if isinstance(payload, (list, tuple)):
        return flatten_to_text(payload)
    return str(payload)


def render_structured_answer(payload: Any) -> str:
    """구조체 payload 에서 답변 텍스트를 만든다.

    텍스트 키(``response``/``answer``/``message`` 등)를 우선 사용하고, 없으면
    사람이 읽을 수 있는 ``key: value`` 텍스트로 평탄화한다. 둘 다 불가능하면
    빈 문자열을 반환해 호출부가 폴백을 결정하게 한다.
    """
    answer = extract_text_answer(payload)
    if answer:
        return answer
    return flatten_to_text(payload, exclude_keys=_EXCLUDED_RESULT_KEYS)


def _render_payload(payload: Any) -> str:
    return render_structured_answer(payload) or UNRENDERABLE_ANSWER


def sanitize_final_answer(text: Any) -> str:
    """최종 답변 텍스트를 정리한다.

    - 모델 사고 과정(think) 블록과 전체를 감싼 코드 펜스를 제거한다.
    - 정상 텍스트는 그대로 둔다.
    - JSON envelope 이면 ``response`` 본문을 꺼낸다.
    - 본문 없는 계획 JSON이면 원문 대신 안내 문구로 대체한다.
    - 그 밖의 구조체 JSON은 사람이 읽을 수 있는 텍스트로 평탄화한다.
    """
    raw = str(text or "")
    if not raw.strip():
        return ""

    candidate = _strip_outer_code_fence(strip_think(raw)).strip()
    if not candidate:
        return ""
    if not looks_like_json(candidate):
        return candidate

    parsed = loads_lenient(candidate)
    if parsed is None:
        return UNRENDERABLE_ANSWER
    if isinstance(parsed, dict):
        if "response" in parsed:
            return _render_payload(parsed["response"])
        if any(key in parsed for key in _PLAN_ENVELOPE_KEYS):
            return UNRENDERABLE_ANSWER
    return _render_payload(parsed)


def render_result_data(data: Any) -> str:
    """스킬 ``result_data`` 를 사용자에게 보여줄 텍스트로 렌더링한다.

    요약 라운드가 실패했을 때 플래너가 쓰는 결정론적 폴백용이다. 운영 필드는
    제외하고, 결과가 비어 있으면 빈 문자열을 반환한다.
    """
    if isinstance(data, dict):
        if not data:
            return ""
        return flatten_to_text(data, exclude_keys=_EXCLUDED_RESULT_KEYS)
    return flatten_to_text(data)
