"""실 백엔드 activity 형태의 JS 표시 계약 테스트.

- ``activity`` 를 정규화/리듀스에서 보존하고 현재 단계·동료·상태를 요약에 항상 표시한다.
- activity 투영 태스크를 태스크 목록에 "로컬 활동"으로 표시한다(원격 실행 태스크와 구분).
- phase_label 한국어를 우선하고, 없으면 실제 백엔드 phase 토큰을 한국어로 매핑한다.
- waiting_for_peer 는 실제 active 대기일 때만 표시한다.
- 비활성/빈 스냅샷은 이전 activity/task/goal 을 되살리지 않고, 추가 HTTP 를 만들지 않는다.
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
ACTIVITY_SCRIPT = (JS_DIR / "cleanup_activity.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def panel() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 실행 파일이 없어 JS 협동 패널 테스트를 건너뜁니다")
    result = subprocess.run(
        [node, "-e", ACTIVITY_SCRIPT, "--", str(COOPERATION_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestActivityNormalization:
    def test_activity_is_preserved_for_display(self, panel):
        activity = panel["activity"]
        assert activity["source"] == "cleanup_demo"
        assert activity["runId"] == "cleanup-run-former0047-7"
        assert activity["sessionId"] == "cleanup-run-former0047-7"
        assert activity["taskId"] == "cleanup-task-7"
        assert activity["phase"] == "demo_motion"
        assert activity["phaseLabel"] == "시연 모션 요청"
        assert activity["peer"] == "cloid01"
        assert activity["status"] == "motion_requested"
        assert activity["active"] is True
        assert activity["observed"] is False
        assert activity["physical"] is False
        assert activity["motion"] is False
        assert activity["updatedAt"] == 1700000010
        assert activity["durationMs"] == 10000

    def test_absent_or_malformed_activity_is_null(self, panel):
        assert panel["activityAbsent"]["missing"] is None
        assert panel["activityAbsent"]["malformed"] is None

    def test_strings_are_bounded_and_unknown_keys_dropped(self, panel):
        bounds = panel["bounds"]
        assert bounds["goalLength"] <= 241
        assert bounds["placeLength"] <= 97
        assert bounds["hasExtra"] is False

    def test_booleans_are_type_checked(self, panel):
        booleans = panel["booleans"]
        assert booleans["observed"] is None
        assert booleans["active"] is False
        assert booleans["physical"] is False
        assert booleans["motion"] is True


class TestActivityLifecycle:
    def test_activity_is_cleared_when_snapshot_stops_reporter(self, panel):
        reduced = panel["reduced"]
        assert reduced["afterAbsent"] is None
        assert reduced["unavailableActivity"] is None
        assert reduced["unavailableTasks"] == 0
        assert reduced["unavailableAvailable"] is False


class TestActivitySummary:
    def test_summary_always_shows_phase_peer_and_status(self, panel):
        summary = panel["current"]["summary"]
        assert "활동" in summary
        assert "로컬 정리 시연" in summary
        assert "현재 단계" in summary
        assert "시연 모션 요청" in summary
        assert "동료" in summary
        assert "cloid01" in summary
        assert "상태" in summary
        assert "시연 모션 요청 수락 · 실제 정리·모션 완료 미확인" in summary

    def test_specific_task_outcome_wins_over_generic_ended_status(self, panel):
        assert "상태 시연 모션 요청 수락" in panel["statusPriority"]
        assert "상태 알 수 없음" not in panel["statusPriority"]

    def test_phase_token_fallback_uses_backend_tokens(self, panel):
        assert "시연 재관측" in panel["phaseTokenDemo"]
        assert "동료 응답 대기" in panel["phaseTokenWait"]
        assert "장소 확인" in panel["phaseTokenStart"]

    def test_event_phase_label_korean_wins_over_token(self, panel):
        events = panel["events"]
        assert "단계: 시연 모션 요청" in events
        assert "단계: demo_motion" not in events
        assert "단계: 장소 확인" in events

    def test_waiting_status_requires_active_wait(self, panel):
        assert "동료 응답 대기" not in panel["waitingInactive"]
        assert "알 수 없음" in panel["waitingInactive"]
        assert "동료 응답 대기" in panel["waitingActive"]


class TestTaskSessionFilter:
    def test_projected_task_respects_session_filter(self, panel):
        assert "cleanup-task-7" in panel["sessionScoped"]
        assert "표시할 태스크가 없습니다" in panel["sessionScopedOther"]


class TestLiveActivitySource:
    def test_live_cooperation_stays_primary_while_cleanup_is_also_visible(self, panel):
        summary = panel["liveCooperation"]
        assert "autonomous_cooperate" in summary
        assert "정리 관측" in summary
        assert "로컬 정리" in summary

    def test_live_activity_source_wins_over_snapshot_metadata(self, panel):
        live = panel["liveSource"]
        assert "로컬 정리 (tidy_home)" in live
        assert "로컬 정리 시연" not in live
        assert "관측" in live


class TestActivityTasks:
    def test_projected_activity_task_is_labeled_local(self, panel):
        tasks = panel["current"]["tasks"]
        assert "cleanup-task-7" in tasks
        assert "로컬 활동" in tasks
        assert "cloid01" in tasks
        assert "시연 모션 요청 수락 · 실제 정리·모션 완료 미확인" in tasks
        assert "거실 정리 시연" in tasks
        assert "로컬 실행 완료" not in tasks

    def test_journal_task_gets_no_local_activity_label(self, panel):
        tasks = panel["journalTask"]["tasks"]
        assert "task-9" in tasks
        assert "로컬 활동" not in tasks


class TestDisabledSnapshot:
    def test_disabled_snapshot_does_not_restore_previous_activity(self, panel):
        disabled = panel["disabled"]
        assert "활동" not in disabled["summary"]
        assert "현재 단계" not in disabled["summary"]
        assert "로컬 정리" not in disabled["summary"]
        assert "–" in disabled["summary"]
        assert "표시할 태스크가 없습니다" in disabled["tasks"]
        assert "Maestro" in disabled["notice"]

    def test_snapshot_rendering_makes_no_http_calls(self, panel):
        assert panel["fetchCalls"] == 0
