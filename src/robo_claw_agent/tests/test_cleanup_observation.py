"""정리 실행 관측(협동 저널 이벤트 + bounded 활동 상태) 회귀 테스트.

``tidy_home``/``cleanup_demo`` 는 사용자 메시지와 실험 metrics 만 남기던 경로였다.
이 테스트는 정리 실행이 협동 저널(QueryState 가 노출하는 스냅샷)에 시작/단계/종료
관측을 남기고, 자율협동 세션 필드와 상대 태스크 결과 어휘를 건드리지 않는지
검증한다. ROS2/네트워크 없이 실행한다.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
import robo_claw_agent.skills.tidy_home_skill as tidy_home_module
from robo_claw_agent.skills.autonomous_skill import globals as autonomous_globals
from robo_claw_agent.skills.cleanup import demo_skill as demo_module
from robo_claw_agent.skills.cleanup.activity import (
    DEFAULT_MAX_ACTIVITIES,
    CleanupActivityStore,
)
from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill
from robo_claw_agent.skills.cleanup.observation import (
    CLEANUP_TASK_STATUSES,
    EVENT_CLEANUP_DELEGATE_FINISHED,
    EVENT_CLEANUP_DELEGATE_STARTED,
    EVENT_CLEANUP_DEMO_FAILED,
    EVENT_CLEANUP_DEMO_FINISHED,
    EVENT_CLEANUP_DEMO_MOTION_REQUESTED,
    EVENT_CLEANUP_DEMO_PHASE,
    EVENT_CLEANUP_DEMO_STARTED,
    EVENT_CLEANUP_HANDOFF,
    EVENT_CLEANUP_PHASE,
    EVENT_CLEANUP_RUN_FINISHED,
    EVENT_CLEANUP_RUN_STARTED,
    SOURCE_CLEANUP_DEMO,
    SOURCE_TIDY_HOME,
    TASK_STATUS_CANCEL_REQUESTED,
    TASK_STATUS_MOTION_REQUESTED,
    TASK_STATUS_REPORTED_TO_MAESTRO,
    TASK_STATUS_WAITING_FOR_PEER,
)
from robo_claw_agent.skills.cooperation_skill.journal import (
    configure_cooperation_journal,
    reset_cooperation_journal,
)
from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill

pytestmark = pytest.mark.unit

ROOM = "거실"
PEER = "cloid01"
REQUESTER = "former0047"


class _FakeSkills:
    def __init__(self, available=(), results=None):
        self.available = set(available)
        self.calls = []
        self.results = dict(results or {})

    def has_skill(self, name):
        return name in self.available

    def execute(self, name, params, **kwargs):
        self.calls.append((name, params, kwargs))
        handler = self.results.get(name)
        if callable(handler):
            return handler(params, kwargs)
        if handler is not None:
            return handler
        return SimpleNamespace(success=True, message="ok", result_data={})


def _node(agent_id, available=(), results=None):
    return SimpleNamespace(
        _agent_id=agent_id,
        _skills=_FakeSkills(available, results),
        _memory=None,
    )


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _event_types(journal) -> list[str]:
    return [event["event_type"] for event in journal.snapshot()["events"]]


@pytest.fixture(autouse=True)
def _isolated_journal_and_slot():
    autonomous_globals._AUTONOMOUS_ACTIVE.clear()
    reset_cooperation_journal()
    try:
        yield
    finally:
        autonomous_globals._AUTONOMOUS_ACTIVE.clear()
        reset_cooperation_journal()


def _room_skill(monkeypatch, *, scene=None, agent_id=REQUESTER):
    skill = TidyHomeSkill()
    skill.node = _node(agent_id)
    skill.send_user_message = lambda _message: None
    skill._collect_places = lambda _scope, _target, max_places=0: (
        [{"name": ROOM, "position": {"x": 1.0, "y": 2.0}}],
        None,
    )
    skill._visit_place = lambda _place: (True, "도착했습니다.")
    skill._analyze_current_scene = lambda _place: (
        scene
        or {
            "success": True,
            "analysis": "거실 바닥에 포장지가 보입니다.",
            "detected_objects": [{"name": "포장지"}],
        }
    )
    skill._handle_cleanup_target = lambda **_kwargs: "handled"
    skill._cloid_indicator_enabled = lambda: False
    return skill


def test_plain_room_cleanup_publishes_journal_activity_and_events(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    skill = _room_skill(monkeypatch)

    result = skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    assert result["success"] is True
    snapshot = journal.snapshot()
    types = _event_types(journal)
    assert EVENT_CLEANUP_RUN_STARTED in types
    assert EVENT_CLEANUP_PHASE in types
    assert EVENT_CLEANUP_RUN_FINISHED in types
    activity = snapshot["activity"]
    assert activity is not None
    assert activity["source"] == SOURCE_TIDY_HOME
    assert activity["run_id"] == result["run_id"]
    assert activity["robot_id"] == REQUESTER
    assert activity["active"] is False
    assert activity["goal"]
    assert activity["result"]
    assert snapshot["activity_source"] == SOURCE_TIDY_HOME
    assert snapshot["direct_cooperation_enabled"] is True


def test_blocked_navigation_exposes_active_phase_before_completion(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    skill = _room_skill(monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def visit(_place):
        entered.set()
        release.wait(5.0)
        return True, "도착했습니다."

    skill._visit_place = visit
    result = skill.execute({"scope": "room", "target_location": ROOM})

    assert result["status"] == "started"
    assert entered.wait(5.0)
    snapshot = journal.snapshot()
    activity = snapshot["activity"]
    assert activity["active"] is True
    assert activity["phase"] == "navigate"
    assert snapshot["enabled"] is True
    assert all(event["session_id"] == result["run_id"] for event in snapshot["events"])

    release.set()
    assert _wait_for(lambda: journal.snapshot()["activity"]["active"] is False)
    final = journal.snapshot()["activity"]
    assert final["status"] == "finished"
    assert final["result"]


def test_demo_handoff_case_records_requester_wait_and_truthful_motion_request(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup.protocol import decode_cleanup_demo_request

    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, capability: (
            {"peer_name": PEER, "transport": "grpc"} if capability == "cleanup_demo" else None
        ),
    )

    def peer_response(params, _kwargs):
        payload = decode_cleanup_demo_request(params["instruction"])
        return SimpleNamespace(
            success=True,
            message="motion requested",
            result_data={
                "peer_result": {
                    "status": "motion_requested",
                    "observed": True,
                    "request_id": payload["request_id"],
                    "physical_cleanup_confirmed": False,
                    "motion_completion_confirmed": False,
                },
            },
        )

    skill = TidyHomeSkill()
    skill.node = _node(
        REQUESTER,
        available={"move_relative", "call_peer_robot"},
        results={"call_peer_robot": peer_response},
    )
    skill.send_user_message = lambda _message: None
    skill._collect_places = lambda _scope, _target, max_places=0: (
        [{"name": ROOM, "position": {"x": 1.0, "y": 2.0}}],
        None,
    )
    skill._visit_place = lambda _place: (True, "도착했습니다.")
    skill._analyze_current_scene = lambda _place: {
        "success": True,
        "analysis": "거실 바닥에 포장지가 보입니다.",
        "detected_objects": [{"name": "포장지"}],
    }
    skill._cloid_indicator_enabled = lambda: False

    result = skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    assert result["status"] == "completed"
    snapshot = journal.snapshot()
    types = _event_types(journal)
    assert EVENT_CLEANUP_HANDOFF in types
    assert EVENT_CLEANUP_DELEGATE_STARTED in types
    assert EVENT_CLEANUP_DELEGATE_FINISHED in types
    handoff = next(e for e in snapshot["events"] if e["event_type"] == EVENT_CLEANUP_HANDOFF)
    assert handoff["peer_id"] == PEER
    assert handoff["details"]["transport"] == "grpc"
    request_id = handoff["details"]["request_id"]
    assert request_id
    delegate_events = [
        e
        for e in snapshot["events"]
        if e["event_type"] in {EVENT_CLEANUP_DELEGATE_STARTED, EVENT_CLEANUP_DELEGATE_FINISHED}
    ]
    assert all(event["task_id"] == request_id for event in delegate_events)
    assert all(event["robot_id"] == REQUESTER for event in delegate_events)
    activity = snapshot["activity"]
    assert activity["task_id"] == request_id
    assert activity["task_status"] == TASK_STATUS_MOTION_REQUESTED
    assert activity["physical_cleanup_confirmed"] is False
    assert activity["motion_completion_confirmed"] is False
    assert activity["active"] is False
    assert "실제 물체 정리 완료는 확인되지 않았습니다" in activity["result"]
    assert "정리 완료했습니다" not in activity["result"]


def test_peer_wait_is_visible_while_delegate_call_is_blocked(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup.protocol import decode_cleanup_demo_request

    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, capability: (
            {"peer_name": PEER, "transport": "grpc"} if capability == "cleanup_demo" else None
        ),
    )
    entered = threading.Event()
    release = threading.Event()

    def peer_response(params, _kwargs):
        entered.set()
        release.wait(5.0)
        payload = decode_cleanup_demo_request(params["instruction"])
        return SimpleNamespace(
            success=True,
            message="motion requested",
            result_data={
                "peer_result": {
                    "status": "motion_requested",
                    "observed": True,
                    "request_id": payload["request_id"],
                },
            },
        )

    skill = TidyHomeSkill()
    skill.node = _node(
        REQUESTER,
        available={"move_relative", "call_peer_robot"},
        results={"call_peer_robot": peer_response},
    )
    skill.send_user_message = lambda _message: None
    skill._collect_places = lambda _scope, _target, max_places=0: (
        [{"name": ROOM, "position": {"x": 1.0, "y": 2.0}}],
        None,
    )
    skill._visit_place = lambda _place: (True, "도착했습니다.")
    skill._analyze_current_scene = lambda _place: {
        "success": True,
        "analysis": "거실 바닥에 포장지가 보입니다.",
        "detected_objects": [{"name": "포장지"}],
    }
    skill._cloid_indicator_enabled = lambda: False

    result = skill.execute({"scope": "room", "target_location": ROOM})
    assert result["status"] == "started"
    assert entered.wait(5.0)
    activity = journal.snapshot()["activity"]
    assert activity["active"] is True
    assert activity["phase"] == "wait_peer"
    assert activity["task_status"] == TASK_STATUS_WAITING_FOR_PEER
    assert activity["peer"] == PEER

    release.set()
    assert _wait_for(lambda: journal.snapshot()["activity"]["active"] is False)


def test_maestro_local_cleanup_is_visible_without_direct_cooperation(monkeypatch):
    monkeypatch.setenv("MAESTRO_ENABLED", "true")
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    skill = _room_skill(monkeypatch)

    result = skill.execute({"scope": "room", "target_location": ROOM})

    assert result["success"] is True
    snapshot = journal.snapshot(enabled=False)
    assert snapshot["direct_cooperation_enabled"] is False
    assert snapshot["enabled"] is True
    assert snapshot["active"] is False
    assert snapshot["activity_source"] == SOURCE_TIDY_HOME
    assert snapshot["activity"]["active"] is False


def test_cleanup_observation_keeps_concurrent_autonomous_cooperate_session(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    session_id = journal.start_session(goal="거실 협동 정리")
    skill = _room_skill(monkeypatch)

    skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    assert journal.session_id == session_id
    assert journal.goal == "거실 협동 정리"
    assert journal.active is True
    snapshot = journal.snapshot()
    assert snapshot["session_id"] == session_id
    assert snapshot["goal"] == "거실 협동 정리"
    assert snapshot["active"] is True
    assert snapshot["activity"]["source"] == SOURCE_TIDY_HOME


def test_maestro_handoff_reports_pending_and_never_claims_remote_success(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    monkeypatch.setenv("MAESTRO_ENABLED", "true")
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, capability: (
            {"peer_name": PEER, "transport": "grpc"} if capability == "cleanup_demo" else None
        ),
    )
    skill = TidyHomeSkill()
    skill.node = _node(REQUESTER)
    skill.send_user_message = lambda _message: None
    skill._collect_places = lambda _scope, _target, max_places=0: (
        [{"name": ROOM, "position": {"x": 1.0, "y": 2.0}}],
        None,
    )
    skill._visit_place = lambda _place: (True, "도착했습니다.")
    skill._analyze_current_scene = lambda _place: {
        "success": True,
        "analysis": "거실 바닥에 포장지가 보입니다.",
        "detected_objects": [{"name": "포장지"}],
    }
    skill._cloid_indicator_enabled = lambda: False

    result = skill.execute({"scope": "room", "target_location": ROOM})

    assert result["status"] == "completed"
    snapshot = journal.snapshot(enabled=False)
    handoff = next(e for e in snapshot["events"] if e["event_type"] == EVENT_CLEANUP_HANDOFF)
    assert handoff["details"]["send_path"] == "maestro_report"
    assert handoff["details"]["observed"] is False
    assert snapshot["activity"]["task_status"] == TASK_STATUS_REPORTED_TO_MAESTRO
    assert snapshot["activity"]["task_status"] in CLEANUP_TASK_STATUSES


def test_no_target_and_cancel_branches_are_visible(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    skill = _room_skill(
        monkeypatch,
        scene={"success": True, "analysis": "정리할 대상이 없습니다.", "detected_objects": []},
    )

    skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    activity = journal.snapshot()["activity"]
    assert activity["active"] is False
    assert "no_target" in activity["result"]

    autonomous_globals._AUTONOMOUS_ACTIVE.clear()
    journal2 = configure_cooperation_journal(local_robot_id=REQUESTER)
    skill = _room_skill(monkeypatch)

    def cancel_during_visit(_place):
        autonomous_globals._AUTONOMOUS_ACTIVE.clear()
        return True, "도착했습니다."

    skill._visit_place = cancel_during_visit
    result = skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    assert result["failure_reason"] == "cancelled"
    activity = journal2.snapshot()["activity"]
    assert activity["status"] == "cancel_requested"
    assert activity["task_status"] == TASK_STATUS_CANCEL_REQUESTED
    assert activity["active"] is False
    assert autonomous_globals._AUTONOMOUS_ACTIVE.is_set() is False


def test_missing_room_and_busy_request_are_recorded_without_overwriting_active_run(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    missing = TidyHomeSkill()
    missing.node = _node(REQUESTER)
    missing.send_user_message = lambda _message: None
    missing._collect_places = lambda _scope, _target, max_places=0: (
        [],
        "requested_location_not_found",
    )

    rejected = missing.execute({"scope": "room", "target_location": "없는방"})

    assert rejected["failure_reason"] == "requested_location_not_found"
    snapshot = journal.snapshot()
    finished = next(e for e in snapshot["events"] if e["event_type"] == EVENT_CLEANUP_RUN_FINISHED)
    assert finished["details"]["failure_reason"] == "requested_location_not_found"
    assert snapshot["activity"]["active"] is False

    journal2 = configure_cooperation_journal(local_robot_id=REQUESTER)
    entered = threading.Event()
    release = threading.Event()
    running = _room_skill(monkeypatch)

    def visit(_place):
        entered.set()
        release.wait(5.0)
        return True, "도착했습니다."

    running._visit_place = visit
    started = running.execute({"scope": "room", "target_location": ROOM})
    assert entered.wait(5.0)

    busy = _room_skill(monkeypatch).execute({"scope": "room", "target_location": ROOM})

    assert busy["failure_reason"] == "autonomous_busy"
    activity = journal2.snapshot()["activity"]
    assert activity["active"] is True
    assert activity["run_id"] == started["run_id"]
    busy_finished = [
        e
        for e in journal2.snapshot()["events"]
        if e["event_type"] == EVENT_CLEANUP_RUN_FINISHED
        and e["details"]["failure_reason"] == "autonomous_busy"
    ]
    assert busy_finished and busy_finished[0]["session_id"] != started["run_id"]

    release.set()
    assert _wait_for(lambda: journal2.snapshot()["activity"]["active"] is False)


def test_worker_start_failure_is_recorded_and_slot_released(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    skill = _room_skill(monkeypatch)

    class _BrokenThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("thread unavailable")

    monkeypatch.setattr(tidy_home_module.threading, "Thread", _BrokenThread)

    result = skill.execute({"scope": "room", "target_location": ROOM})

    assert result["failure_reason"] == "start_failed"
    finished = next(
        e for e in journal.snapshot()["events"] if e["event_type"] == EVENT_CLEANUP_RUN_FINISHED
    )
    assert finished["details"]["failure_reason"] == "start_failed"
    assert journal.snapshot()["activity"]["active"] is False
    assert autonomous_globals._AUTONOMOUS_ACTIVE.is_set() is False


def test_observation_exceptions_do_not_change_cleanup_result(monkeypatch):
    class _ExplodingObserver:
        def __getattr__(self, _name):
            def _raise(*_args, **_kwargs):
                raise RuntimeError("observer unavailable")

            return _raise

    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    monkeypatch.setattr(
        tidy_home_module, "open_cleanup_observation", lambda **_kwargs: _ExplodingObserver()
    )
    skill = _room_skill(monkeypatch)

    result = skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    assert result["success"] is True
    assert result["status"] == "completed"

    monkeypatch.setattr(
        tidy_home_module,
        "open_cleanup_observation",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("factory down")),
    )
    skill = _room_skill(monkeypatch)

    result = skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    assert result["success"] is True
    assert autonomous_globals._AUTONOMOUS_ACTIVE.is_set() is False
    assert journal.snapshot()["activity"] is None


def test_cleanup_demo_receiver_records_its_own_phases_and_motion_request(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=PEER)
    motion = {
        "motion_id": 77,
        "motion_name": "cleanup_demo",
        "pre_id": None,
        "intent_id": "cleanup_demo",
        "description": "검토된 임시 정리 시연 동작",
        "mask": "WH",
    }
    skills = _FakeSkills(
        available={"list_cloid_motions", "execute_cloid_motion"},
        results={
            "list_cloid_motions": SimpleNamespace(
                success=True, message="ok", result_data={"motions": [motion]}
            ),
            "execute_cloid_motion": SimpleNamespace(success=True, message="START published"),
        },
    )
    node = SimpleNamespace(
        _agent_id=PEER,
        _skills=skills,
        _memory=object(),
        get_parameter=lambda _name: SimpleNamespace(value="[77]"),
    )

    monkeypatch.setattr(
        TidyHomeSkill,
        "_collect_places",
        lambda _self, _scope, location: ([{"name": location}], None),
    )
    monkeypatch.setattr(
        TidyHomeSkill, "_visit_place", lambda _self, _place: (True, "도착했습니다.")
    )
    monkeypatch.setattr(
        TidyHomeSkill,
        "_analyze_current_scene",
        lambda _self, _place: {
            "success": True,
            "analysis": "거실 바닥에 포장지가 보입니다.",
            "detected_objects": [{"name": "포장지"}],
        },
    )

    skill = CleanupDemoSkill()
    skill.set_node(node)
    skill.send_user_message = lambda _message: None
    request_id = "demo-request-0001"

    result = skill.execute(
        {
            "request_id": request_id,
            "target_location": ROOM,
            "target_object": "포장지",
            "scene_summary": "거실 바닥에 포장지가 보입니다.",
        }
    )

    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert result["physical_cleanup_confirmed"] is False
    snapshot = journal.snapshot()
    types = _event_types(journal)
    assert EVENT_CLEANUP_DEMO_STARTED in types
    assert EVENT_CLEANUP_DEMO_PHASE in types
    assert EVENT_CLEANUP_DEMO_MOTION_REQUESTED in types
    assert EVENT_CLEANUP_DEMO_FINISHED in types
    assert all(
        event["session_id"] == request_id
        for event in snapshot["events"]
        if event["event_type"].startswith("cleanup_demo")
    )
    assert all(event["robot_id"] == PEER for event in snapshot["events"])
    activity = snapshot["activity"]
    assert activity["source"] == SOURCE_CLEANUP_DEMO
    assert activity["run_id"] == request_id
    assert activity["task_status"] == TASK_STATUS_MOTION_REQUESTED
    assert activity["physical_cleanup_confirmed"] is False
    assert activity["motion_completion_confirmed"] is False
    assert activity["active"] is False


def test_cleanup_demo_receiver_failure_keeps_existing_payload(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=PEER)
    skills = _FakeSkills(available={"list_cloid_motions", "execute_cloid_motion"})
    node = SimpleNamespace(
        _agent_id=PEER,
        _skills=skills,
        get_parameter=lambda _name: SimpleNamespace(value="[77]"),
    )
    monkeypatch.setattr(
        TidyHomeSkill,
        "_collect_places",
        lambda _self, _scope, location: ([{"name": location}], None),
    )
    monkeypatch.setattr(
        TidyHomeSkill, "_visit_place", lambda _self, _place: (False, "경로를 찾지 못했습니다.")
    )

    skill = CleanupDemoSkill()
    skill.set_node(node)
    skill.send_user_message = lambda _message: None

    result = skill.execute(
        {
            "request_id": "demo-request-0002",
            "target_location": ROOM,
            "target_object": "포장지",
            "scene_summary": "거실 바닥에 포장지가 보입니다.",
        }
    )

    assert result["success"] is False
    assert result["failure_reason"] == "location_unreachable"
    assert result["recoverable"] is True
    snapshot = journal.snapshot()
    failed = [
        event for event in snapshot["events"] if event["event_type"] == EVENT_CLEANUP_DEMO_FAILED
    ]
    assert failed and failed[0]["details"]["failure_reason"] == "location_unreachable"
    assert snapshot["activity"]["active"] is False
    assert snapshot["activity"]["status"] == "failed"


def test_cleanup_activity_store_is_bounded_and_never_drops_active_run():
    store = CleanupActivityStore(max_activities=DEFAULT_MAX_ACTIVITIES)
    for index in range(DEFAULT_MAX_ACTIVITIES + 5):
        run_id = f"run-{index}"
        store.begin(source=SOURCE_TIDY_HOME, run_id=run_id, goal=ROOM)
        store.finish((SOURCE_TIDY_HOME, run_id), result="done")

    snapshot = store.snapshot()
    assert snapshot is not None
    assert snapshot["run_id"] == f"run-{DEFAULT_MAX_ACTIVITIES + 4}"

    active_key = ("tidy_home", "active-run")
    store.begin(source=SOURCE_TIDY_HOME, run_id="active-run", goal=ROOM)
    for index in range(DEFAULT_MAX_ACTIVITIES + 5):
        store.begin(source=SOURCE_CLEANUP_DEMO, run_id=f"demo-{index}", goal=ROOM)
        store.finish((SOURCE_CLEANUP_DEMO, f"demo-{index}"), result="done")

    assert store.get(active_key)["active"] is True
    assert store.snapshot()["run_id"] == "active-run"


def test_demo_source_module_is_used_for_receiver(monkeypatch):
    """수신 로봇의 시연 상태는 자기 로컬 저널에만 남고 상대 태스크로 승격되지 않는다."""
    journal = configure_cooperation_journal(local_robot_id=PEER)
    skills = _FakeSkills(available={"list_cloid_motions", "execute_cloid_motion"})
    node = SimpleNamespace(
        _agent_id=PEER,
        _skills=skills,
        get_parameter=lambda _name: SimpleNamespace(value="[77]"),
    )
    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda _self, _scope, location: ([], "no_memory")
    )
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)

    skill = CleanupDemoSkill()
    skill.set_node(node)
    skill.send_user_message = lambda _message: None
    result = skill.execute(
        {
            "request_id": "demo-request-0003",
            "target_location": ROOM,
            "target_object": "포장지",
            "scene_summary": "거실 바닥에 포장지가 보입니다.",
        }
    )

    assert result["failure_reason"] == "no_memory"
    snapshot = journal.snapshot()
    assert snapshot["tasks"] == []
    assert snapshot["activity"]["source"] == SOURCE_CLEANUP_DEMO
    assert snapshot["activity"]["status"] == "failed"


def test_former_to_cloid_demo_request_correlates_both_local_journals(monkeypatch):
    """former0047 -> cloid01 시연 요청의 request_id 가 두 로봇의 로컬 관측을 잇는다."""
    from robo_claw_agent.skills.autonomous_skill import execution_nodes
    from robo_claw_agent.skills.cleanup.protocol import decode_cleanup_demo_request

    requester_journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    receiver_journal = None
    # 두 로봇은 다른 프로세스다. 수신 로봇의 자율 슬롯 점유가 송신 로봇의
    # 진행 중인 정리 슬롯을 건드리지 않도록 수신 경로만 분리한다.
    monkeypatch.setattr(demo_module, "claim_autonomous", lambda _event: True)
    monkeypatch.setattr(demo_module, "release_autonomous", lambda: None)

    def peer_response(params, _kwargs):
        nonlocal receiver_journal
        payload = decode_cleanup_demo_request(params["instruction"])
        motion = {
            "motion_id": 77,
            "motion_name": "cleanup_demo",
            "pre_id": None,
            "intent_id": "cleanup_demo",
            "description": "검토된 임시 정리 시연 동작",
            "mask": "WH",
        }
        receiver_journal = configure_cooperation_journal(local_robot_id=PEER)
        receiver_node = SimpleNamespace(
            _agent_id=PEER,
            _skills=_FakeSkills(
                available={"list_cloid_motions", "execute_cloid_motion"},
                results={
                    "list_cloid_motions": SimpleNamespace(
                        success=True, message="ok", result_data={"motions": [motion]}
                    ),
                    "execute_cloid_motion": SimpleNamespace(
                        success=True, message="START published"
                    ),
                },
            ),
            _memory=object(),
            get_parameter=lambda _name: SimpleNamespace(value="[77]"),
        )
        receiver = CleanupDemoSkill()
        receiver.set_node(receiver_node)
        receiver.send_user_message = lambda _message: None
        received = receiver.execute(payload)
        return SimpleNamespace(
            success=received["success"],
            message=received["message"],
            result_data={
                "peer_result": {
                    "status": "motion_requested",
                    "observed": True,
                    "request_id": payload["request_id"],
                    "physical_cleanup_confirmed": False,
                    "motion_completion_confirmed": False,
                },
            },
        )

    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, capability: (
            {"peer_name": PEER, "transport": "grpc"} if capability == "cleanup_demo" else None
        ),
    )
    monkeypatch.setattr(
        TidyHomeSkill,
        "_collect_places",
        lambda _self, _scope, location, max_places=0: ([{"name": location}], None),
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda _self, _place: (True, "arrived"))
    monkeypatch.setattr(
        TidyHomeSkill,
        "_analyze_current_scene",
        lambda _self, _place: {
            "success": True,
            "analysis": "거실 바닥에 포장지가 보입니다.",
            "detected_objects": [{"name": "포장지"}],
        },
    )

    requester = TidyHomeSkill()
    requester.node = _node(
        REQUESTER,
        available={"move_relative", "call_peer_robot"},
        results={"call_peer_robot": peer_response},
    )
    requester.send_user_message = lambda _message: None
    requester._cloid_indicator_enabled = lambda: False

    result = requester.execute(
        {"scope": "room", "target_location": ROOM, "wait_for_completion": True}
    )

    assert result["status"] == "completed"
    requester_snapshot = requester_journal.snapshot()
    receiver_snapshot = receiver_journal.snapshot()
    assert requester_snapshot["activity"]["task_status"] == TASK_STATUS_MOTION_REQUESTED
    assert requester_snapshot["activity"]["physical_cleanup_confirmed"] is False
    assert receiver_snapshot["activity"]["source"] == SOURCE_CLEANUP_DEMO
    assert receiver_snapshot["activity"]["task_status"] == TASK_STATUS_MOTION_REQUESTED
    assert receiver_snapshot["activity"]["physical_cleanup_confirmed"] is False
    request_id = receiver_snapshot["activity"]["run_id"]
    assert request_id
    requester_delegate = [
        event
        for event in requester_snapshot["events"]
        if event["event_type"] == EVENT_CLEANUP_DELEGATE_STARTED
    ]
    assert requester_delegate[0]["task_id"] == request_id
    assert requester_delegate[0]["robot_id"] == REQUESTER
    assert all(
        event["robot_id"] == PEER
        for event in receiver_snapshot["events"]
        if event["event_type"].startswith("cleanup_demo")
    )
    assert receiver_snapshot["events"][0]["session_id"] == request_id


def test_run_exception_is_visible_and_releases_slot(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    skill = _room_skill(monkeypatch)

    def broken_observation(_place):
        raise RuntimeError("observation backend crashed")

    skill._analyze_current_scene = broken_observation

    result = skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    assert result["failure_reason"] == "run_error"
    activity = journal.snapshot()["activity"]
    assert activity["status"] == "failed"
    assert activity["active"] is False
    finished = next(
        event
        for event in journal.snapshot()["events"]
        if event["event_type"] == EVENT_CLEANUP_RUN_FINISHED
    )
    assert finished["details"]["failure_reason"] == "run_error"
    assert autonomous_globals._AUTONOMOUS_ACTIVE.is_set() is False


def test_staging_failure_is_visible_and_peer_request_is_not_sent(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, capability: (
            {"peer_name": PEER, "transport": "grpc"} if capability == "cleanup_demo" else None
        ),
    )
    skill = TidyHomeSkill()
    skill.node = _node(
        REQUESTER,
        available={"move_relative", "call_peer_robot"},
        results={"move_relative": SimpleNamespace(success=False, message="nav rejected")},
    )
    skill.send_user_message = lambda _message: None
    skill._collect_places = lambda _scope, _target, max_places=0: (
        [{"name": ROOM, "position": {"x": 1.0, "y": 2.0}}],
        None,
    )
    skill._visit_place = lambda _place: (True, "도착했습니다.")
    skill._analyze_current_scene = lambda _place: {
        "success": True,
        "analysis": "거실 바닥에 포장지가 보입니다.",
        "detected_objects": [{"name": "포장지"}],
    }
    skill._cloid_indicator_enabled = lambda: False

    skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    types = _event_types(journal)
    assert EVENT_CLEANUP_DELEGATE_STARTED not in types
    assert EVENT_CLEANUP_DELEGATE_FINISHED not in types
    stage_events = [
        event
        for event in journal.snapshot()["events"]
        if event["event_type"] == EVENT_CLEANUP_PHASE and event["details"]["phase"] == "stage"
    ]
    assert stage_events
    assert any(
        event["details"]["failure_reason"] == "handoff_staging_failed" for event in stage_events
    )
    activity = journal.snapshot()["activity"]
    assert activity["active"] is False
    assert activity["failure_reason"] == "handoff_staging_failed"


def test_delegate_timeout_is_recorded_as_unconfirmed(monkeypatch):
    from robo_claw_agent.skills.autonomous_skill import execution_nodes

    journal = configure_cooperation_journal(local_robot_id=REQUESTER)
    monkeypatch.setattr(
        execution_nodes,
        "_find_connected_peer_with_capability",
        lambda _node, capability: (
            {"peer_name": PEER, "transport": "grpc"} if capability == "cleanup_demo" else None
        ),
    )
    skill = TidyHomeSkill()
    skill.node = _node(
        REQUESTER,
        available={"move_relative", "call_peer_robot"},
        results={
            "call_peer_robot": SimpleNamespace(
                success=False, message="timeout: 응답 없음", result_data={}
            )
        },
    )
    skill.send_user_message = lambda _message: None
    skill._collect_places = lambda _scope, _target, max_places=0: (
        [{"name": ROOM, "position": {"x": 1.0, "y": 2.0}}],
        None,
    )
    skill._visit_place = lambda _place: (True, "도착했습니다.")
    skill._analyze_current_scene = lambda _place: {
        "success": True,
        "analysis": "거실 바닥에 포장지가 보입니다.",
        "detected_objects": [{"name": "포장지"}],
    }
    skill._cloid_indicator_enabled = lambda: False

    skill.execute({"scope": "room", "target_location": ROOM, "wait_for_completion": True})

    finished = next(
        event
        for event in journal.snapshot()["events"]
        if event["event_type"] == EVENT_CLEANUP_DELEGATE_FINISHED
    )
    assert finished["details"]["success"] is False
    assert finished["details"]["observed"] is False
    assert finished["details"]["failure_reason"] == "result_unconfirmed"
    activity = journal.snapshot()["activity"]
    assert activity["task_status"] == "unknown"
    assert activity["failure_reason"] == "result_unconfirmed"
    assert activity["physical_cleanup_confirmed"] is False
