"""최종 답변 텍스트 정리(answer.py) 단위 테스트.

사용자에게 전달되는 최종 답변에 JSON 원문·파이썬 repr·think 잔해가 그대로
노출되던 회귀를 막는다. rclpy 의존이 없는 순수 모듈이라 fast loop에서 실행된다.
"""

import pytest

pytestmark = pytest.mark.unit

from robo_claw_agent.answer import (
    MAX_FINAL_ANSWER_CHARS,
    UNRENDERABLE_ANSWER,
    extract_text_answer,
    render_result_data,
    sanitize_final_answer,
)

# 모델 사고 과정 태그를 리터럴로 쓰지 않고 구성한다.
_THINK_OPEN = chr(60) + "think" + chr(62)
_THINK_CLOSE = chr(60) + "/think" + chr(62)

# ── 정상 텍스트는 변형하지 않는다 ─────────────────────────────────────────


def test_plain_answer_is_unchanged():
    assert sanitize_final_answer("도착했습니다.") == "도착했습니다."


def test_answer_with_inner_code_fence_is_unchanged():
    text = "설정은 아래와 같습니다.\n```yaml\nkey: value\n```"
    assert sanitize_final_answer(text) == text


# ── 정상 envelope 는 response 본문만 남긴다 ──────────────────────────────


def test_skill_null_response_envelope_is_unwrapped():
    assert sanitize_final_answer(
        '{"skill": null, "response": "수신 가능한 토픽은 3개입니다."}'
    ) == ("수신 가능한 토픽은 3개입니다.")


def test_response_only_envelope_is_unwrapped():
    assert sanitize_final_answer('{"response": "완료했습니다."}') == "완료했습니다."


# ── 계획 envelope(사용자 본문 없음)는 원문을 노출하지 않는다 ────────────


def test_plan_envelope_without_response_does_not_leak_json():
    text = '{"skill": "ros_command", "params": {"command": "ros2 topic list"}, "reason": "조회"}'
    assert sanitize_final_answer(text) == UNRENDERABLE_ANSWER


def test_tool_calls_envelope_does_not_leak_json():
    text = '{"tool_calls": [{"function": "get_status", "args": {}}]}'
    assert sanitize_final_answer(text) == UNRENDERABLE_ANSWER


# ── 구조체 JSON 은 사람이 읽을 수 있는 텍스트로 평탄화한다 ──────────────


def test_unknown_key_json_is_flattened_to_readable_text():
    assert sanitize_final_answer('{"topics": ["/cmd_vel", "/scan"], "count": 2}') == (
        "topics: /cmd_vel, /scan; count: 2"
    )


def test_nested_response_object_is_flattened():
    assert sanitize_final_answer('{"skill": null, "response": {"topics": ["/a", "/b"]}}') == (
        "topics: /a, /b"
    )


def test_python_repr_like_payload_is_not_left_as_repr():
    """dict/list 는 파이썬 repr 로 노출되지 않아야 한다."""
    result = sanitize_final_answer('{"items": ["a", "b"]}')
    assert "'" not in result
    assert result == "items: a, b"


def test_trailing_comma_json_is_repaired_instead_of_leaked():
    text = '{"skill": "ros_command", "params": {"command": "ros2 topic list",}, "reason": "x"}'
    result = sanitize_final_answer(text)
    assert result == UNRENDERABLE_ANSWER
    assert "{" not in result


def test_broken_json_is_replaced_with_unrenderable_notice():
    assert sanitize_final_answer('{"topics": ["/a", ') == UNRENDERABLE_ANSWER


# ── think 블록/코드펜스 정리 ─────────────────────────────────────────────


def test_think_block_is_stripped_before_sanitizing():
    text = (
        _THINK_OPEN
        + "사용자가 목록을 물었다"
        + _THINK_CLOSE
        + '{"skill": null, "response": "토픽은 2개입니다."}'
    )
    assert sanitize_final_answer(text) == "토픽은 2개입니다."


def test_fenced_json_envelope_is_unwrapped():
    text = '```json\n{"skill": null, "response": "완료했습니다."}\n```'
    assert sanitize_final_answer(text) == "완료했습니다."


# ── 빈 입력 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "   ", None])
def test_empty_input_returns_empty_string(value):
    assert sanitize_final_answer(value) == ""


# ── extract_text_answer ────────────────────────────────────────────────


def test_extract_text_answer_prefers_response_key():
    assert extract_text_answer({"response": "본문", "message": "다른값"}) == "본문"


def test_extract_text_answer_returns_empty_for_plan_envelope():
    assert extract_text_answer({"skill": "get_status", "params": {}}) == ""


def test_extract_text_answer_parses_json_string_value():
    assert extract_text_answer('{"answer": "안녕"}') == "안녕"


# ── render_result_data (플래너 결정론적 폴백용) ──────────────────────────


def test_render_result_data_renders_ros_command_output():
    rendered = render_result_data(
        {
            "command": "ros2 topic list",
            "stdout": "/cmd_vel\n/scan",
            "stderr": "",
            "returncode": 0,
        }
    )
    assert "stdout: /cmd_vel" in rendered
    assert "command: ros2 topic list" in rendered
    # 운영 필드는 노출하지 않는다
    assert "returncode" not in rendered
    assert "success" not in rendered


def test_render_result_data_skips_empty_and_internal_fields():
    assert render_result_data({"success": True, "status": "completed", "message": ""}) == ""


def test_render_result_data_handles_non_dict():
    assert render_result_data(None) == ""


def test_sanitize_final_answer_caps_excessive_length():
    huge_text = "이것은 매우 긴 답변입니다. " * 500  # 약 8,000자
    sanitized = sanitize_final_answer(huge_text)
    assert len(sanitized) <= MAX_FINAL_ANSWER_CHARS + 100
    assert "생략" in sanitized


def test_render_result_data_caps_excessive_length():
    huge_data = {"key": "매우 긴 결과 내용입니다. " * 500}
    rendered = render_result_data(huge_data)
    assert len(rendered) <= MAX_FINAL_ANSWER_CHARS + 100
    assert "생략" in rendered
