"""Verify real cleanup execution observations reach dashboard and JS without hardware."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.autonomous_skill.globals import release_autonomous
from robo_claw_agent.skills.cleanup.demo_skill import CleanupDemoSkill
from robo_claw_agent.skills.cooperation_skill.journal import (
    configure_cooperation_journal,
    reset_cooperation_journal,
)
from robo_claw_agent.skills.tidy_home_skill import TidyHomeSkill
from robo_claw_dashboard.application.service import DashboardService

from .test_cooperation_integration import JournalBackend

pytestmark = pytest.mark.integration
ASSETS = Path(__file__).resolve().parents[1] / "web" / "assets"


@pytest.fixture(autouse=True)
def isolated_observations():
    release_autonomous()
    reset_cooperation_journal()
    yield
    release_autonomous()
    reset_cooperation_journal()


def test_former_room_cleanup_updates_dashboard_while_navigation_is_waiting(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id="former0047", path=None)
    skill = TidyHomeSkill()
    skill.set_node(
        SimpleNamespace(_agent_id="former0047", _skills=SimpleNamespace(has_skill=lambda _: False))
    )
    skill.send_user_message = lambda _: None
    monkeypatch.setattr(
        skill, "_collect_places", lambda *args, **kwargs: ([{"name": "거실"}], None)
    )
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original_run = skill._run_cleanup

    def blocked_navigation(place):
        entered.set()
        assert release.wait(timeout=5)
        return False, "fake navigation rejected"

    def tracked_run(*args, **kwargs):
        try:
            return original_run(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(skill, "_visit_place", blocked_navigation)
    monkeypatch.setattr(skill, "_run_cleanup", tracked_run)
    try:
        result = skill.execute({"scope": "room", "target_location": "거실"})
        assert result["success"] is True
        assert entered.wait(timeout=2)
        service = DashboardService(JournalBackend(journal))
        payload = service.status()["state"]["cooperation"]
        assert payload["events"], "plain tidy_home currently has no dashboard events"
        assert payload["activity_source"] == "tidy_home"
        assert payload["active"] is True
        assert "거실" in payload["goal"]
        assert any(event["event_type"] == "cleanup_phase" for event in payload["events"])
        assert payload["local_robot_id"] == "former0047"
    finally:
        release.set()
        assert finished.wait(timeout=3)
    final = service.status()["state"]["cooperation"]
    assert final["active"] is False
    assert any(event["event_type"] == "cleanup_run_finished" for event in final["events"])
    assert any(task["status"] in {"failed", "partial", "unknown"} for task in final["tasks"])


class DemoSkills:
    def __init__(self):
        self.calls = []

    def has_skill(self, name):
        return name in {"list_cloid_motions", "execute_cloid_motion"}

    def execute(self, name, params, **kwargs):
        self.calls.append((name, params, kwargs))
        if name == "list_cloid_motions":
            return SimpleNamespace(
                success=True,
                message="fake catalog",
                result_data={
                    "motions": [
                        {
                            "motion_id": 77,
                            "motion_name": "cleanup_demo",
                            "pre_id": None,
                            "intent_id": "cleanup_demo",
                            "description": "approved fake demo",
                            "mask": "WH",
                        }
                    ]
                },
            )
        return SimpleNamespace(success=True, message="fake motion request accepted")


def test_duplicate_demo_request_does_not_overwrite_active_observation(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id="cloid01", path=None)
    skills = DemoSkills()
    skill = CleanupDemoSkill()
    skill.set_node(
        SimpleNamespace(
            _agent_id="cloid01",
            _skills=skills,
            _memory=object(),
            get_parameter=lambda _: SimpleNamespace(value="[77]"),
        )
    )
    skill.send_user_message = lambda _: None
    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda *args, **kwargs: ([{"name": "거실"}], None)
    )
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    def navigate(*args):
        entered.set()
        assert release.wait(timeout=5)
        return True, "fake arrival"

    monkeypatch.setattr(TidyHomeSkill, "_visit_place", navigate)
    monkeypatch.setattr(
        TidyHomeSkill,
        "_analyze_current_scene",
        lambda *args: {
            "success": True,
            "analysis": "거실 바닥에 포장지가 있습니다.",
            "detected_objects": [{"name": "포장지"}],
        },
    )
    params = {
        "target_location": "거실",
        "target_object": "포장지",
        "scene_summary": "거실의 포장지 확인",
        "request_id": "same-demo-request",
    }
    results = []

    def first_request():
        try:
            results.append(skill.execute(params))
        finally:
            finished.set()

    thread = threading.Thread(target=first_request)
    thread.start()
    try:
        assert entered.wait(timeout=2)
        before = journal.snapshot()["activity"]
        repeated = skill.execute(params)
        assert repeated["success"] is False
        assert repeated["failure_reason"] == "autonomous_busy"
        after = journal.snapshot()["activity"]
        assert after["active"] is True
        for key in ["phase", "goal", "session_id", "task_status"]:
            assert after[key] == before[key]
    finally:
        release.set()
        assert finished.wait(timeout=3)
        thread.join(timeout=1)
    assert results[0]["status"] == "motion_requested"
    assert len([call for call in skills.calls if call[0] == "execute_cloid_motion"]) == 1


def test_cloid_demo_motion_request_reaches_dashboard_without_becoming_cleanup_success(monkeypatch):
    journal = configure_cooperation_journal(local_robot_id="cloid01", path=None)
    skills = DemoSkills()
    skill = CleanupDemoSkill()
    skill.set_node(
        SimpleNamespace(
            _agent_id="cloid01",
            _skills=skills,
            _memory=object(),
            get_parameter=lambda _: SimpleNamespace(value="[77]"),
        )
    )
    skill.send_user_message = lambda _: None
    monkeypatch.setattr(
        TidyHomeSkill, "_collect_places", lambda *args, **kwargs: ([{"name": "거실"}], None)
    )
    monkeypatch.setattr(TidyHomeSkill, "_visit_place", lambda *args: (True, "fake arrival"))
    monkeypatch.setattr(
        TidyHomeSkill,
        "_analyze_current_scene",
        lambda *args: {
            "success": True,
            "analysis": "거실 바닥에 포장지가 있습니다.",
            "detected_objects": [{"name": "포장지"}],
        },
    )
    result = skill.execute(
        {
            "target_location": "거실",
            "target_object": "포장지",
            "scene_summary": "거실의 포장지 확인",
            "request_id": "demo-req-47-01",
        }
    )
    assert result["success"] is True
    assert result["status"] == "motion_requested"
    assert result["physical_cleanup_confirmed"] is False
    assert result["motion_completion_confirmed"] is False
    assert [call[0] for call in skills.calls] == ["list_cloid_motions", "execute_cloid_motion"]
    assert skills.calls[1][1]["motion_id"] == 77
    assert skills.calls[1][2]["timeout_sec"] == 60.0
    service = DashboardService(JournalBackend(journal))
    payload = service.status()["state"]["cooperation"]
    assert payload["activity_source"] == "cleanup_demo"
    assert payload["local_robot_id"] == "cloid01"
    assert payload["active"] is False
    requests = [
        event
        for event in payload["events"]
        if event["event_type"] == "cleanup_demo_motion_requested"
    ]
    assert len(requests) == 1
    assert requests[0]["status"] == "motion_requested"
    assert any(task["status"] == "motion_requested" for task in payload["tasks"])
    _, exported = service.cooperation_export()
    assert "cleanup_demo_motion_requested" in exported
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to validate the actual browser reducer")
    script = """
const fs = require('fs');
const coop = require(process.argv[1]);
const state = coop.reduceSnapshot(null, JSON.parse(fs.readFileSync(0, 'utf8')), 1000);
console.log(JSON.stringify({
  events: state.events.map(event => ({type:event.eventType, status:event.status})),
  tasks: state.tasks.map(task => task.status),
  label: coop.formatStatusLabel('motion_requested'),
}));
"""
    browser = subprocess.run(
        [node, "-e", script, str(ASSETS / "cooperation.js")],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
    )
    parsed = json.loads(browser.stdout)
    assert "motion_requested" in parsed["tasks"]
    assert "미확인" in parsed["label"]
    assert any(
        event["type"] == "cleanup_demo_motion_requested" and event["status"] == "motion_requested"
        for event in parsed["events"]
    )
