"""로컬 정리/시연(cooperation 저널 확장) 도메인·리듀서 계약 테스트.

계약:
- 상태 허용 목록에 ``motion_requested``/``waiting_for_peer``/``cancel_requested``/
  ``reported_to_maestro`` 가 포함되고, 실제 motion_requested 를 unknown/completed 로 바꾸지 않는다.
- 스냅샷 메타데이터 ``activity_source``/``direct_cooperation_enabled`` 를 화이트리스트로 보존한다.
- 정리 이벤트 details 의 source/phase/place/target/transport/request_id/observed/duration_ms/
  failure_reason 과 확인값 ``false`` 를 상태·캐시·내보내기 정규화에서 잃지 않는다.
- 로컬 정리 관측은 active=false 여도 마지막 목표/세션/결과를 남긴다(백엔드 계약을 그대로 표시).
- 백엔드는 fake 저널 dict 로 대체하고 추가 HTTP/폴링을 만들지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from robo_claw_dashboard.domain import cooperation as coop

pytestmark = pytest.mark.unit

COOPERATION_JS = Path(__file__).resolve().parents[1] / "web" / "assets" / "cooperation.js"
JS_DIR = Path(__file__).resolve().parent / "js"

CLEANUP_EVENT_TYPES = [
    "cleanup_run_started",
    "cleanup_phase",
    "cleanup_run_finished",
    "cleanup_handoff",
    "cleanup_delegate_started",
    "cleanup_delegate_finished",
]
DEMO_EVENT_TYPES = [
    "cleanup_demo_started",
    "cleanup_demo_phase",
    "cleanup_demo_motion_requested",
    "cleanup_demo_failed",
    "cleanup_demo_finished",
]


def cleanup_event(sequence: int, event_type: str = "cleanup_phase", **overrides) -> dict:
    base = {
        "sequence": sequence,
        "event_type": event_type,
        "timestamp": 1700000000.0 + sequence,
        "robot_id": "former0047",
        "peer_id": "",
        "session_id": "cleanup-run-former0047-1",
        "task_id": "cleanup-task-1",
        "message_id": "",
        "reply_to": "",
        "status": "",
        "content": "",
        "details": {},
    }
    base.update(overrides)
    return base


def cleanup_journal(**overrides) -> dict:
    base = {
        "available": True,
        "enabled": True,
        "active": False,
        "local_robot_id": "former0047",
        "session_id": "cleanup-run-former0047-1",
        "goal": "거실 정리 (마지막 정리 실행)",
        "stream_id": "stream-cleanup",
        "activity_source": "tidy_home",
        "direct_cooperation_enabled": False,
        "events": [
            cleanup_event(
                1,
                "cleanup_run_started",
                details={"source": "local_cleanup", "phase": "place_resolution", "place": "거실"},
            ),
            cleanup_event(
                2,
                "cleanup_phase",
                details={"source": "local_cleanup", "phase": "navigation", "place": "거실"},
            ),
            cleanup_event(
                3,
                "cleanup_run_finished",
                details={"source": "local_cleanup", "phase": "response", "duration_ms": 4200},
            ),
        ],
        "tasks": [
            {
                "task_id": "cleanup-task-1",
                "session_id": "cleanup-run-former0047-1",
                "peer_id": "",
                "description": "거실 정리",
                "status": "completed",
                "created_at": 1.0,
                "updated_at": 2.0,
                "result": "거실 정리 1건 완료",
            }
        ],
        "next_cursor": 4,
        "oldest_cursor": 1,
        "dropped_events": 0,
        "persistence": {"enabled": True, "error": None},
    }
    base.update(overrides)
    return base


class TestStatusAllowlist:
    @pytest.mark.parametrize(
        "status",
        ["motion_requested", "waiting_for_peer", "cancel_requested", "reported_to_maestro"],
    )
    def test_cleanup_status_is_known(self, status: str):
        assert status in coop.KNOWN_STATUSES
        assert coop.normalize_status(status) == status

    def test_motion_requested_is_never_mapped_to_unknown(self):
        payload = coop.normalize_cooperation(
            cleanup_journal(
                events=[
                    cleanup_event(
                        1,
                        "cleanup_demo_motion_requested",
                        status="motion_requested",
                        observed=False,
                        details={"request_id": "demo-req-7", "physical_cleanup_confirmed": False},
                    )
                ]
            )
        )
        item = payload["events"][0]
        assert item["status"] == "motion_requested"
        assert item["status"] not in {"unknown", "completed"}


class TestSnapshotMetadata:
    def test_activity_source_and_direct_flag_are_preserved(self):
        payload = coop.normalize_cooperation(cleanup_journal())
        assert payload["enabled"] is True
        assert payload["active"] is False
        assert payload["activity_source"] == "tidy_home"
        assert payload["direct_cooperation_enabled"] is False
        assert payload["session_id"] == "cleanup-run-former0047-1"
        assert payload["goal"].startswith("거실 정리")

    def test_unknown_or_malformed_activity_source_is_dropped(self):
        for raw in ("remote_control", {"source": "tidy_home"}, 7, None):
            payload = coop.normalize_cooperation(cleanup_journal(activity_source=raw))
            assert payload["activity_source"] == ""

    def test_activity_source_is_lowercased_whitelist(self):
        payload = coop.normalize_cooperation(cleanup_journal(activity_source=" CLEANUP_DEMO "))
        assert payload["activity_source"] == "cleanup_demo"
        payload = coop.normalize_cooperation(
            cleanup_journal(activity_source="autonomous_cooperate")
        )
        assert payload["activity_source"] == "autonomous_cooperate"

    def test_missing_metadata_keeps_old_snapshot_behavior(self):
        raw = {
            key: value
            for key, value in cleanup_journal().items()
            if key not in ("activity_source", "direct_cooperation_enabled")
        }
        payload = coop.normalize_cooperation(raw)
        assert payload["available"] is True
        assert payload["activity_source"] == ""
        assert payload["direct_cooperation_enabled"] is False

    def test_direct_flag_is_true_only_for_literal_true(self):
        for raw in ("yes", 1, {"enabled": True}, None):
            payload = coop.normalize_cooperation(cleanup_journal(direct_cooperation_enabled=raw))
            assert payload["direct_cooperation_enabled"] is False
        enabled = coop.normalize_cooperation(cleanup_journal(direct_cooperation_enabled=True))
        assert enabled["direct_cooperation_enabled"] is True

    def test_unavailable_cooperation_has_empty_metadata(self):
        payload = coop.normalize_cooperation(None)
        assert payload["activity_source"] == ""
        assert payload["direct_cooperation_enabled"] is False

    def test_payload_carries_metadata_to_cache_and_api(self):
        cached = coop.normalize_cooperation(cleanup_journal())
        payload = coop.cooperation_payload(cached)
        assert payload["activity_source"] == "tidy_home"
        assert payload["direct_cooperation_enabled"] is False
        unavailable = coop.cooperation_payload(None)
        assert unavailable["activity_source"] == ""
        assert unavailable["direct_cooperation_enabled"] is False

    def test_cleanup_finish_keeps_last_goal_and_result_without_active(self):
        payload = coop.normalize_cooperation(
            cleanup_journal(
                events=[
                    cleanup_event(1, "cleanup_run_started", details={"phase": "place_resolution"}),
                    cleanup_event(
                        2, "cleanup_run_finished", status="", details={"duration_ms": 1200}
                    ),
                ]
            )
        )
        assert payload["active"] is False
        assert payload["goal"]
        assert payload["session_id"]
        assert payload["events"][-1]["event_type"] == "cleanup_run_finished"
        assert payload["tasks"][0]["result"]


class TestProvenanceDetails:
    def test_details_and_false_confirmations_survive_normalization(self):
        details = {
            "source": "cleanup_demo",
            "phase": "motion_request",
            "place": "거실",
            "target": "cloid01",
            "transport": "local",
            "request_id": "demo-req-7",
            "observed": False,
            "duration_ms": 1200,
            "failure_reason": "",
            "physical_cleanup_confirmed": False,
            "motion_completion_confirmed": False,
        }
        payload = coop.normalize_cooperation(
            cleanup_journal(
                activity_source="cleanup_demo",
                events=[
                    cleanup_event(
                        1,
                        "cleanup_demo_motion_requested",
                        status="motion_requested",
                        observed=False,
                        details=details,
                    )
                ],
            )
        )
        item = payload["events"][0]
        assert item["status"] == "motion_requested"
        assert item["observed"] is False
        assert item["details"] == details

    def test_export_keeps_provenance_and_masks_secrets(self):
        payload = coop.normalize_cooperation(
            cleanup_journal(
                events=[
                    cleanup_event(
                        1,
                        "cleanup_demo_phase",
                        details={
                            "source": "cleanup_demo",
                            "phase": "reobservation",
                            "place": "거실",
                            "request_id": "demo-req-7",
                            "observed": True,
                            "physical_cleanup_confirmed": False,
                            "device_token": "r-secret",
                        },
                    )
                ]
            )
        )
        lines = [line for line in coop.format_cooperation_jsonl(payload).splitlines() if line]
        decoded = json.loads(lines[0])
        assert decoded["details"]["request_id"] == "demo-req-7"
        assert decoded["details"]["phase"] == "reobservation"
        assert decoded["details"]["physical_cleanup_confirmed"] is False
        assert decoded["details"]["device_token"] == coop.MASK
        assert "r-secret" not in lines[0]


REDUCER_SCRIPT = (JS_DIR / "cleanup_demo_reducer.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def reducer() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node 실행 파일이 없어 JS 협동 패널 테스트를 건너뜁니다")
    result = subprocess.run(
        [node, "-e", REDUCER_SCRIPT, "--", str(COOPERATION_JS)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


class TestReducerStatuses:
    def test_motion_requested_is_neutral_and_unconfirmed(self, reducer):
        status = reducer["statuses"]["motionRequested"]
        assert status["label"] == "시연 모션 요청 수락 · 실제 정리·모션 완료 미확인"
        assert status["tone"] in ("warn", "idle")

    def test_waiting_cancel_and_maestro_labels_are_explicit(self, reducer):
        statuses = reducer["statuses"]
        assert statuses["waitingForPeer"]["label"] == "동료 응답 대기 · 실행 결과 미확인"
        assert statuses["cancelRequested"]["label"] == "정리 중단 요청 · 실제 정지 확인 전"
        assert statuses["reportedToMaestro"]["label"] == "Maestro 보고 · 동료 실행 미확인"
        assert statuses["reportedToMaestro"]["tone"] != "ok"

    def test_new_statuses_are_not_normalized_to_unknown(self, reducer):
        assert reducer["normalizedStatus"] == {
            "motionRequested": "motion_requested",
            "waitingForPeer": "waiting_for_peer",
            "cancelRequested": "cancel_requested",
            "reportedToMaestro": "reported_to_maestro",
        }


class TestReducerSnapshotMetadata:
    def test_activity_metadata_survives_reduce(self, reducer):
        assert reducer["activity"] == {
            "source": "tidy_home",
            "direct": False,
            "enabled": True,
            "active": False,
            "goal": "거실 정리 (마지막 정리 실행)",
            "sessionId": "cleanup-run-former0047-1",
        }

    def test_old_snapshot_has_no_activity_metadata(self, reducer):
        assert reducer["legacyActivity"] == {"source": "", "direct": False}

    def test_unknown_metadata_is_dropped_and_source_is_bounded(self, reducer):
        assert reducer["oddActivity"] == {"source": "", "direct": False}
        assert reducer["demoActivity"] == {"source": "cleanup_demo"}

    def test_finished_snapshot_keeps_last_goal_but_is_inactive(self, reducer):
        assert reducer["finished"]["active"] is False
        assert reducer["finished"]["goal"]
        assert reducer["finished"]["sessionId"]
        assert reducer["finished"]["events"] == 1


class TestReducerTimelineTypes:
    def test_cleanup_and_demo_events_are_in_default_timeline(self, reducer):
        assert reducer["filters"]["all"] == len(CLEANUP_EVENT_TYPES) + len(DEMO_EVENT_TYPES)

    def test_type_filters_cover_cleanup_and_demo(self, reducer):
        filters = reducer["filters"]
        assert filters["cleanup"] == CLEANUP_EVENT_TYPES
        assert filters["demo"] == DEMO_EVENT_TYPES
        assert filters["messages"] == []
        assert "cleanup_delegate_started" in filters["tasks"]
        assert "cleanup_delegate_finished" in filters["tasks"]

    def test_every_cleanup_event_has_a_korean_label(self, reducer):
        labels = reducer["labels"]
        for event_type in CLEANUP_EVENT_TYPES + DEMO_EVENT_TYPES:
            assert labels[event_type] != event_type
            assert any("\uac00" <= char <= "\ud7a3" for char in labels[event_type])
        assert "방 전체" in labels["cleanup_demo_finished"]
        assert "방 전체" in labels["cleanup_run_finished"]

    def test_phase_labels_are_human_readable(self, reducer):
        phases = reducer["phases"]
        assert phases["place"] == "장소 확인"
        assert phases["navigation"] == "이동"
        assert phases["observation"] == "관측"
        assert phases["target"] == "정리 대상 선택"
        assert phases["peer"] == "동료 선택"
        assert phases["staging"] == "정리 준비"
        assert phases["waiting"] == "동료 응답 대기"
        assert phases["response"] == "응답 확인"
        assert phases["reobservation"] == "재관측"
        assert phases["motionRequest"] == "모션 요청"
        assert phases["korean"] == "거실 관측 중"
        assert phases["blank"] == ""

    def test_provenance_details_keep_false_confirmations(self, reducer):
        provenance = reducer["provenance"]
        assert provenance["status"] == "motion_requested"
        assert provenance["observed"] is False
        details = provenance["details"]
        assert details["request_id"] == "demo-req-7"
        assert details["physical_cleanup_confirmed"] is False
        assert details["motion_completion_confirmed"] is False
        assert details["phase"] == "motion_request"
        assert details["target"] == "cloid01"
