"""ExecuteTask 결과 → gRPC 응답 변환 헬퍼 (ROS/gRPC 의존 없음).

``ros_future_utils`` 는 ``robo_claw_msgs`` 를, ``grpc_server`` 는 ``grpc`` 를 임포트하므로
그 안에 두면 ROS 없는 환경에서 테스트할 수 없다. 순수 변환 로직만 여기로 분리한다.
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 스킬 1건의 result_data 직렬화 상한. 카메라/지도 스킬은 base64 이미지를 result_data 에
# 담기 때문에 그대로 실으면 gRPC 응답이 수 MB가 된다. 상한을 넘으면 본문은 버리고
# 진단에 필요한 키만 남긴다.
MAX_RESULT_DATA_CHARS = 16000
# 진단용으로 잘라내지 않고 보존할 키 (rag_search 가 참조한 Qdrant 항목 등).
DIAGNOSTIC_KEYS = ("referenced", "count", "message")


def shrink_result_data(data: Any) -> Any:
    """result_data 가 과도하게 크면 진단 키만 남긴 요약으로 바꾼다."""
    try:
        size = len(json.dumps(data, ensure_ascii=False))
    except (TypeError, ValueError):
        return {"_unserializable": True}
    if size <= MAX_RESULT_DATA_CHARS:
        return data
    summary: dict[str, Any] = {"_omitted": True, "_size": size}
    if isinstance(data, dict):
        for key in DIAGNOSTIC_KEYS:
            if key in data:
                summary[key] = data[key]
    return summary


def build_task_result_json(response: dict[str, Any]) -> str:
    """ExecuteTask 결과 dict 을 gRPC ``CommandResponse.result_json`` 문자열로 만든다.

    스킬별 ``result_json`` 은 **파싱해서 객체로** 심는다. 문자열로 두면 클라이언트가
    중첩 구조를 훑어도 ``rag_search`` 의 ``referenced`` 같은 진단 필드에 닿지 못한다.
    실을 것이 없으면 빈 문자열(=필드 미설정)을 돌려준다.
    """
    steps: list[dict[str, Any]] = []
    for item in response.get("skill_results") or []:
        raw = str(item.get("result_json") or "")
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            data = {"_unparsed": raw[:2000]}
        steps.append(
            {
                "skill_name": item.get("skill_name", ""),
                "code": item.get("code", 0),
                "message": item.get("message", ""),
                "duration_sec": item.get("duration_sec", 0.0),
                "result_data": shrink_result_data(data),
            }
        )
    if not steps:
        return ""
    try:
        return json.dumps({"skill_results": steps}, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        logger.warning("Failed to serialise skill results into result_json: %s", exc)
        return ""


def unpack_callback_result(res_result: object) -> tuple[bool, str, str]:
    """``on_message_cb`` 반환값을 ``(success, text, result_json)`` 으로 정규화한다.

    콜백 구현이 문자열 / ``(success, text)`` / ``(success, text, result_json)`` 세 형태로
    존재하므로(다른 채널·테스트 더블 포함) 모두 받아준다.
    """
    if isinstance(res_result, tuple):
        if len(res_result) >= 3:
            success, text, result_json = res_result[0], res_result[1], res_result[2]
            return bool(success), str(text), str(result_json or "")
        if len(res_result) == 2:
            success, text = res_result
            return bool(success), str(text), ""
        text = str(res_result[0]) if res_result else ""
        return not text.startswith("실패:"), text, ""
    text = str(res_result)
    return not text.startswith("실패:"), text, ""
