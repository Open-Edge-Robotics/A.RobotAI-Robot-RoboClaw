"""Maestro 관제 중 자율협동 차단 회귀 테스트 (ROS2 불필요).

AGENTS.md §8 은 peer task delegation 을 안전 관련 변경 대상으로 규정한다.
관제(Maestro)가 이 로봇의 명령 단일 경로가 되면 자율협동 루프와 원격 위임은
중단되어야 한다. 시작 시점 검사만으로는 ``MAESTRO_ENABLED`` 가 런타임에 켜진
경우 이미 돌고 있는 루프를 막지 못하므로, 루프 사이클과 위임 진입점에서
다시 검사한다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from robo_claw_agent.skills.cooperation_skill import cooperate_autonomous as ca

pytestmark = pytest.mark.unit


class _RecordingJournal:
    """저널 호출만 기록하는 최소 대역."""

    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []

    def start_session(self, **kwargs: Any) -> None:
        self.sessions.append({"start": kwargs})

    def stop_session(self, **kwargs: Any) -> None:
        self.sessions.append({"stop": kwargs})

    def upsert_task(self, *args: Any, **kwargs: Any) -> None:
        self.sessions.append({"task": kwargs})


@pytest.fixture
def skill(monkeypatch):
    instance = ca.AutonomousCooperateSkill()
    instance.set_node(SimpleNamespace())
    instance._inbox = None
    journal = _RecordingJournal()
    monkeypatch.setattr(instance, "_journal", lambda: journal)
    instance._test_journal = journal
    return instance


@pytest.fixture(autouse=True)
def _no_real_cooperation_state(monkeypatch):
    """전역 협동 상태를 건드리지 않도록 최소 대역으로 바꾼다."""
    monkeypatch.setattr(ca, "claim_cooperation", lambda: True)
    monkeypatch.setattr(ca, "release_cooperation", lambda wake=True: None)
    monkeypatch.setattr(ca, "set_cooperation_thread", lambda thread: None)
    monkeypatch.setattr(ca, "clear_cooperation_thread_if_current", lambda: None)
    monkeypatch.setattr(ca, "is_cooperation_active", lambda: True)
    monkeypatch.setattr(ca, "maestro_control_active", lambda: True, raising=False)


def test_loop_stops_when_maestro_takes_control(monkeypatch):
    """루프는 다음 사이클에서 Maestro 관제를 확인하고 스스로 종료한다."""
    calls = {"n": 0}

    def _maestro() -> bool:
        calls["n"] += 1
        # 첫 사이클은 통과시키고 두 번째 사이클에서 관제가 켜진 상황을 재현한다.
        return calls["n"] > 1

    monkeypatch.setattr(ca, "maestro_control_active", _maestro)

    instance = ca.AutonomousCooperateSkill()
    instance.set_node(SimpleNamespace())
    instance._inbox = None
    journal = _RecordingJournal()
    monkeypatch.setattr(instance, "_journal", lambda: journal)

    messages: list[str] = []
    monkeypatch.setattr(instance, "send_user_message", lambda msg: messages.append(msg))
    monkeypatch.setattr(instance, "_proactive_check", lambda: [])

    instance._run_loop(cycle_interval=0.01, proactive_interval=3600.0, max_cycles=10)

    assert calls["n"] >= 2
    assert any("Maestro" in msg for msg in messages)
    assert journal.sessions[-1]["stop"]["reason"] == "maestro_control_active"


def test_loop_does_not_start_cycle_work_while_maestro_is_active(monkeypatch):
    """관제가 이미 켜져 있으면 첫 사이클부터 아무 작업도 하지 않는다."""
    proactive_calls: list[int] = []

    instance = ca.AutonomousCooperateSkill()
    instance.set_node(SimpleNamespace())
    instance._inbox = None
    journal = _RecordingJournal()
    monkeypatch.setattr(instance, "_journal", lambda: journal)
    monkeypatch.setattr(instance, "send_user_message", lambda msg: None)
    monkeypatch.setattr(instance, "_proactive_check", lambda: proactive_calls.append(1))

    instance._run_loop(cycle_interval=0.01, proactive_interval=1.0, max_cycles=10)

    assert proactive_calls == []
    assert journal.sessions[-1]["stop"]["reason"] == "maestro_control_active"


def test_send_to_peer_is_blocked_while_maestro_is_active(monkeypatch):
    """관제 중에는 원격 위임 메시지를 보내지 않는다."""
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ca, "send_cooperation_message", lambda **kwargs: sent.append(kwargs) or {"ok": True}
    )

    instance = ca.AutonomousCooperateSkill()
    instance.set_node(SimpleNamespace())
    instance._inbox = None
    monkeypatch.setattr(instance, "_journal", lambda: _RecordingJournal())

    result = instance._send_to_peer("robot_b", "도와줘")

    assert result["success"] is False
    assert result.get("failure_reason") == "maestro_control_active"
    assert sent == []


def test_execute_task_is_blocked_while_maestro_is_active(monkeypatch):
    """관제 중에는 동료가 위임한 작업을 실행하지 않는다."""
    executed: list[dict[str, Any]] = []
    monkeypatch.setattr(
        ca, "execute_remote_task", lambda **kwargs: executed.append(kwargs) or {"success": True}
    )

    instance = ca.AutonomousCooperateSkill()
    instance.set_node(SimpleNamespace())
    instance._inbox = None
    monkeypatch.setattr(instance, "_journal", lambda: _RecordingJournal())

    result = instance._execute_task("문 열어줘")

    assert result["success"] is False
    assert result.get("failure_reason") == "maestro_control_active"
    assert executed == []


def test_loop_runs_normally_without_maestro(monkeypatch):
    """관제가 꺼져 있으면 기존 동작(사이클 점검)이 유지된다."""
    monkeypatch.setattr(ca, "maestro_control_active", lambda: False)
    proactive_calls: list[int] = []
    stop = {"flag": False}

    instance = ca.AutonomousCooperateSkill()
    instance.set_node(SimpleNamespace())
    instance._inbox = None
    journal = _RecordingJournal()
    monkeypatch.setattr(instance, "_journal", lambda: journal)
    monkeypatch.setattr(instance, "send_user_message", lambda msg: None)

    def _proactive() -> list:
        proactive_calls.append(1)
        stop["flag"] = True
        return []

    monkeypatch.setattr(instance, "_proactive_check", _proactive)
    monkeypatch.setattr(ca, "is_cooperation_active", lambda: not stop["flag"])

    instance._run_loop(cycle_interval=0.01, proactive_interval=1.0, max_cycles=0)

    assert proactive_calls
    assert journal.sessions[-1]["stop"]["reason"] == "cooperation_loop_ended"
