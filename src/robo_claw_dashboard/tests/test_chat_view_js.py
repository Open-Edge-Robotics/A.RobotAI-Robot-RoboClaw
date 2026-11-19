"""채팅 결과 표시 JS 테스트 — chat_view.js 순수 함수를 node 로 실행해 검증한다.

결과 대기 시간이 지나 돌아온 응답(`unknown`)을 "실패"로 단정하지 않고, 에이전트가
계속 실행 중일 수 있다는 미확인 문구로 표시하는 규칙을 고정한다.
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

NODE_SCRIPT = """
const chat = require(process.argv[1]);
console.log(
  JSON.stringify({
    unconfirmed: chat.resultView({
      success: false,
      unknown: true,
      message: "태스크 결과 대기 타임아웃",
      error: "태스크 결과 대기 타임아웃",
    }),
    unknownWithoutMessage: chat.resultView({ success: false, unknown: true }),
    success: chat.resultView({ success: true, message: "완료" }),
    failure: chat.resultView({ success: false, message: "거부됨" }),
    empty: chat.resultView({}),
    nullish: chat.resultView(null),
    pending: chat.pendingView(),
  })
);
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        skip_or_fail_missing_node("node 실행 파일이 없어 JS 채팅 결과 표시 테스트를 건너뜁니다")
    return node


@pytest.fixture(scope="module")
def data() -> dict:
    result = subprocess.run(
        [_node(), "-e", NODE_SCRIPT, "--", str(CHAT_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_unconfirmed_result_is_warning_not_failure(data):
    view = data["unconfirmed"]
    assert "text-warning" in view["className"]
    assert "text-danger" not in view["className"]
    assert view["text"].startswith("결과 미확인")


def test_unconfirmed_result_keeps_cause_and_points_at_status(data):
    text = data["unconfirmed"]["text"]
    assert "태스크 결과 대기 타임아웃" in text
    # 이후 동작 여부는 상태/스킬/로그에서 확인해야 한다는 안내를 함께 남긴다.
    assert "계속 실행" in text
    assert "상태" in text


def test_unconfirmed_result_without_message_still_explains(data):
    view = data["unknownWithoutMessage"]
    assert view["text"].startswith("결과 미확인")
    assert "text-warning" in view["className"]


def test_success_and_failure_keep_existing_prefixes(data):
    assert data["success"]["text"] == "성공: 완료"
    assert "text-success" in data["success"]["className"]
    assert data["failure"]["text"] == "실패: 거부됨"
    assert "text-danger" in data["failure"]["className"]


def test_unknown_flag_absent_is_not_treated_as_unconfirmed(data):
    assert data["empty"]["text"].startswith("실패")
    assert data["nullish"]["text"].startswith("실패")


def test_pending_view_reports_sending_state(data):
    assert "전송 중" in data["pending"]["text"]
    assert "text-danger" not in data["pending"]["className"]
