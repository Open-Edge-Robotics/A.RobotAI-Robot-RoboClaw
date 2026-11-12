"""실 백엔드 형태(activity 객체) 교차계약 테스트.

계약:
- 백엔드는 현재 정리/시연 상태를 ``activity`` 객체에 저장하고 ``journal.tasks`` 는 빈 배열일 수 있다.
- 도메인은 activity 를 화이트리스트로 정규화하고, 그 활동을 "로컬 활동" 뷰 태스크로 투영해
  HTTP tasks/JS 태스크 목록에 보여준다(원래 에이전트 저널 태스크는 유지).
- 투영 태스크 상태는 task_status 우선, 없으면 유효한 status, 그다음
  running/failed/partial/unknown fallback 이며 waiting_for_peer 는 실제 active 대기일 때만 쓴다.
- 확인값 false(physical_cleanup_confirmed/motion_completion_confirmed)와 observed 는 승격하지 않는다.
"""

from __future__ import annotations

import pytest
from robo_claw_dashboard.domain import cooperation as coop

pytestmark = pytest.mark.unit

ACTIVITY_KEYS = {
    "source",
    "run_id",
    "session_id",
    "robot_id",
    "task_id",
    "goal",
    "phase",
    "phase_label",
    "place",
    "target",
    "peer",
    "transport",
    "status",
    "task_status",
    "result",
    "failure_reason",
    "observed",
    "physical_cleanup_confirmed",
    "motion_completion_confirmed",
    "active",
    "started_at",
    "updated_at",
    "finished_at",
    "duration_ms",
}


def activity(**overrides) -> dict:
    base = {
        "source": "cleanup_demo",
        "run_id": "cleanup-run-former0047-7",
        "session_id": "cleanup-run-former0047-7",
        "robot_id": "former0047",
        "task_id": "cleanup-task-7",
        "goal": "거실 정리 시연",
        "phase": "demo_motion",
        "phase_label": "시연 모션 요청",
        "place": "거실",
        "target": "테이블 위 컵",
        "peer": "cloid01",
        "transport": "local",
        "status": "motion_requested",
        "task_status": "",
        "result": "정리 시연 모션 요청을 전달했습니다. 실제 정리와 모션 완료는 확인되지 않았습니다.",
        "failure_reason": "",
        "observed": False,
        "physical_cleanup_confirmed": False,
        "motion_completion_confirmed": False,
        "active": True,
        "started_at": 1700000000.0,
        "updated_at": 1700000010.0,
        "finished_at": 0.0,
        "duration_ms": 10000,
    }
    base.update(overrides)
    return base


def backend_snapshot(**overrides) -> dict:
    """백엔드가 activity 를 active/goal/session_id 표시에 투영해 보내는 형태."""
    base = {
        "available": True,
        "enabled": True,
        "active": True,
        "activity_source": "cleanup_demo",
        "direct_cooperation_enabled": False,
        "local_robot_id": "former0047",
        "session_id": "cleanup-run-former0047-7",
        "goal": "거실 정리 시연",
        "stream_id": "stream-cleanup-7",
        "activity": activity(),
        "events": [
            {
                "sequence": 1,
                "event_type": "cleanup_demo_motion_requested",
                "timestamp": 1700000010.0,
                "robot_id": "former0047",
                "peer_id": "cloid01",
                "session_id": "cleanup-run-former0047-7",
                "task_id": "cleanup-task-7",
                "message_id": "",
                "reply_to": "",
                "status": "motion_requested",
                "observed": False,
                "content": "",
                "details": {
                    "source": "cleanup_demo",
                    "phase": "demo_motion",
                    "phase_label": "시연 모션 요청",
                    "place": "거실",
                    "target": "cloid01",
                    "request_id": "demo-req-7",
                    "observed": False,
                    "physical_cleanup_confirmed": False,
                    "motion_completion_confirmed": False,
                },
            }
        ],
        "tasks": [],
        "next_cursor": 2,
        "oldest_cursor": 1,
        "dropped_events": 0,
        "persistence": {"enabled": True, "error": None},
    }
    base.update(overrides)
    return base


def projected_tasks(snapshot: dict) -> list[dict]:
    return coop.normalize_cooperation(snapshot)["tasks"]


