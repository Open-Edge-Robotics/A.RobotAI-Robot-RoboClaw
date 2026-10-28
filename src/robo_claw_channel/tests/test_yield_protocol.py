"""자리 양보 gRPC 전송 규약 단위 테스트 (ROS2 불필요).

자유 문자열(content)로 실리는 JSON 페이로드는 손상될 수 있으므로, 수신측이
형식 검증을 통과한 값만 로컬 서비스로 승격하는지 확인한다. 좌표는 로봇 사이에
넘기지 않는다 — 목표는 장소 이름만 담는다.
"""

import json

import pytest
from robo_claw_channel.yield_protocol import (
    MAX_PAYLOAD_LENGTH,
    YIELD_MESSAGE_TYPE,
    decode_request,
    decode_response,
    encode_request,
    encode_response,
)

pytestmark = pytest.mark.unit

_REQUEST = {
    "requester_id": "Stretch3",
    "target_place": "안방",
}


# --- 요청 인코딩/디코딩 -------------------------------------------------------


def test_request_round_trip():
    encoded = encode_request(**_REQUEST)

    assert encoded is not None
    assert decode_request(encoded) == {**_REQUEST, "radius_m": 0.0, "timeout_sec": 0.0}


def test_message_type_constant_is_stable():
    assert YIELD_MESSAGE_TYPE == "yield_slot"


def test_request_requires_requester_id():
    assert encode_request(**{**_REQUEST, "requester_id": "  "}) is None


def test_request_requires_a_place_name():
    assert encode_request(**{**_REQUEST, "target_place": "   "}) is None


def test_request_payload_carries_no_coordinates():
    """좌표를 넘기지 않는다 — 맵이 달라도 성립하려면 장소 이름만 있어야 한다."""
    decoded = decode_request(encode_request(**_REQUEST))

    assert decoded is not None
    assert not any(key in decoded for key in ("target_x", "target_y", "requester_x", "requester_y"))


def test_request_carries_requester_at_target_when_judged():
    encoded = encode_request(**_REQUEST, requester_at_target=False)

    decoded = decode_request(encoded)

    assert decoded is not None
    assert decoded["requester_at_target"] is False


def test_requester_at_target_is_absent_when_not_judged():
    decoded = decode_request(encode_request(**_REQUEST))

    assert decoded is not None
    assert "requester_at_target" not in decoded


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   ",
        "not json",
        "[]",
        "null",
        json.dumps({**_REQUEST, "requester_id": ""}),
        json.dumps({**_REQUEST, "target_place": ""}),
        json.dumps({**_REQUEST, "radius_m": -1.0}),
        json.dumps({**_REQUEST, "radius_m": 99.0}),
        json.dumps({**_REQUEST, "timeout_sec": 9999.0}),
        "x" * (MAX_PAYLOAD_LENGTH + 1),
    ],
)
def test_decode_rejects_malformed_requests(content):
    assert decode_request(content) is None


def test_overlong_place_name_is_rejected():
    encoded = encode_request(**{**_REQUEST, "target_place": "가" * 200})

    assert encoded is None


def test_legacy_payload_with_coordinates_still_decodes():
    """구버전 페이로드(좌표 포함)는 장소 이름이 있으면 그대로 해석한다."""
    legacy = json.dumps(
        {
            "requester_id": "Stretch3",
            "has_target_coords": True,
            "target_x": 2.0,
            "target_y": 3.0,
            "target_place": "안방",
            "requester_x": 5.0,
            "requester_y": 5.0,
        }
    )

    decoded = decode_request(legacy)

    assert decoded is not None
    assert decoded["target_place"] == "안방"
    assert "target_x" not in decoded


# --- 응답 인코딩/디코딩 -------------------------------------------------------


def test_response_round_trip():
    encoded = encode_response(
        decision="accepted",
        reason="yield_prepared",
        distance_m=0.31,
        has_yield_spot=True,
        yield_spot_x=0.0,
        yield_spot_y=3.0,
        message="비켜섰습니다.",
    )

    decoded = decode_response(encoded)

    assert decoded is not None
    assert decoded["decision"] == "accepted"
    assert decoded["has_yield_spot"] is True
    assert decoded["yield_spot_y"] == 3.0
    assert decoded["message"] == "비켜섰습니다."


def test_response_encodes_busy_retry_hint():
    decoded = decode_response(
        encode_response(decision="busy", reason="agent_busy", retry_after_sec=3.0)
    )

    assert decoded is not None
    assert decoded["retry_after_sec"] == 3.0


def test_response_with_unknown_decision_is_rejected():
    assert decode_response(json.dumps({"decision": "maybe"})) is None


def test_response_without_decision_is_rejected():
    assert decode_response(json.dumps({})) is None


@pytest.mark.parametrize("content", ["", "nope", "[]", "x" * (MAX_PAYLOAD_LENGTH + 1)])
def test_decode_rejects_malformed_responses(content):
    assert decode_response(content) is None


def test_response_negative_retry_is_clamped():
    decoded = decode_response(json.dumps({"decision": "busy", "retry_after_sec": -5.0}))

    assert decoded is not None
    assert decoded["retry_after_sec"] == 0.0
