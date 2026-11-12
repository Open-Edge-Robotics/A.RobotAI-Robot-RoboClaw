"""정리 활동 projection/ID/TTL/마스킹/로깅 계약 테스트.

저널 내부 세션 필드(`_active`/`_goal`/`_session_id`)를 바꾸지 않고 스냅샷만
정리 활동으로 projection 하는지, 64자 관측 ID/원본 request_id 보존, active 16개
포화 시 거부, TTL, 비밀값 마스킹, 로거 예외 비전파를 검증한다.
"""

from __future__ import annotations

import json
import time

import pytest
from robo_claw_agent.skills.cleanup import observation as observation_module
from robo_claw_agent.skills.cleanup.activity import (
    DEFAULT_MAX_ACTIVITIES,
    CleanupActivityStore,
    observation_id,
)
from robo_claw_agent.skills.cleanup.observation import (
    EVENT_CLEANUP_DELEGATE_FINISHED,
    EVENT_CLEANUP_HANDOFF,
    EVENT_CLEANUP_PHASE,
    SOURCE_AUTONOMOUS_COOPERATE,
    SOURCE_CLEANUP_DEMO,
    SOURCE_TIDY_HOME,
    TASK_STATUS_MOTION_REQUESTED,
    TASK_STATUS_REPORTED_TO_MAESTRO,
    open_cleanup_observation,
    safe_observe,
)
from robo_claw_agent.skills.cleanup.observer import CleanupObserver
from robo_claw_agent.skills.cooperation_skill.journal import (
    configure_cooperation_journal,
    reset_cooperation_journal,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _journal():
    reset_cooperation_journal()
    try:
        yield
    finally:
        reset_cooperation_journal()


def _observer(run_id="run-1", goal="거실", source=SOURCE_TIDY_HOME):
    return open_cleanup_observation(source=source, run_id=run_id, goal=goal)


def test_snapshot_projects_active_cleanup_without_mutating_journal_session():
    journal = configure_cooperation_journal(local_robot_id="former0047")
    observer = _observer()
    observer.start()
    observer.phase(phase="navigate", content="거실로 이동합니다.")

    snapshot = journal.snapshot()
    assert snapshot["active"] is True
    assert snapshot["goal"] == "거실"
    assert snapshot["session_id"] == observation_id("run-1")
    assert snapshot["activity_source"] == SOURCE_TIDY_HOME
    assert snapshot["enabled"] is True
    assert snapshot["direct_cooperation_enabled"] is True

    # 내부 세션 필드는 그대로다.
    assert journal.active is False
    assert journal.goal == ""
    assert journal.session_id == ""

    observer.finished(status="finished", result="거실: handled")
    finished = journal.snapshot()
    assert finished["active"] is False
    assert finished["goal"] == "거실"
    assert finished["activity_source"] == SOURCE_TIDY_HOME


def test_live_cooperation_session_takes_priority_over_stale_activity():
    journal = configure_cooperation_journal(local_robot_id="former0047")
    observer = _observer()
    observer.start()
    observer.finished(status="finished", result="거실: handled")
    session_id = journal.start_session(goal="협동 정리")

    snapshot = journal.snapshot()
    assert snapshot["active"] is True
    assert snapshot["goal"] == "협동 정리"
    assert snapshot["session_id"] == session_id
    assert snapshot["activity_source"] == SOURCE_AUTONOMOUS_COOPERATE
    assert journal.session_id == session_id
    assert journal.active is True


def test_maestro_activity_view_is_enabled_without_direct_cooperation():
    journal = configure_cooperation_journal(local_robot_id="former0047")
    observer = _observer()
    observer.start()

    snapshot = journal.snapshot(enabled=False)
    assert snapshot["enabled"] is True
    assert snapshot["direct_cooperation_enabled"] is False
    assert snapshot["active"] is True
    assert snapshot["activity_source"] == SOURCE_TIDY_HOME
    assert journal.active is False


def test_long_request_id_is_stable_64_for_activity_session_and_task():
    request_id = "r" * 80
    journal = configure_cooperation_journal(local_robot_id="cloid01")
    observer = open_cleanup_observation(
        source=SOURCE_CLEANUP_DEMO,
        run_id=request_id,
        goal="거실",
        task_id=request_id,
        session_id=request_id,
    )
    observer.start()
    observer.demo_motion_requested(
        place="거실", target="포장지", request_id=request_id, motion_name="cleanup_demo"
    )

    snapshot = journal.snapshot()
    activity = snapshot["activity"]
    mapped = observation_id(request_id)
    assert len(mapped) == 64
    assert mapped == observation_id(request_id)
    assert activity["run_id"] == mapped
    assert activity["session_id"] == mapped
    assert activity["task_id"] == mapped
    events = snapshot["events"]
    assert events
    for event in events:
        assert len(event["session_id"]) <= 64
        assert event["session_id"] == mapped
        if event["task_id"]:
            assert len(event["task_id"]) <= 64
    motion_event = next(
        event for event in events if event["event_type"] == "cleanup_demo_motion_requested"
    )
    assert motion_event["details"]["request_id"] == request_id


def test_full_active_store_refuses_new_observation_without_evicting():
    store = CleanupActivityStore(max_activities=2)
    for index in range(2):
        assert store.begin(source=SOURCE_TIDY_HOME, run_id=f"active-{index}", goal="거실")

    assert store.begin(source=SOURCE_TIDY_HOME, run_id="active-new", goal="거실") is None
    assert {item["run_id"] for item in store._activities.values()} == {
        "active-0",
        "active-1",
    }

    events = []
    observer = CleanupObserver(
        source=SOURCE_TIDY_HOME,
        run_id="active-new",
        goal="거실",
        store=store,
        recorder=lambda *args, **kwargs: events.append((args, kwargs)),
    )
    observer.start()
    observer.phase(phase="navigate", content="거실로 이동합니다.")
    observer.finished(status="failed", failure_reason="start_failed", result="시작 실패")
    assert events
    assert store.snapshot()["run_id"] in {"active-0", "active-1"}


def test_finished_activity_expires_after_ttl_but_active_stays():
    now = time.time()
    store = CleanupActivityStore(max_activities=DEFAULT_MAX_ACTIVITIES, ttl_sec=3600.0)
    store.begin(source=SOURCE_TIDY_HOME, run_id="old-finished", goal="거실", now=now - 7200)
    store.finish(("tidy_home", "old-finished"), result="done", now=now - 7200)
    store.begin(source=SOURCE_TIDY_HOME, run_id="recent-finished", goal="주방", now=now - 10)
    store.finish(("tidy_home", "recent-finished"), result="done", now=now - 10)
    store.begin(source=SOURCE_TIDY_HOME, run_id="old-active", goal="안방", now=now - 7200)

    snapshot = store.snapshot(now=now)
    assert snapshot["run_id"] == "old-active"
    store.finish(("tidy_home", "old-active"), result="done", now=now)
    assert store.snapshot(now=now)["run_id"] == "old-active"
    store.begin(source=SOURCE_TIDY_HOME, run_id="old-active", goal="안방", now=now)
    store.finish(("tidy_home", "old-active"), result="done", now=now - 7200)
    assert store.snapshot(now=now)["run_id"] == "recent-finished"


def test_activity_strings_are_masked_and_bounded():
    store = CleanupActivityStore()
    store.begin(
        source=SOURCE_TIDY_HOME,
        run_id="run-secret",
        goal="거실 token=supersecret-value",
        place="거실 password=hunter2",
    )
    store.finish(
        ("tidy_home", "run-secret"),
        result="정리 결과 auth_token=abcdef0123456789 " + "y" * 2000,
    )

    encoded = json.dumps(store.snapshot(), ensure_ascii=False)
    assert "supersecret-value" not in encoded
    assert "hunter2" not in encoded
    assert "abcdef0123456789" not in encoded
    assert "***" in encoded
    assert len(store.snapshot()["result"]) <= 500


def test_phase_label_is_korean_token_and_phase_name_is_shared():
    journal = configure_cooperation_journal(local_robot_id="former0047")
    observer = _observer()
    observer.start()
    observer.phase(phase="navigate", content="거실로 이동합니다.")

    event = next(
        item for item in journal.snapshot()["events"] if item["event_type"] == EVENT_CLEANUP_PHASE
    )
    assert event["details"]["phase"] == "navigate"
    assert event["details"]["phase_label"] == "장소로 이동"
    assert journal.snapshot()["activity"]["phase"] == "navigate"
    assert journal.snapshot()["activity"]["phase_label"] == "장소로 이동"


def test_local_motion_request_and_finish_are_not_remote_observed():
    journal = configure_cooperation_journal(local_robot_id="cloid01")
    observer = open_cleanup_observation(source=SOURCE_CLEANUP_DEMO, run_id="demo-run", goal="거실")
    observer.start()
    observer.demo_motion_requested(
        place="거실", target="포장지", request_id="demo-run", motion_name="cleanup_demo"
    )
    observer.finished(
        status="motion_requested",
        result="모션 요청 접수",
        task_status=TASK_STATUS_MOTION_REQUESTED,
    )

    snapshot = journal.snapshot()
    motion = next(
        item for item in snapshot["events"] if item["event_type"] == "cleanup_demo_motion_requested"
    )
    assert motion["details"]["observed"] is False
    finished = next(
        item for item in snapshot["events"] if item["event_type"] == "cleanup_demo_finished"
    )
    assert finished["details"]["observed"] is False
    assert snapshot["activity"]["observed"] is False


def test_maestro_handoff_records_reported_status_on_event_and_activity():
    journal = configure_cooperation_journal(local_robot_id="former0047")
    observer = _observer()
    observer.start()
    observer.handoff(
        place="거실",
        target="포장지",
        handoff_kind="cleanup_demo",
        peer="cloid01",
        transport="grpc",
        send_path="maestro_report",
        reason="Maestro에 보고합니다.",
        request_id="demo-request-9",
    )

    handoff = next(
        item for item in journal.snapshot()["events"] if item["event_type"] == EVENT_CLEANUP_HANDOFF
    )
    assert handoff["status"] == TASK_STATUS_REPORTED_TO_MAESTRO
    assert handoff["details"]["task_status"] == TASK_STATUS_REPORTED_TO_MAESTRO
    assert handoff["details"]["observed"] is False
    assert journal.snapshot()["activity"]["task_status"] == TASK_STATUS_REPORTED_TO_MAESTRO


def test_generic_delegate_finish_never_stays_waiting_for_peer():
    events = []
    observer = CleanupObserver(
        source=SOURCE_TIDY_HOME,
        run_id="run-2",
        goal="거실",
        recorder=lambda *args, **kwargs: events.append((args, kwargs)),
    )
    observer.start()
    observer.delegate_started(peer="cloid01", request_id="demo-2")
    observer.delegate_finished(
        peer="cloid01", request_id="demo-2", success=True, message="작업 완료"
    )

    finished = next(item for item in events if item[0][0] == EVENT_CLEANUP_DELEGATE_FINISHED)
    details = finished[1]["details"]
    assert details["task_status"] == "unknown"
    assert details["observed"] is False
    assert details["failure_reason"] == "result_unconfirmed"


def test_broken_logger_and_observer_never_raise(monkeypatch):
    def broken_debug(*_args, **_kwargs):
        raise RuntimeError("logging sink unavailable")

    monkeypatch.setattr(observation_module.logger, "debug", broken_debug)

    class _Exploding:
        def __getattr__(self, _name):
            def _raise(*_args, **_kwargs):
                raise RuntimeError("observer unavailable")

            return _raise

    safe_observe(_Exploding(), "start")
    observer = CleanupObserver(
        source=SOURCE_TIDY_HOME,
        run_id="run-broken",
        goal="거실",
        recorder=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("recorder down")),
    )
    observer.start()
    observer.phase(phase="navigate", content="거실로 이동합니다.")
    observer.finished(status="failed", failure_reason="run_error", result="실패")

    # 팩토리 내부 저널 조회가 실패하고 로거도 깨져도 관측기는 생성된다.
    import robo_claw_agent.skills.cooperation_skill.journal as journal_module

    monkeypatch.setattr(
        journal_module,
        "get_cooperation_journal",
        lambda: (_ for _ in ()).throw(RuntimeError("journal down")),
    )
    fallback = observation_module.open_cleanup_observation(
        source=SOURCE_TIDY_HOME, run_id="run-fallback"
    )
    fallback.start()