class TestNormalizeActivity:
    def test_backend_activity_is_whitelisted(self):
        payload = coop.normalize_cooperation(backend_snapshot())
        item = payload["activity"]
        assert set(item) == ACTIVITY_KEYS
        assert item["source"] == "cleanup_demo"
        assert item["run_id"] == "cleanup-run-former0047-7"
        assert item["task_id"] == "cleanup-task-7"
        assert item["phase"] == "demo_motion"
        assert item["phase_label"] == "시연 모션 요청"
        assert item["peer"] == "cloid01"
        assert item["status"] == "motion_requested"
        assert item["observed"] is False
        assert item["physical_cleanup_confirmed"] is False
        assert item["motion_completion_confirmed"] is False
        assert item["active"] is True
        assert item["duration_ms"] == 10000

    def test_unknown_keys_and_sources_are_dropped(self):
        payload = coop.normalize_cooperation(
            backend_snapshot(activity=activity(source="fake_source", secret_note="drop me"))
        )
        item = payload["activity"]
        assert item["source"] == ""
        assert "secret_note" not in item
        tidy = coop.normalize_cooperation(backend_snapshot(activity=activity(source="tidy_home")))
        assert tidy["activity"]["source"] == "tidy_home"

    def test_strings_are_bounded_and_masked(self):
        payload = coop.normalize_cooperation(
            backend_snapshot(
                activity=activity(
                    goal="정리 " + "x" * 5000,
                    result="http_readonly_token=r-secret 로 실패",
                    failure_reason="token=abc 실패",
                    place="y" * 500,
                    phase_label="z" * 500,
                )
            )
        )
        item = payload["activity"]
        assert len(item["goal"]) <= coop.MAX_CONTENT_CHARS
        assert len(item["place"]) <= coop.MAX_TEXT_CHARS
        assert len(item["phase_label"]) <= coop.MAX_TEXT_CHARS
        assert "r-secret" not in item["result"]
        assert coop.MASK in item["result"]
        assert "abc" not in item["failure_reason"]

    def test_booleans_and_numbers_are_safe(self):
        payload = coop.normalize_cooperation(
            backend_snapshot(
                activity=activity(
                    observed="yes",
                    physical_cleanup_confirmed=False,
                    motion_completion_confirmed=False,
                    active=1,
                    started_at=float("inf"),
                    updated_at="12.5",
                    finished_at=float("nan"),
                    duration_ms=float("inf"),
                )
            )
        )
        item = payload["activity"]
        assert item["observed"] is None
        assert item["physical_cleanup_confirmed"] is False
        assert item["motion_completion_confirmed"] is False
        assert item["active"] is False
        assert item["started_at"] == 0.0
        assert item["updated_at"] == 12.5
        assert item["finished_at"] == 0.0
        assert item["duration_ms"] == 0.0

    def test_observed_and_confirmed_true_values_are_kept(self):
        payload = coop.normalize_cooperation(
            backend_snapshot(activity=activity(observed=True, motion_completion_confirmed=False))
        )
        item = payload["activity"]
        assert item["observed"] is True
        assert item["motion_completion_confirmed"] is False

    def test_status_values_are_validated(self):
        upper = coop.normalize_cooperation(
            backend_snapshot(activity=activity(status="MOTION_REQUESTED", task_status=""))
        )["activity"]
        assert upper["status"] == "motion_requested"
        assert upper["task_status"] == ""
        bogus = coop.normalize_cooperation(
            backend_snapshot(activity=activity(status="EXPLODED", task_status="NOPE"))
        )["activity"]
        assert bogus["status"] == "unknown"
        assert bogus["task_status"] == "unknown"

    def test_missing_or_malformed_activity_is_none(self):
        for raw in (None, "activity", 7, [], {}):
            payload = coop.normalize_cooperation(backend_snapshot(activity=raw))
            assert payload["activity"] is None


