from robo_claw_agent.agent_node.utils import parse_llm_plan


def test_parse_llm_plan_rescues_simple_tool_calls_navigation_alias():
    plan = parse_llm_plan(
        '{"tool_calls": [{"function": "navigate", "args": {"destination": "거실"}}]}'
    )

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "navigate_to"
    assert plan.skills[0].params == {"target_name": "거실"}


def test_parse_llm_plan_rescues_openai_tool_calls_shape():
    plan = parse_llm_plan(
        '{"tool_calls": [{"function": {"name": "navigate", "arguments": "{\\"destination\\": \\"메인룸\\"}"}}]}'
    )

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "navigate_to"
    assert plan.skills[0].params == {"target_name": "메인룸"}


def test_parse_llm_plan_normalizes_direct_skill_alias():
    plan = parse_llm_plan(
        '{"skill": "navigate", "params": {"destination": "메인룸"}, "reason": "이동"}'
    )

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "navigate_to"
    assert plan.skills[0].params == {"target_name": "메인룸"}


def test_parse_llm_plan_maps_get_location_to_get_location():
    plan = parse_llm_plan(
        '{"tool_calls": [{"function": "get_location", "args": {"location": "메인룸"}}]}'
    )

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "get_location"
    assert plan.skills[0].params == {"location_name": "메인룸"}


def test_parse_llm_plan_maps_get_coordinates_to_get_location():
    plan = parse_llm_plan(
        '{"tool_calls": [{"function": "get_coordinates", "args": {"location": "메인룸"}}]}'
    )

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "get_location"
    assert plan.skills[0].params == {"location_name": "메인룸"}


def test_parse_llm_plan_maps_current_location_to_get_status():
    plan = parse_llm_plan('{"skill": "get_current_location", "params": {}}')

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "get_status"
    assert plan.skills[0].params == {}


def test_parse_llm_plan_maps_date_alias_to_get_datetime():
    plan = parse_llm_plan('{"tool_calls": [{"function": "get_current_date", "args": {}}]}')

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "get_datetime"
    assert plan.skills[0].params == {}


def test_parse_llm_plan_direct_response_is_final():
    plan = parse_llm_plan('{"response": "안녕하세요! 만나서 반갑습니다."}')

    assert plan.skills == []
    assert plan.response == "안녕하세요! 만나서 반갑습니다."
    assert plan.final is True


def test_parse_llm_plan_unknown_json_is_not_leaked_as_answer():
    """알 수 없는 키만 가진 JSON은 원문을 답변으로 승격하지 않는다.

    모델이 질문 내용을 그대로 JSON으로 답하면(`{"topics": [...]}`) 그 JSON 원문이
    사용자 답변이 되던 회귀를 막는다. 형식 오류로 표시해 형식 교정 재시도 대상에
    넣고, response 에는 사람이 읽을 수 있는 텍스트만 넣는다.
    """
    plan = parse_llm_plan('{"error": "사용할 수 없는 함수 호출입니다."}')

    assert plan.skills == []
    assert plan.parse_failed is True
    assert "error" in plan.response
    assert "{" not in plan.response and "}" not in plan.response


def test_parse_llm_plan_structured_answer_json_is_flattened():
    plan = parse_llm_plan('{"topics": ["/cmd_vel", "/scan"], "count": 2}')

    assert plan.skills == []
    assert plan.parse_failed is True
    assert plan.response == "topics: /cmd_vel, /scan; count: 2"


def test_parse_llm_plan_structured_response_object_is_flattened():
    """response 가 객체여도 파이썬 repr 로 노출하지 않는다."""
    plan = parse_llm_plan('{"skill": null, "response": {"topics": ["/a", "/b"]}}')

    assert plan.skills == []
    assert plan.final is True
    assert plan.response == "topics: /a, /b"
    assert "'" not in plan.response


def test_parse_llm_plan_response_with_embedded_text_key_is_used():
    plan = parse_llm_plan('{"skill": null, "response": {"answer": "토픽은 2개입니다."}}')

    assert plan.response == "토픽은 2개입니다."
    assert plan.parse_failed is False


# ── skills 배열 체이닝 — 문서화된 주 형식인데 기존 커버리지가 전혀 없었다 ──