def test_journal_tasks_remain_empty_for_cleanup_activity():
    journal = configure_cooperation_journal(local_robot_id="former0047")
    observer = _observer()
    observer.start()
    observer.finished(status="finished", result="거실: no_target")
    assert journal.snapshot()["tasks"] == []


def test_root_no_target_result_states_cleanup_not_completed():
    from types import SimpleNamespace

    from robo_claw_agent.skills.autonomous_skill import globals as autonomous_globals
    from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

    autonomous_globals._AUTONOMOUS_ACTIVE.clear()
    journal = configure_cooperation_journal(local_robot_id="former0047")
    skill = TidyHomeSkill()
    skill.node = SimpleNamespace(
        _agent_id="former0047", _skills=SimpleNamespace(has_skill=lambda _name: False), _memory=None
    )
    skill.send_user_message = lambda _message: None
    skill._collect_places = lambda _scope, _target, max_places=0: (
        [{"name": "거실", "position": {"x": 1.0, "y": 2.0}}],
        None,
    )
    skill._visit_place = lambda _place: (True, "도착했습니다.")
    skill._analyze_current_scene = lambda _place: {
        "success": True,
        "analysis": "정리할 대상이 없습니다.",
        "detected_objects": [],
    }
    skill._cloid_indicator_enabled = lambda: False

    skill.execute({"scope": "room", "target_location": "거실", "wait_for_completion": True})

    activity = journal.snapshot()["activity"]
    assert activity["status"] == "unknown"
    assert "정리 완료가 아닙니다" in activity["result"]
    autonomous_globals._AUTONOMOUS_ACTIVE.clear()