class TestProjectedTask:
    def test_empty_journal_tasks_still_show_current_activity(self):
        tasks = projected_tasks(backend_snapshot(tasks=[]))
        assert len(tasks) == 1
        task = tasks[0]
        assert task["task_id"] == "cleanup-task-7"
        assert task["session_id"] == "cleanup-run-former0047-7"
        assert task["peer_id"] == "cloid01"
        assert task["status"] == "motion_requested"
        assert task["observed"] is False
        assert task["source"] == "local_activity"
        assert task["projected"] is True
        assert task["activity_source"] == "cleanup_demo"
        assert task["physical_cleanup_confirmed"] is False
        assert task["motion_completion_confirmed"] is False
        assert task["created_at"] == 1700000000.0
        assert task["updated_at"] == 1700000010.0
        assert task["result"]

    def test_description_combines_goal_place_and_target(self):
        task = projected_tasks(backend_snapshot())[0]
        assert "거실 정리 시연" in task["description"]
        assert "거실" in task["description"]
        assert "테이블 위 컵" in task["description"]

    def test_ids_fall_back_to_run_id(self):
        task = projected_tasks(backend_snapshot(activity=activity(task_id="", session_id="")))[0]
        assert task["task_id"] == "cleanup-run-former0047-7"
        assert task["session_id"] == "cleanup-run-former0047-7"

    def test_task_status_takes_precedence(self):
        task = projected_tasks(
            backend_snapshot(activity=activity(status="motion_requested", task_status="partial"))
        )[0]
        assert task["status"] == "partial"

    def test_invalid_task_status_falls_back_to_valid_status(self):
        task = projected_tasks(
            backend_snapshot(activity=activity(status="motion_requested", task_status="EXPLODED"))
        )[0]
        assert task["status"] == "motion_requested"

    def test_waiting_for_peer_only_when_actually_waiting(self):
        waiting = projected_tasks(
            backend_snapshot(activity=activity(status="waiting_for_peer", active=True))
        )[0]
        assert waiting["status"] == "waiting_for_peer"
        ended = projected_tasks(
            backend_snapshot(activity=activity(status="waiting_for_peer", active=False))
        )[0]
        assert ended["status"] != "waiting_for_peer"
        assert ended["status"] == "unknown"

    def test_status_fallbacks_use_running_failed_partial_unknown(self):
        failed = projected_tasks(
            backend_snapshot(activity=activity(status="", failure_reason="peer_timeout"))
        )[0]
        running = projected_tasks(backend_snapshot(activity=activity(status="", active=True)))[0]
        partial = projected_tasks(
            backend_snapshot(
                activity=activity(
                    status="",
                    active=False,
                    physical_cleanup_confirmed=True,
                    motion_completion_confirmed=False,
                )
            )
        )[0]
        unknown = projected_tasks(backend_snapshot(activity=activity(status="", active=False)))[0]
        assert failed["status"] == "failed"
        assert running["status"] == "running"
        assert partial["status"] == "partial"
        assert unknown["status"] == "unknown"

    def test_journal_tasks_are_preserved_and_capped(self):
        journal_tasks = [
            {
                "task_id": f"task-{index}",
                "session_id": "coop-session-old",
                "peer_id": "cloid01",
                "description": "원격 위임",
                "status": "queued",
                "created_at": float(index),
                "updated_at": float(index),
                "result": "",
            }
            for index in range(1, 51)
        ]
        tasks = projected_tasks(backend_snapshot(tasks=journal_tasks))
        ids = [task["task_id"] for task in tasks]
        assert len(tasks) == coop.MAX_TASKS
        assert "cleanup-task-7" in ids
        assert "task-50" in ids
        assert "task-1" not in ids

    def test_duplicate_task_id_keeps_journal_task(self):
        journal_tasks = [
            {
                "task_id": "cleanup-task-7",
                "session_id": "coop-session-old",
                "peer_id": "cloid01",
                "description": "원격 위임 태스크",
                "status": "queued",
                "created_at": 1.0,
                "updated_at": 1.0,
                "result": "",
            }
        ]
        tasks = projected_tasks(backend_snapshot(tasks=journal_tasks))
        assert len(tasks) == 1
        assert tasks[0]["description"] == "원격 위임 태스크"
        assert tasks[0]["source"] == ""
        assert tasks[0]["projected"] is False

    def test_journal_tasks_are_unchanged_without_activity(self):
        journal_tasks = [
            {"task_id": "task-9", "description": "scan", "status": "running", "updated_at": 5.0}
        ]
        tasks = projected_tasks(backend_snapshot(activity=None, tasks=journal_tasks))
        assert [task["task_id"] for task in tasks] == ["task-9"]
        assert tasks[0]["source"] == ""
        assert tasks[0]["projected"] is False


class TestPayloadActivity:
    def test_http_payload_carries_activity_and_display_fields(self):
        cached = coop.normalize_cooperation(backend_snapshot())
        payload = coop.cooperation_payload(cached)
        assert payload["active"] is True
        assert "거실" in payload["goal"]
        assert payload["session_id"] == "cleanup-run-former0047-7"
        assert payload["activity"]["phase_label"] == "시연 모션 요청"
        assert payload["activity"]["peer"] == "cloid01"
        statuses = [task["status"] for task in payload["tasks"]]
        assert statuses == ["motion_requested"]

    def test_task_status_wins_in_http_tasks(self):
        cached = coop.normalize_cooperation(
            backend_snapshot(activity=activity(task_status="completed"))
        )
        payload = coop.cooperation_payload(cached)
        assert [task["status"] for task in payload["tasks"]] == ["completed"]
        assert payload["activity"]["status"] == "motion_requested"

    def test_unavailable_payload_has_no_activity(self):
        payload = coop.cooperation_payload(None)
        assert payload["activity"] is None
        assert payload["tasks"] == []

    def test_missing_activity_keeps_none(self):
        cached = coop.normalize_cooperation(backend_snapshot(activity=None))
        payload = coop.cooperation_payload(cached)
        assert payload["activity"] is None
        assert payload["tasks"] == []