def test_parse_llm_plan_skills_chaining_basic():
    plan = parse_llm_plan(
        '{"skills": [{"skill": "a", "params": {}}, {"skill": "b", "params": {"x": 1}}], '
        '"reason": "체인"}'
    )

    assert [s.skill for s in plan.skills] == ["a", "b"]
    assert plan.skills[1].params == {"x": 1}
    assert plan.reason == "체인"
    assert plan.final is False


def test_parse_llm_plan_skills_array_with_invalid_entry_is_skipped():
    plan = parse_llm_plan('{"skills": [{"name": "x"}, {"skill": "get_status", "params": {}}]}')

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "get_status"


# ── 산문 전용 응답 — 재시도 로직을 우회하던 가장 흔한 실패 모드 ──────────


def test_parse_llm_plan_prose_only_wraps_as_final_response():
    plan = parse_llm_plan("거실로 이동하겠습니다. 잠시만 기다려 주세요.")

    assert plan.skills == []
    assert plan.final is True
    assert plan.parse_failed is True
    assert plan.response == "거실로 이동하겠습니다. 잠시만 기다려 주세요."


def test_parse_llm_plan_explicit_final_response_is_not_parse_failed():
    """{"skill": null, "response": ...} 같은 정상 최종 답변은 parse_failed가 아니어야 한다."""
    plan = parse_llm_plan('{"skill": null, "response": "완료했습니다."}')

    assert plan.final is True
    assert plan.parse_failed is False


# ── 대괄호 프리앰블 — 프롬프트가 [도구명] 표기를 요구하는 것과 충돌 ───────


def test_parse_llm_plan_bracket_preamble_recovers_real_json():
    plan = parse_llm_plan(
        '[rag_search]를 사용합니다.\n{"skill":"rag_search","params":{"query":"주방 좌표"}}'
    )

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "rag_search"
    assert plan.skills[0].params == {"query": "주방 좌표"}
    assert plan.parse_failed is False


# ── 최상위 JSON 배열/스칼라 ────────────────────────────────────────────────


def test_parse_llm_plan_top_level_array_treated_as_skills():
    plan = parse_llm_plan('[{"skill": "navigate_to", "params": {"target_name": "주방"}}]')

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "navigate_to"
    assert plan.skills[0].params == {"target_name": "주방"}


def test_parse_llm_plan_top_level_scalar_does_not_raise():
    plan = parse_llm_plan("42")

    assert plan.skills == []
    assert plan.response == "42"


# ── 코드 펜스 / think 블록 ─────────────────────────────────────────────────


def test_parse_llm_plan_code_fence_json():
    plan = parse_llm_plan('```json\n{"skill": "get_status", "params": {}}\n```')

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "get_status"


def test_parse_llm_plan_think_block_stripped():
    plan = parse_llm_plan(
        '<think>사용자가 상태를 물어봤다</think>{"skill": "get_status", "params": {}}'
    )

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "get_status"


def test_parse_llm_plan_unterminated_think_block():
    plan = parse_llm_plan("<think>추론 중이고 토큰 한도로 잘렸습니다")

    assert plan.skills == []
    assert plan.parse_failed is True


# ── 절단(truncation) 자동 복구 ─────────────────────────────────────────────


def test_parse_llm_plan_truncated_json_repaired():
    plan = parse_llm_plan('{"skill": "navigate_to", "params": {"target_name": "거실')

    assert len(plan.skills) == 1
    assert plan.skills[0].skill == "navigate_to"
    assert plan.skills[0].params == {"target_name": "거실"}
    assert plan.parse_failed is False


def test_parse_llm_plan_trailing_comma_in_complete_block_fails_gracefully():
    """완결된 블록 안의 후행 쉼표는 보수적 정책상 복구 대상이 아니다 — parse_failed로 드러난다."""
    plan = parse_llm_plan('{"skill": "get_status", "params": {},}')

    assert plan.skills == []
    assert plan.parse_failed is True


# ── 명시적 final: false ────────────────────────────────────────────────────


def test_parse_llm_plan_explicit_final_false_with_response():
    plan = parse_llm_plan('{"response": "일부 진행 중", "final": false}')

    assert plan.final is False
    assert plan.response == "일부 진행 중"
