"""채팅 전송 배선 테스트 — chat_view.js `bindSubmit` 을 가짜 DOM 으로 node 실행 검증한다.

대시보드 채팅이 "버튼을 눌러도 아무 반응이 없는" 상태로 회귀하지 않도록 다음을 고정한다.

- 폼 submit 이 preventDefault 후 정확히 한 번 POST /api/chat 을 호출하는지
- 전송 중에는 pending 문구와 비활성 버튼으로 중복 전송을 막는지
- 성공/미확인/HTTP 실패/예외가 각각 다른 문구로 표시되고, 실패 시 입력이 보존되는지
- 필수 노드가 없으면 null 을 돌려 예외로 죽지 않는지
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ._js_node import skip_or_fail_missing_node

pytestmark = pytest.mark.component

ASSETS = Path(__file__).resolve().parents[1] / "web" / "assets"
CHAT_JS = ASSETS / "chat_view.js"
HARNESS = Path(__file__).resolve().parent / "js" / "chat_send.js"


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        skip_or_fail_missing_node("node 실행 파일이 없어 JS 채팅 전송 테스트를 건너뜁니다")
    return node


@pytest.fixture(scope="module")
def data() -> dict:
    result = subprocess.run(
        [_node(), str(HARNESS), str(CHAT_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_single_request_uses_control_token_and_trims_message(data):
    success = data["success"]
    assert success["calls"] == 1
    assert success["url"] == "/api/chat"
    assert success["token"] == "control-token"
    assert success["method"] == "POST"
    assert json.loads(success["body"]) == {"message": "현재 상태 알려줘"}


def test_pending_state_is_visible_and_button_blocked_during_request(data):
    during = data["success"]["duringFetch"]
    assert "전송 중" in during["text"]
    assert during["disabled"] is True
    assert data["success"]["pendingText"] == "전송 중…"


def test_success_clears_input_and_reenables_button(data):
    success = data["success"]
    assert success["text"] == "성공: 완료"
    assert "text-success" in success["className"]
    assert success["inputAfter"] == ""
    assert success["buttonEnabledAfter"] is True


def test_form_submit_is_handled_and_prevented(data):
    assert data["formSubmit"]["prevented"] is True
    assert data["formSubmit"]["calls"] == 1


def test_empty_message_is_not_sent(data):
    empty = data["emptyInput"]
    assert empty["calls"] == 0
    assert "메시지를 입력" in empty["text"]
    assert "text-danger" not in empty["className"]


def test_duplicate_submit_sends_only_one_request(data):
    duplicate = data["duplicate"]
    assert duplicate["calls"] == 1
    assert duplicate["inFlight"] is True
    assert duplicate["inFlightAfter"] is False


def test_http_failure_reports_status_and_keeps_message(data):
    failure = data["httpFailure"]
    assert failure["text"] == "실패(400): 거부됨"
    assert "text-danger" in failure["className"]
    # 재시도할 수 있도록 입력을 지우지 않는다.
    assert failure["inputAfter"] == "위험한 명령"
    assert failure["buttonEnabledAfter"] is True


def test_unconfirmed_response_points_at_status_and_clears_input(data):
    unconfirmed = data["unconfirmed"]
    assert unconfirmed["text"].startswith("결과 미확인")
    assert "text-warning" in unconfirmed["className"]
    assert unconfirmed["inputAfter"] == ""


def test_thrown_error_is_shown_and_state_recovered(data):
    thrown = data["thrown"]
    assert thrown["text"].startswith("실패: ")
    assert "권한 없음" in thrown["text"]
    assert thrown["inputAfter"] == "안녕"
    assert thrown["buttonEnabledAfter"] is True


def test_bind_submit_tolerates_missing_nodes(data):
    assert data["missingForm"] is None
    assert data["missingApi"] is True
