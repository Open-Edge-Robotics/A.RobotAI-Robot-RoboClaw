"""로컬 정리/시연 이벤트의 타임라인 표시 계약 테스트(DOM).

- 정리/시연 이벤트가 기본 타임라인과 유형 필터에 나타나고, 상태가 비어도 한국어 단계명을 보여준다.
- motion_requested/waiting_for_peer/cancel_requested/reported_to_maestro 는 완료/녹색으로 승격되지 않는다.
- Maestro 관제로 직접 협동이 꺼져 있어도 로컬 정리 관측은 표시하고 그 사실을 문구로 알린다.
- 구버전(activity_source 없는) 스냅샷 표시는 그대로 유지된다.
- 스냅샷 렌더에 추가 HTTP/타이머를 만들지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.component

COOPERATION_JS = Path(__file__).resolve().parents[1] / "web" / "assets" / "cooperation.js"
JS_DIR = Path(__file__).resolve().parent / "js"

DOM_SCRIPT = (JS_DIR / "cleanup_demo_dom.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dom() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 실행 파일이 없어 JS 협동 패널 테스트를 건너뜁니다")
    result = subprocess.run(
        [node, "-e", DOM_SCRIPT, "--", str(COOPERATION_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def _item(items: list[dict], needle: str) -> dict:
    for item in items:
        if needle in item["text"]:
            return item
    raise AssertionError(
        f"'{needle}' 이벤트가 타임라인에 없습니다: {[item['text'] for item in items]}"
    )


class TestWaitingForPeer:
    def test_requester_phases_and_peer_are_visible(self, dom):
        text = dom["waiting"]["text"]
        assert "장소 확인" in text
        assert "이동" in text
        assert "관측" in text
        assert "거실" in text
        assert "cloid01" in text
        assert "요청 ID: demo-req-7" in text
        assert "알 수 없음" not in text

    def test_waiting_status_is_explicit_and_not_completed(self, dom):
        text = dom["waiting"]["text"]
        assert "동료 응답 대기 · 실행 결과 미확인" in text
        assert "로컬 실행 완료" not in text
        assert "상대 보고: 완료" not in text
        waiting = _item(dom["waiting"]["items"], "동료 응답 대기")
        assert "완료" not in waiting["text"]
        assert waiting["chips"], "대기 상태 칩이 없습니다"
        assert all("-ok" not in chip["cls"] for chip in waiting["chips"])
        assert "→" in waiting["text"]

    def test_waiting_items_expose_semantic_labels(self, dom):
        labels = [label for label in dom["waiting"]["labels"] if label]
        assert len(labels) == len(dom["waiting"]["items"])
        assert all(
            "대기" in label or "수신" in label or "송신" in label or "관측" in label
            for label in labels
        )


class TestMotionRequested:
    def test_motion_request_is_accepted_but_unconfirmed(self, dom):
        text = dom["responded"]["text"]
        assert "시연 모션 요청 수락 · 실제 정리·모션 완료 미확인" in text
        motion = _item(dom["responded"]["items"], "시연 모션 요청 수락")
        assert "완료" in motion["text"]  # 문구 자체는 "완료 미확인"
        assert "미확인" in motion["text"]
        assert "-ok" not in " ".join(chip["cls"] for chip in motion["chips"])
        assert all("상대 보고: 완료" not in chip["text"] for chip in motion["chips"])

    def test_local_execution_and_peer_response_are_distinguished(self, dom):
        text = dom["responded"]["text"]
        assert "로컬 실행 기준" in text
        assert "동료 응답 기준" in text

    def test_no_green_physical_success_anywhere(self, dom):
        text = dom["responded"]["text"]
        assert "로컬 실행 완료" not in text
        assert "상대 보고: 완료" not in text
        motion = _item(dom["responded"]["items"], "시연 모션 요청 수락")
        assert "실제 정리 완료" not in motion["text"]
        assert motion["chips"] and all("rc-coop-ok" not in chip["cls"] for chip in motion["chips"])


class TestCancelAndMaestroReport:
    def test_cancel_request_is_local_notice_only(self, dom):
        text = dom["cancel"]["text"]
        assert "정리 중단 요청 · 실제 정지 확인 전" in text
        assert "실패 이유: 동료 응답 시간 초과" in text
        cancel = _item(dom["cancel"]["items"], "정리 중단 요청")
        assert all("-ok" not in chip["cls"] for chip in cancel["chips"])

    def test_reported_to_maestro_does_not_claim_peer_execution(self, dom):
        text = dom["reported"]["text"]
        assert "Maestro 보고 · 동료 실행 미확인" in text
        reported = _item(dom["reported"]["items"], "Maestro 보고")
        assert all("-ok" not in chip["cls"] for chip in reported["chips"])
        assert "방 전체" in text


class TestMaestroLocalOnly:
    def test_local_cleanup_progress_renders_with_disabled_notice(self, dom):
        local = dom["localOnly"]
        assert "로컬 정리" in local["notice"]
        assert "직접 협동 비활성" in local["notice"]
        assert "Maestro" in local["notice"]
        assert "재관측" in local["text"]
        assert "거실" in local["text"]
        assert "r-secret" not in local["text"]
        assert "***" in local["text"]

    def test_summary_names_activity_source_and_keeps_goal(self, dom):
        summary = dom["localOnly"]["summary"]
        assert "활동" in summary
        assert "로컬 정리 시연" in summary
        assert "거실 정리 시연" in summary
        assert "cleanup-run-former0047-2" in summary
        assert any("rc-coop-warn" in badge["cls"] for badge in dom["localOnly"]["badges"])

    def test_old_maestro_disabled_snapshot_is_unchanged(self, dom):
        old = dom["oldDisabled"]
        assert "Maestro 관제로 협동이 비활성화되었습니다" in old["notice"]
        assert "로컬 정리" not in old["notice"]
        assert "로컬 정리" not in old["summary"]
        assert "활동" not in old["summary"]

    def test_old_enabled_snapshot_keeps_active_badge(self, dom):
        old = dom["oldEnabled"]
        assert "협동 관측 활성" in old["summary"]
        assert "로컬 정리" not in old["summary"]
        assert "직접 협동 비활성" not in old["notice"]


class TestFinishedRun:
    def test_finished_run_keeps_goal_and_never_claims_whole_room(self, dom):
        finished = dom["finished"]
        assert "거실 정리" in finished["summary"]
        assert "cleanup-run-former0047-1" in finished["summary"]
        assert "방 전체" in finished["text"]
        assert "정리 시연 종료" in finished["text"]
        assert all("rc-coop-ok" not in badge["cls"] for badge in finished["badges"])


class TestFiltersAndRequests:
    def test_default_timeline_contains_cleanup_and_demo_events(self, dom):
        assert dom["filters"]["counts"]["all"] == 5

    def test_type_filters_cover_cleanup_and_demo(self, dom):
        counts = dom["filters"]["counts"]
        assert counts["messages"] == 1
        assert counts["tasks"] == 2
        assert counts["cleanup"] == 2
        assert counts["demo"] == 1
        options = " ".join(dom["filters"]["options"])
        assert "정리 실행" in options
        assert "정리 시연" in options

    def test_snapshot_and_filter_rendering_make_no_http_calls(self, dom):
        assert dom["fetchAfterSnapshots"] == 0
        assert dom["filters"]["fetchCalls"] == 0

    def test_export_still_uses_header_token_only(self, dom):
        call = dom["exportCall"]
        assert call["url"] == "/api/cooperation/export"
        assert call["headers"]["X-RoboClaw-Token"] == "r-secret"
        assert "r-secret" not in call["url"]
