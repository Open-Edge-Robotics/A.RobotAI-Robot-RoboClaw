"""ExecuteTask 결과 → gRPC result_json 변환 테스트.

배경: 액션 result 의 ``skill_results`` 가 gRPC 응답까지 전달되지 않아, rag_search 가
어떤 Qdrant 항목을 참조했는지(``referenced``)를 클라이언트 리포트에서 볼 수 없었다.
"""

import json

from robo_claw_channel.task_result import (
    MAX_RESULT_DATA_CHARS,
    build_task_result_json,
    shrink_result_data,
    unpack_callback_result,
)


class TestBuildTaskResultJson:
    def test_empty_when_no_skill_results(self):
        """스킬 결과가 없으면 빈 문자열 → proto 필드 미설정."""
        assert build_task_result_json({"success": True, "message": "완료"}) == ""
        assert build_task_result_json({"skill_results": []}) == ""

    def test_referenced_is_reachable_as_nested_object(self):
        """스킬의 result_json 은 객체로 파싱돼야 클라이언트가 referenced 를 찾을 수 있다."""
        inner = {
            "success": True,
            "message": "지식 1건을 검색했습니다.",
            "referenced": [
                {"id": "abc-123", "score": 0.71, "type": "location", "text": "충전대"}
            ],
        }
        out = build_task_result_json(
            {
                "skill_results": [
                    {
                        "skill_name": "rag_search",
                        "code": 0,
                        "message": "지식 1건을 검색했습니다.",
                        "duration_sec": 1.5,
                        "result_json": json.dumps(inner, ensure_ascii=False),
                    }
                ]
            }
        )
        parsed = json.loads(out)
        step = parsed["skill_results"][0]
        assert step["skill_name"] == "rag_search"
        # 문자열이 아니라 dict 이어야 한다 (중첩 탐색 가능해야 함).
        assert isinstance(step["result_data"], dict)
        assert step["result_data"]["referenced"][0]["id"] == "abc-123"

    def test_unparseable_result_json_is_preserved_not_dropped(self):
        out = build_task_result_json(
            {"skill_results": [{"skill_name": "x", "result_json": "not json{"}]}
        )
        step = json.loads(out)["skill_results"][0]
        assert step["result_data"]["_unparsed"] == "not json{"

    def test_missing_result_json_becomes_empty_object(self):
        out = build_task_result_json({"skill_results": [{"skill_name": "say"}]})
        step = json.loads(out)["skill_results"][0]
        assert step["result_data"] == {}

    def test_oversized_payload_is_shrunk_but_keeps_referenced(self):
        """base64 이미지 등으로 커진 result_data 는 잘라내되 진단 키는 남긴다."""
        inner = {
            "image_base64": "A" * (MAX_RESULT_DATA_CHARS + 100),
            "referenced": [{"id": "keep-me"}],
            "count": 1,
        }
        out = build_task_result_json(
            {"skill_results": [{"skill_name": "capture_map",
                                "result_json": json.dumps(inner)}]}
        )
        data = json.loads(out)["skill_results"][0]["result_data"]
        assert data["_omitted"] is True
        assert "image_base64" not in data
        assert data["referenced"] == [{"id": "keep-me"}]
        assert data["count"] == 1

    def test_small_payload_is_untouched(self):
        data = {"a": 1, "b": "짧음"}
        assert shrink_result_data(data) == data


class TestUnpackCallbackResult:
    def test_three_tuple(self):
        assert unpack_callback_result((True, "ok", '{"x":1}')) == (True, "ok", '{"x":1}')

    def test_two_tuple_keeps_backward_compatibility(self):
        assert unpack_callback_result((False, "실패: 오류")) == (False, "실패: 오류", "")

    def test_plain_string_infers_success_from_prefix(self):
        assert unpack_callback_result("완료") == (True, "완료", "")
        assert unpack_callback_result("실패: 타임아웃") == (False, "실패: 타임아웃", "")

    def test_none_result_json_becomes_empty_string(self):
        assert unpack_callback_result((True, "ok", None)) == (True, "ok", "")
