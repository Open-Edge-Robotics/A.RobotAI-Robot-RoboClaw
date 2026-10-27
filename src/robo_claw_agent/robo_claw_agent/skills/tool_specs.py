"""스킬 목록을 LLM 네이티브 도구(tool) 스펙으로 변환한다.

스킬의 ``input_schema``(JSON Schema) 가 호출 계약의 **유일한 원본**이다.
프롬프트에 스킬 목록을 평문으로 붙이거나 별도의 어댑터 함수를 만들어
docstring 으로 스키마를 재정의하지 않는다 — 어댑터는 두 번째 원본이 되어
 drifting 을 만든다(실측: 함수에서 자동 생성한 스키마는 손으로 만든 스키마보다
 정확도도 낮고 페이로드도 컸다).

출력 형식은 OpenAI Chat Completions / Ollama ``/api/chat`` 가 공통으로 쓰는
``{"type": "function", "function": {"name", "description", "parameters"}}`` 다.
Anthropic 은 ``llm_bridge.anthropic.to_anthropic_tools`` 가 이 형식을 변환한다.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# per-property ``description`` 을 남길지 여부 기본값.
#
# 실측(qwen3.5:9b, 43개 한국어 명령 × 3회 반복, temperature 0):
#   - 유지: 도구 선택 74%, 프롬프트 19,565 토큰
#   - 제거: 도구 선택 76%, 프롬프트 18,669 토큰
# 제거 쪽이 더 정확하고 더 싸다. 스킬 ``description`` 에 이미
# "파라미터: target_object(str, 접근할 객체명, 필수), ..." 형태의 서술이
# 포함되어 있어(34/89 스킬) per-property 설명이 중복되기 때문이다.
DEFAULT_INCLUDE_PROPERTY_DESCRIPTION = False

# OpenAI 호환 API(vLLM, Ollama proxy, Azure 등)는 tool ``parameters`` 의 최상위가
# ``{"type": "object"}`` 이고 ``oneOf``/``anyOf``/``allOf``/``enum``/``const``/``not``
# 을 갖지 않아야 한다. 위반하면 요청 자체가 400으로 거부된다.
#
# 실측(cloid 2호기, 2026-10-27): 스킬 input_schema 가 "어떤 인자가 필수인가"를
# ``oneOf: [{"required": [...]}, ...]`` 로 표현한 face_direction 등에서
# ``Invalid schema for function 'face_direction': schema must have type 'object'
# and not have 'oneOf'/'anyOf'/'allOf'/'enum'/'const'/'not' at the top level.``
# 가 발생해 계획 수립이 전부 실패했다(해석 오류로 사용자에게 전달됨).
#
# 대안 조건은 런타임 검증(``BaseSkill.validate_input_schema``)이 원본 schema 로
# 계속 강제하므로, 전송 schema 에서는 이 키만 제거한다.
_UNSUPPORTED_TOP_LEVEL_KEYS = ("oneOf", "anyOf", "allOf", "enum", "const", "not")


def _clean_parameters(
    schema: Any,
    *,
    include_property_description: bool,
) -> dict[str, Any]:
    """스킬 ``input_schema`` 를 tool ``parameters`` 로 정규화한다.

    - ``title`` 은 툴 스키마에서 불필요한 잡음이라 제거한다.
    - per-property ``description`` 은 기본적으로 제거한다(위 실측 근거).
    - ``required`` / ``default`` / ``enum`` / ``items`` /
      ``additionalProperties`` / ``minimum`` / ``maximum`` 은 그대로 둔다.
      런타임 스키마 검증과 서버 렌더링이 이 정보에 의존한다.
    - 반면 최상위 ``oneOf``/``anyOf``/``allOf``/``enum``/``const``/``not`` 은
      제거한다. OpenAI 호환 서버가 이 조합을 400으로 거부하기 때문이다
      (근거는 ``_UNSUPPORTED_TOP_LEVEL_KEYS`` 주석). 대안 조건은 스킬 원본
      schema 에 남아 런타임 검증에서 강제된다.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    try:
        parameters = json.loads(json.dumps(schema, default=str))
    except (TypeError, ValueError):
        parameters = dict(schema)

    parameters.pop("title", None)
    parameters.setdefault("type", "object")

    dropped = [key for key in _UNSUPPORTED_TOP_LEVEL_KEYS if key in parameters]
    for key in dropped:
        parameters.pop(key, None)
    if dropped:
        logger.debug(
            "tool schema: dropped unsupported top-level %s from parameters",
            ", ".join(dropped),
        )

    properties = parameters.get("properties")
    if isinstance(properties, dict) and not include_property_description:
        for spec in properties.values():
            if isinstance(spec, dict):
                spec.pop("description", None)
    return parameters


def build_tool_specs(
    skills: list[dict[str, Any]],
    *,
    include_property_description: bool = DEFAULT_INCLUDE_PROPERTY_DESCRIPTION,
) -> list[dict[str, Any]]:
    """스킬 목록을 tool 스펙 목록으로 변환한다.

    Args:
        skills: ``SkillManager.list_skills()`` 가 돌려준 스킬 dict 목록.
        include_property_description: per-property ``description`` 유지 여부.

    Returns:
        OpenAI/Ollama 호환 tool 스펙 리스트. 이름이 없는 스킬은 건너뛴다.
    """
    specs: list[dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        name = str(skill.get("name") or "").strip()
        if not name:
            continue
        description = str(skill.get("description") or "").strip()
        parameters = _clean_parameters(
            skill.get("input_schema"),
            include_property_description=include_property_description,
        )
        specs.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": parameters,
                },
            }
        )
    return specs


def tool_spec_names(specs: list[dict[str, Any]]) -> list[str]:
    """tool 스펙에서 도구 이름만 뽑는다(로깅/디버깅용)."""
    names: list[str] = []
    for spec in specs:
        fn = spec.get("function") if isinstance(spec, dict) else None
        if isinstance(fn, dict) and fn.get("name"):
            names.append(str(fn["name"]))
    return names
