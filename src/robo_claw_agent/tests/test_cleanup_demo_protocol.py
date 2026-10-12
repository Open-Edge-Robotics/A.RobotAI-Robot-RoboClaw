import pytest
from robo_claw_agent.skills.cleanup_demo_protocol import (
    decode_cleanup_demo_request,
    encode_cleanup_demo_request,
)

pytestmark = pytest.mark.unit


def test_cleanup_demo_request_is_structured_and_does_not_invent_object_coordinates():
    request = {
        "request_id": "req-123",
        "target_location": "안방",
        "target_object": "포장지",
        "scene_summary": "바닥에 포장지가 놓여 있습니다.",
    }

    encoded = encode_cleanup_demo_request(request)
    decoded = decode_cleanup_demo_request(encoded)

    assert decoded == request
    assert "x" not in decoded
    assert "y" not in decoded
    assert "target_pose" not in decoded


def test_cleanup_demo_request_rejects_unknown_fields_and_bad_payloads():
    assert decode_cleanup_demo_request("일반 자연어 요청") is None
    assert decode_cleanup_demo_request("ROBOCLAW_CLEANUP_DEMO_V1:{bad json") is None
    assert (
        decode_cleanup_demo_request(
            'ROBOCLAW_CLEANUP_DEMO_V1:{"target_location":"안방","target_object":"포장지",'
            '"scene_summary":"포장지","x":1.0}'
        )
        is None
    )
    with pytest.raises(ValueError):
        encode_cleanup_demo_request(
            {"target_location": "안방", "target_object": "", "scene_summary": ""}
        )
