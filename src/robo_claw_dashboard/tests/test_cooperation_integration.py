"""Exercise real agent journals through dashboard HTTP and browser reducers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from robo_claw_agent.skills.cooperation_skill.inbox import PeerMessageInbox
from robo_claw_agent.skills.cooperation_skill.journal import CooperationJournal
from robo_claw_agent.skills.cooperation_skill.receive import receive_peer_message
from robo_claw_agent.skills.cooperation_skill.remote_task import execute_remote_task
from robo_claw_dashboard.application.http_router import route_http
from robo_claw_dashboard.application.service import DashboardService

pytestmark = pytest.mark.integration
ASSETS = Path(__file__).resolve().parents[1] / "web" / "assets"


class JournalBackend:
    def __init__(self, journal: CooperationJournal) -> None:
        self.journal = journal
        self.calls = 0

    def query_state(self) -> str:
        self.calls += 1
        return json.dumps(
            {"agent_id": self.journal.local_robot_id, "cooperation": self.journal.snapshot()}
        )

    def latest_agent_status(self) -> dict:
        return {}

    def latest_telemetry(self) -> dict:
        return {}

    def latest_sensor_health(self) -> dict:
        return {}

    def list_peers(self) -> list:
        return []


@pytest.fixture
def journal():
    instance = CooperationJournal(local_robot_id="robot-a")
    yield instance
    instance.close()


def test_actual_journal_pages_without_skipping_events(journal):
    for number in range(5):
        journal.record("message_queued", peer_id="robot-b", content=f"message {number}")
    backend = JournalBackend(journal)
    service = DashboardService(backend)
    service.status()
    sequences = []
    cursor = 0
    for _ in range(3):
        response = route_http(
            "GET",
            f"/api/cooperation?after={cursor}&limit=2",
            static_root=ASSETS.parent,
            service=service,
        )
        assert response.status == 200
        payload = json.loads(response.body)
        sequences.extend(item["sequence"] for item in payload["events"])
        cursor = payload["cursor"]
        assert payload["gap"] is None
    assert sequences == [1, 2, 3, 4, 5]
    assert backend.calls == 1


def test_real_receiver_execution_keeps_peer_session_and_policy():
    receiver = CooperationJournal(local_robot_id="robot-b")
    receiver.start_session(goal="inspect", session_id="local-b-session")
    try:
        outcome = receive_peer_message(
            peer_name="robot-a",
            message="inspect the room",
            message_id="request-a",
            task_id="task-a",
            session_id="shared-a-session",
            inbox=PeerMessageInbox(),
            journal=receiver,
        )
        assert outcome.accepted is True
        calls = []
        manager = SimpleNamespace(
            get_skill=lambda name: SimpleNamespace(risk_level="read"),
            execute=lambda name, params, **kwargs: (
                calls.append((name, params, kwargs))
                or SimpleNamespace(success=True, message="room inspected")
            ),
        )
        result = execute_remote_task(
            node=SimpleNamespace(_skills=manager),
            instruction="inspect the room",
            skill_name="analyze_scene",
            skill_params={"label": "room", "auth_token": "fake-sensitive-value"},
            allow_remote_execution=True,
            allowed_skills=["analyze_scene"],
            journal=receiver,
            task_id="task-a",
            message_id="request-a",
            peer_id=outcome.inbox_item.peer_name,
            session_id=outcome.inbox_item.session_id,
        )
        assert result["success"] is True
        assert len(calls) == 1
        backend = JournalBackend(receiver)
        service = DashboardService(backend)
        snapshot = service.status()["state"]["cooperation"]
        execution = [
            event
            for event in snapshot["events"]
            if event["event_type"] in {"policy", "task_started", "task_completed"}
        ]
        assert len(execution) == 3
        assert all(event["peer_id"] == "robot-a" for event in execution)
        assert all(event["session_id"] == "shared-a-session" for event in execution)
        assert all(event["task_id"] == "task-a" for event in execution)
        assert snapshot["tasks"][0]["status"] == "completed"
        assert "fake-sensitive-value" not in json.dumps(snapshot)
        _, exported = service.cooperation_export()
        assert "fake-sensitive-value" not in exported
        assert len([json.loads(line) for line in exported.splitlines()]) >= 4
    finally:
        receiver.close()


def test_sender_queued_does_not_become_remote_success_in_browser(journal):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to execute the browser reducer")
    journal.start_session(goal="demo", session_id="session-a")
    journal.record(
        "message_queued",
        peer_id="robot-b",
        task_id="task-1",
        message_id="message-1",
        status="queued",
        content="please inspect",
    )
    journal.upsert_task(
        "task-1",
        peer_id="robot-b",
        session_id="session-a",
        description="please inspect",
        status="queued",
    )
    service = DashboardService(JournalBackend(journal))
    payload = service.status()["state"]["cooperation"]
    script = """
const fs = require('fs');
const coop = require(process.argv[1]);
const raw = JSON.parse(fs.readFileSync(0, 'utf8'));
const state = coop.reduceSnapshot(null, raw, 1000);
console.log(JSON.stringify({
  task: state.tasks[0].status,
  label: coop.formatStatusLabel(state.tasks[0].status),
  types: state.events.map(event => event.eventType),
  active: state.active,
}));
"""
    completed = subprocess.run(
        [node, "-e", script, str(ASSETS / "cooperation.js")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    browser = json.loads(completed.stdout)
    assert browser["task"] == "queued"
    assert "미확인" in browser["label"]
    assert "task_completed" not in browser["types"]
    assert browser["active"] is True


def test_actual_disabled_journal_is_not_active_in_dashboard(journal):
    journal.start_session(goal="inspect")
    backend = JournalBackend(journal)
    backend.query_state = lambda: json.dumps({"cooperation": journal.snapshot(enabled=False)})
    payload = DashboardService(backend).status()["state"]["cooperation"]
    assert payload["enabled"] is False
    assert payload["active"] is False
