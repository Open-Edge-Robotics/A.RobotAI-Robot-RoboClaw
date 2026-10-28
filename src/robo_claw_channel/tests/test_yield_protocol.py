"""자리 양보 gRPC 전송 규약 단위 테스트 (ROS2 불필요).

자유 문자열(content)로 실리는 JSON 페이로드는 손상될 수 있으므로, 수신측이
형식 검증을 통과한 값만 로컬 서비스로 승격하는지 확인한다.
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
    "target_x": 2.0,
    "target_y": 3.0,
    "requester_x": 5.0,
    "requester_y": 5.0,
}


# --- 요청 인코딩/디코딩 -------------------------------------------------------


def test_request_round_trip():
    encoded = encode_request(**_REQUEST)

    assert encoded is not None
    assert decode_request(encoded) == {
        **_REQUEST,
        "has_target_coords": True,
        "target_place": "",
        "radius_m": 0.0,
        "timeout_sec": 0.0,
    }


def test_message_type_constant_is_stable():
    assert YIELD_MESSAGE_TYPE == "yield_slot"


def test_request_requires_requester_id():
    assert encode_request(**{**_REQUEST, "requester_id": "  "}) is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "abc", None])
def test_request_rejects_invalid_target(bad):
    assert encode_request(**{**_REQUEST, "target_x": bad}) is None


def test_request_allows_unknown_requester_position():
    """요청자가 자기 위치를 모르면 nan 으로 보낸다 — 숫자이므로 허용."""
    encoded = encode_request(**{**_REQUEST, "requester_x": float("nan")})

    assert encoded is not None
    decoded = decode_request(encoded)
    assert decoded is not None
    assert decoded["requester_x"] != decoded["requester_x"]  # nan


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   ",
        "not json",
        "[]",
        "null",
        json.dumps({**_REQUEST, "requester_id": ""}),
        json.dumps({**_REQUEST, "target_x": "abc"}),
        json.dumps({**_REQUEST, "target_x": float("inf")}),
        json.dumps({**_REQUEST, "radius_m": -1.0}),
        json.dumps({**_REQUEST, "radius_m": 99.0}),
        json.dumps({**_REQUEST, "timeout_sec": 9999.0}),
        json.dumps({**_REQUEST, "requester_x": "nope"}),
        "x" * (MAX_PAYLOAD_LENGTH + 1),
    ],
)
def test_decode_rejects_malformed_requests(content):
    assert decode_request(content) is None


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


# --- 장소 이름 기반 요청 (맵 미공유 환경) ------------------------------------


def test_place_request_round_trip_without_coordinates():
    encoded = encode_request(
        requester_id="Former0047",
        target_x=0.0,
        target_y=0.0,
        requester_x=float("nan"),
        requester_y=float("nan"),
        has_target_coords=False,
        target_place="안방",
        requester_at_target=False,
    )

    decoded = decode_request(encoded)

    assert decoded is not None
    assert decoded["has_target_coords"] is False
    assert decoded["target_place"] == "안방"
    assert decoded["requester_at_target"] is False


def test_place_request_without_coords_or_place_is_rejected():
    encoded = encode_request(
        requester_id="Former0047",
        target_x=0.0,
        target_y=0.0,
        requester_x=0.0,
        requester_y=0.0,
        has_target_coords=False,
        target_place="",
    )

    assert encoded is None


def test_overlong_place_name_is_rejected():
    encoded = encode_request(
        requester_id="Former0047",
        target_x=0.0,
        target_y=0.0,
        requester_x=0.0,
        requester_y=0.0,
        has_target_coords=False,
        target_place="가" * 200,
    )

    assert encoded is None


def test_decoded_place_request_without_place_is_rejected():
    payload = json.dumps(
        {
            "requester_id": "Former0047",
            "has_target_coords": False,
            "target_place": "",
            "requester_x": 0.0,
            "requester_y": 0.0,
        }
    )

    assert decode_request(payload) is None


def test_requester_at_target_is_optional_and_absent_when_not_judged():
    decoded = decode_request(encode_request(**_REQUEST))

    assert decoded is not None
    assert "requester_at_target" not in decoded


def test_old_payload_without_new_fields_still_decodes():
    """구버전 페이로드(has_target_coords 없음)는 좌표 요청으로 해석한다."""
    legacy = json.dumps(
        {
            "requester_id": "Stretch3",
            "target_x": 2.0,
            "target_y": 3.0,
            "requester_x": 5.0,
            "requester_y": 5.0,
        }
    )

    decoded = decode_request(legacy)

    assert decoded is not None
    assert decoded["has_target_coords"] is True
    assert decoded["target_place"] == ""
