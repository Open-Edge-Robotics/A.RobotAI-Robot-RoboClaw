"""결과 데이터의 이미지 base64 가 사용자 답변·LLM 프롬프트에 섞이지 않는지 검증한다.

실기 회귀(2026-10-08): ``capture_camera_image`` 결과의 ``image_base64`` 가 결정론적 폴백 답변
(``planner._compose_result_answer`` → ``answer.render_result_data``)에 그대로 들어가, 사용자 메시지와
TaskDecomposer 다음 단계 프롬프트(267만 토큰)를 오염시켰다.
"""

from types import SimpleNamespace

import pytest
from robo_claw_agent.answer import flatten_to_text, render_result_data, render_structured_answer

pytestmark = pytest.mark.unit

_B64 = "iVBORw0KGgo" + "A" * 50_000


def test_render_result_data_drops_image_base64():
    text = render_result_data(
        {
            "message": "카메라 이미지를 캡처했습니다.",
            "file_path": "/tmp/camera_capture_1.png",
            "image_base64": _B64,
            "camera": "base",
        }
    )

    assert "image_base64" not in text
    assert "AAAA" not in text
    assert "file_path: /tmp/camera_capture_1.png" in text
    assert "camera: base" in text
    assert len(text) < 300


def test_nested_and_case_insensitive_base64_keys_are_dropped():
    text = flatten_to_text(
        {"frames": [{"Image_Base64": _B64, "id": 1}], "map": {"png_base64": _B64, "name": "w2"}}
    )

    assert "AAAA" not in text
    assert "id: 1" in text
    assert "name: w2" in text


def test_structured_llm_answer_drops_base64():
    assert "AAAA" not in render_structured_answer({"image_base64": _B64, "location": "주방"})


def test_regular_keys_containing_image_are_kept():
    """키 이름에 base64 가 없는 일반 필드는 그대로 보인다(과잉 차단 방지)."""
    text = render_result_data({"image_path": "/tmp/a.png", "detected_objects": ["cup"]})
    assert "image_path: /tmp/a.png" in text
    assert "cup" in text


def test_planner_fallback_answer_has_no_base64():
    from robo_claw_agent.agent_node.planner import _compose_result_answer

    results = [
        SimpleNamespace(
            message="카메라 이미지를 캡처했습니다.",
            result_data={"file_path": "/tmp/c.png", "image_base64": _B64, "camera": "base"},
        ),
        SimpleNamespace(
            message="시각 분석 완료.",
            result_data={"analysis": "책상 위에 컵이 있습니다.", "file_path": "/tmp/s.png"},
        ),
    ]

    answer = _compose_result_answer(results)

    assert "AAAA" not in answer
    assert "책상 위에 컵이 있습니다." in answer
    assert len(answer) < 500
