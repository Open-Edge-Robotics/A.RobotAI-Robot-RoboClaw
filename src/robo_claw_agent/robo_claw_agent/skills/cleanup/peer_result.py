"""동료 정리 시연 결과의 순수 파서.

``call_peer_robot``(gRPC)과 ``delegate_task``(ROS action)는 서로 다른 형태로 결과를
돌려준다. 두 경로에서 실제 ``cleanup_demo`` 스킬 결과만 골라 "모션 요청 접수" 상태와
request_id/확인 플래그를 꺼낸다. 일반 성공 응답·산문·큐 ack 는 시연 상태로 추론하지
않는다.

gRPC ``CommandResponse.result_json`` 은 ``task_result.build_task_result_json`` 형태다::

    {"skill_results": [{"skill_name": "cleanup_demo", "code": 0, "message": "...",
                        "result_data": {"status": "motion_requested", ...}}]}

ROS ``ExecuteTask.Result.skill_results[]`` 는 ``SkillResult.result_json`` 문자열에
스킬 ``result_data`` dict 를 그대로 담는다.

ROS2/네트워크 의존이 없다.
"""

from __future__ import annotations

import json
from typing import Any

#: gRPC result_json 상한(task_result.MAX_RESULT_DATA_CHARS 와 동일한 규모).
MAX_PEER_RESULT_JSON_CHARS = 16000
#: 한 응답에서 검사할 스킬 결과 최대 개수.
MAX_PEER_RESULT_STEPS = 30
#: 상태/ID/메시지 문자열 상한.
MAX_STATUS_CHARS = 64
MAX_ID_CHARS = 80
MAX_MESSAGE_CHARS = 500

CLEANUP_DEMO_SKILL = "cleanup_demo"
STATUS_MOTION_REQUESTED = "motion_requested"


def _text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _as_bool(value: object) -> bool:
    return value is True


def _peer_result_from_result_data(
    result_data: object, *, skill_name: str, message: str
) -> dict[str, Any] | None:
    if not isinstance(result_data, dict):
        return None
    if skill_name == CLEANUP_DEMO_SKILL:
        request_id = _text(result_data.get("request_id"), MAX_ID_CHARS)
        status = _text(result_data.get("status"), MAX_STATUS_CHARS)
        physical_confirmed = _as_bool(result_data.get("physical_cleanup_confirmed"))
        motion_confirmed = _as_bool(result_data.get("motion_completion_confirmed"))
        # motion_requested 는 "요청 접수"일 뿐이다. 물리 정리/모션 완료로 주장된
        # 결과는 초록 상태로 승격하지 않고 미확인(unknown)으로 둔다.
        explicit_motion = (
            status == STATUS_MOTION_REQUESTED and not physical_confirmed and not motion_confirmed
        )
        motion_id = result_data.get("motion_id")
        return {
            "status": STATUS_MOTION_REQUESTED if explicit_motion else "",
            "observed": True,
            "request_id": request_id,
            "target_location": _text(result_data.get("target_location"), MAX_ID_CHARS),
            "target_object": _text(result_data.get("target_object"), MAX_ID_CHARS * 2),
            "motion_id": motion_id if isinstance(motion_id, int) else None,
            "physical_cleanup_confirmed": False,
            "motion_completion_confirmed": False,
            "skill_name": CLEANUP_DEMO_SKILL,
            "message": _text(message, MAX_MESSAGE_CHARS),
        }
    return None


def peer_result_from_grpc_json(raw: object) -> dict[str, Any] | None:
    """gRPC ``result_json`` 문자열에서 시연 결과를 꺼낸다."""
    text = str(raw or "").strip()
    if not text or len(text) > MAX_PEER_RESULT_JSON_CHARS:
        return None
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    steps = payload.get("skill_results")
    if not isinstance(steps, list):
        return None
    for step in steps[:MAX_PEER_RESULT_STEPS]:
        if not isinstance(step, dict):
            continue
        result = _peer_result_from_result_data(
            step.get("result_data"),
            skill_name=_text(step.get("skill_name"), MAX_STATUS_CHARS),
            message=_text(step.get("message"), MAX_MESSAGE_CHARS),
        )
        if result is not None:
            return result
    return None


def peer_result_from_skill_results(skill_results: object) -> dict[str, Any] | None:
    """ROS ``ExecuteTask.Result.skill_results`` 목록에서 시연 결과를 꺼낸다."""
    if not isinstance(skill_results, (list, tuple)):
        return None
    for item in skill_results[:MAX_PEER_RESULT_STEPS]:
        raw = getattr(item, "result_json", "")
        if not raw:
            continue
        text = str(raw)
        if len(text) > MAX_PEER_RESULT_JSON_CHARS:
            continue
        try:
            result_data = json.loads(text)
        except (TypeError, ValueError):
            continue
        result = _peer_result_from_result_data(
            result_data,
            skill_name=_text(getattr(item, "skill_name", ""), MAX_STATUS_CHARS),
            message=_text(getattr(item, "message", ""), MAX_MESSAGE_CHARS),
        )
        if result is not None:
            return result
    return None


def correlated_peer_result(
    peer_result: object, *, expected_request_id: str = ""
) -> dict[str, Any] | None:
    """상대 결과의 request_id 를 우리가 보낸 시연 요청과 대조한다.

    상관이 맞지 않거나 ID 가 없으면 ``None``(미확인)을 돌려준다. 시연 요청이 아닌
    일반 위임(expected_request_id 없음)도 시연 상태로 승격하지 않는다.
    """
    expected = str(expected_request_id or "").strip()
    if not expected or not isinstance(peer_result, dict):
        return None
    actual = str(peer_result.get("request_id") or "").strip()
    if not actual or actual != expected:
        return None
    if peer_result.get("status") != STATUS_MOTION_REQUESTED:
        return None
    return peer_result
